from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch, MagicMock
from urllib.robotparser import RobotFileParser
from datetime import UTC, datetime, timedelta

from src.db import get_connection, upsert_source
from src.services.layered_collection import (
    _source_cycle,
    initialize_frontier,
    export_frontier,
    import_frontier,
    _http_get,
)

ARTICLE = (
    "<html><title>Nike launches a new Olympic campaign</title><article><h1>Nike launches a new Olympic campaign</h1><p>"
    + (
        "Nike announced its Olympic advertising campaign with athletes, a television film and an outdoor creative strategy. "
        * 12
    )
    + "</p></article></html>"
)


class LayeredCollectionTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.database = Path(self.directory.name) / "collection.db"
        initialize_frontier(self.database)
        with closing(get_connection(self.database)) as connection:
            upsert_source(
                connection,
                dict(
                    source_name="Example",
                    website_url="https://example.com",
                    category="Marketing",
                    country="Global",
                    credibility_tier=1,
                    active_status=1,
                ),
            )
            connection.commit()
            self.source = dict(connection.execute("SELECT * FROM sources").fetchone())

    def tearDown(self):
        self.directory.cleanup()

    def getter(self, pages):
        self.requested = []

        def get(session, url, source, deadline):
            self.requested.append(url)
            code, body = pages.get(url, (404, ""))
            return url, code, body

        return get

    def cycle(self, pages, run="run_one", limit=30, renderer=None):
        with patch("src.services.layered_collection.time.sleep"):
            return _source_cycle(
                self.database,
                self.source,
                run,
                20,
                limit,
                http_get=self.getter(pages),
                renderer=renderer,
            )

    def test_feed_sitemap_and_deep_pagination_resume(self):
        pages = {
            "https://example.com/robots.txt": (
                200,
                "User-agent: *\nAllow: /\nSitemap: https://example.com/news-map.xml",
            ),
            "https://example.com/": (200, '<a href="/news/">News section</a>'),
            "https://example.com/feed": (
                200,
                "<rss><channel><item><link>https://example.com/news/nike-launches-new-olympic-campaign</link></item></channel></rss>",
            ),
            "https://example.com/news-map.xml": (
                200,
                "<urlset><url><loc>https://example.com/news/nike-announces-new-film-campaign</loc></url></urlset>",
            ),
            "https://example.com/news": (200, '<link rel="next" href="/news/page/2">'),
            "https://example.com/news/page/6": (
                200,
                '<a href="/news/nike-launches-an-outdoor-campaign">Nike launches an outdoor campaign</a>',
            ),
        }
        for page in range(2, 6):
            pages[f"https://example.com/news/page/{page}"] = (
                200,
                f'<a rel="next" href="/news/page/{page+1}">Next</a>',
            )
        for slug in (
            "nike-launches-new-olympic-campaign",
            "nike-announces-new-film-campaign",
            "nike-launches-an-outdoor-campaign",
        ):
            pages["https://example.com/news/" + slug] = (200, ARTICLE)
        first = self.cycle(pages, limit=1)
        self.assertTrue(first["budget_reached"])
        self.assertGreater(first["queued"], 0)
        second = self.cycle(
            pages,
            run="run_two",
            renderer=lambda *args: (args[0], 200, "<p>Navigation</p>"),
        )
        self.assertEqual(second["articles_read"], 3)
        self.assertIn("feed", second["discovery_methods"])
        self.assertIn("sitemap", second["discovery_methods"])
        self.assertIn("https://example.com/news/page/6", self.requested)
        self.assertEqual(second["completeness"], "unknown")

    def test_robots_and_access_blocks_are_not_bypassed(self):
        report = self.cycle(
            {
                "https://example.com/robots.txt": (
                    200,
                    "User-agent: *\nDisallow: /news/private-campaign",
                ),
                "https://example.com/": (
                    200,
                    '<a href="/news/private-campaign">Private campaign title</a><a href="/news/blocked-campaign">Blocked campaign title</a>',
                ),
                "https://example.com/news/blocked-campaign": (403, "Access denied"),
            },
            renderer=lambda *args: self.fail("No block bypass"),
        )
        self.assertEqual(report["robots_disallowed"], 1)
        self.assertEqual(report["blocked"], 1)
        self.assertNotIn("https://example.com/news/private-campaign", self.requested)

    def test_javascript_article_uses_browser(self):
        report = self.cycle(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/": (
                    200,
                    '<a href="/news/campaign-built-in-javascript">Campaign built in JavaScript</a>',
                ),
                "https://example.com/news/campaign-built-in-javascript": (
                    200,
                    "<html><title>Loading</title></html>",
                ),
            },
            renderer=lambda url, *args: (url, 200, ARTICLE),
        )
        self.assertEqual(report["articles_read"], 1)
        self.assertEqual(report["browser_fallbacks"], 1)
        with closing(get_connection(self.database)) as connection:
            self.assertEqual(
                connection.execute("SELECT extraction_method FROM articles").fetchone()[
                    0
                ],
                "browser",
            )

    def test_external_domains_and_paywalls_stay_unread(self):
        report = self.cycle(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/": (
                    200,
                    '<a href="https://evil.invalid/news/a-campaign-story">External story</a><a href="/news/subscriber-campaign">Subscriber campaign story</a>',
                ),
                "https://example.com/news/subscriber-campaign": (
                    200,
                    '<script type="application/ld+json">{"isAccessibleForFree":false}</script>'
                    + ARTICLE,
                ),
            },
            renderer=lambda *args: self.fail("No paywall bypass"),
        )
        self.assertEqual(report["articles_read"], 0)
        self.assertEqual(report["blocked"], 1)
        self.assertFalse(any("evil.invalid" in url for url in self.requested))

    def test_shared_checkpoint_restores_queue_and_overwrites_stale_host(self):
        self.cycle(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/": (
                    200,
                    '<a href="/news/nike-new-campaign">Nike launches campaign</a>',
                ),
            },
            limit=1,
        )
        checkpoint = export_frontier(self.database)
        root = next(
            row
            for row in checkpoint["sources"][0]["rows"]
            if row["url"] == "https://example.com/"
        )
        root.update(status="retry", attempts=2)
        import_frontier(self.database, checkpoint)
        with closing(get_connection(self.database)) as connection:
            self.assertEqual(
                tuple(
                    connection.execute(
                        "SELECT status,attempts FROM collection_frontier WHERE url=?",
                        ("https://example.com/",),
                    ).fetchone()
                ),
                ("retry", 2),
            )
        other = Path(self.directory.name) / "restored.db"
        initialize_frontier(other)
        with closing(get_connection(other)) as connection:
            upsert_source(
                connection,
                dict(
                    source_name="Example",
                    website_url="https://example.com",
                    category="Marketing",
                    country="Global",
                    credibility_tier=1,
                    active_status=1,
                ),
            )
            connection.commit()
        import_frontier(other, checkpoint)
        with closing(get_connection(other)) as connection:
            self.assertGreater(
                connection.execute(
                    "SELECT COUNT(*) FROM collection_frontier WHERE status='queued'"
                ).fetchone()[0],
                0,
            )
        self.assertGreater(
            export_frontier(self.database, max_rows_per_source=1)["overflow_urls"], 0
        )

    def test_redirect_robots_rules_apply_before_request(self):
        robots = RobotFileParser()
        robots.parse(["User-agent: *", "Disallow: /private"])
        response = MagicMock()
        response.is_redirect = True
        response.headers = {"Location": "/private/story"}
        response.__enter__.return_value = response
        session = MagicMock()
        session.get.return_value = response
        with self.assertRaisesRegex(PermissionError, "robots_disallowed"):
            _http_get(
                session,
                "https://example.com/news/redirect-story",
                dict(self.source, _robots_policy=robots),
                float("inf"),
            )
        session.get.assert_called_once()

    def test_collection_pages_are_not_articles(self):
        report = self.cycle(
            {
                "https://example.com/robots.txt": (404, ""),
                "https://example.com/": (
                    200,
                    '<a href="/news/marketing-campaign-library">Marketing campaign library collection</a>',
                ),
                "https://example.com/news/marketing-campaign-library": (
                    200,
                    '<script type="application/ld+json">{"@type":["WebPage","CollectionPage"]}</script>'
                    + ARTICLE,
                ),
            }
        )
        self.assertEqual(report["articles_read"], 0)

    def test_transient_failures_back_off_then_recover_without_manual_reset(self):
        article_url = "https://example.com/news/temporarily-unavailable-campaign"
        pages = {
            "https://example.com/robots.txt": (404, ""),
            "https://example.com/": (
                200,
                f'<a href="{article_url}">Temporarily unavailable campaign story</a>',
            ),
            article_url: (503, "Try later"),
        }
        self.cycle(pages)
        with closing(get_connection(self.database)) as connection:
            connection.execute(
                "UPDATE collection_frontier SET attempts=2,next_attempt_at=? WHERE url=?",
                ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), article_url),
            )
            connection.commit()
        self.cycle(pages, run="third_attempt")
        with closing(get_connection(self.database)) as connection:
            row = connection.execute(
                "SELECT status,next_attempt_at FROM collection_frontier WHERE url=?",
                (article_url,),
            ).fetchone()
            self.assertEqual(row["status"], "retry")
            self.assertGreater(
                datetime.fromisoformat(row["next_attempt_at"]),
                datetime.now(UTC) + timedelta(hours=20),
            )
        self.cycle(pages, run="too_soon")
        self.assertNotIn(article_url, self.requested)
        with closing(get_connection(self.database)) as connection:
            connection.execute(
                "UPDATE collection_frontier SET next_attempt_at=? WHERE url=?",
                ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), article_url),
            )
            connection.commit()
        pages[article_url] = (200, ARTICLE)
        recovered = self.cycle(pages, run="provider_recovered")
        self.assertEqual(recovered["articles_read"], 1)

    def test_section_scope_excludes_global_navigation_and_recommendations(self):
        self.source["website_url"] = "https://example.com/v/advertising"
        story = "https://example.com/2026/marketing/nike-launches-olympic-campaign"
        unrelated = "https://example.com/2026/film/unrelated-movie-premiere"
        report = self.cycle(
            {
                "https://example.com/robots.txt": (404, ""),
                self.source["website_url"]: (
                    200,
                    f"""<header><a href="{unrelated}">Global film navigation story</a></header>
                <main><nav><a href="{unrelated}">Global film navigation story</a></nav>
                <section><a href="{story}">Nike launches Olympic advertising campaign</a></section>
                <aside><a href="{unrelated}">Sidebar entertainment story</a></aside>
                <div class="related-stories"><a href="{unrelated}">Related entertainment story</a></div>
                <div itemprop="articleBody"><a href="{unrelated}">An article body crosslink</a></div>
                <footer><a href="{unrelated}">Footer entertainment story</a></footer></main>""",
                ),
                story: (200, ARTICLE),
                unrelated: (200, ARTICLE),
            }
        )
        self.assertEqual(report["articles_read"], 1)
        self.assertIn(story, self.requested)
        self.assertNotIn(unrelated, self.requested)

    def test_section_without_identifiable_content_reports_adapter_requirement(self):
        self.source["website_url"] = "https://example.com/v/advertising"
        unrelated = "https://example.com/2026/film/unrelated-movie-premiere"
        report = self.cycle(
            {
                "https://example.com/robots.txt": (404, ""),
                self.source["website_url"]: (
                    200,
                    f'<div><a href="{unrelated}">A global entertainment story</a></div>',
                ),
                unrelated: (200, ARTICLE),
            }
        )
        self.assertEqual(report["articles_read"], 0)
        self.assertNotIn(unrelated, self.requested)
        self.assertTrue(
            any(
                isinstance(issue, dict)
                and issue.get("outcome") == "section_scope_requires_adapter"
                for issue in report["issues"]
            )
        )
