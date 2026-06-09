import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.article_fetcher import build_feed_candidates, dedupe_candidates
from src.article_parser import (
    discover_feed_links,
    extract_article_link_candidates,
    is_generic_headline,
    is_likely_article_url,
    parse_article_html,
)


class ArticleParserTest(unittest.TestCase):
    def test_parse_article_html_extracts_metadata(self) -> None:
        html = """
        <html>
            <head>
                <title>Nike launches AI-powered Olympics campaign</title>
                <meta name="description" content="Nike launches a new AI campaign.">
                <meta name="author" content="Jane Doe">
                <meta property="article:published_time" content="2026-06-01">
            </head>
            <body>
                <article>
                    <p>Nike launched an AI-powered Olympics campaign with a new global film.</p>
                    <p>The campaign uses athlete data and generative tools across social channels.</p>
                </article>
            </body>
        </html>
        """

        parsed = parse_article_html("https://example.com/news/nike-ai-campaign", html)

        self.assertEqual(parsed.headline, "Nike launches AI-powered Olympics campaign")
        self.assertEqual(parsed.author, "Jane Doe")
        self.assertEqual(parsed.published_date, "2026-06-01")
        self.assertIn("Nike launched", parsed.article_text)

    def test_parse_article_html_uses_structured_article_data(self) -> None:
        html = """
        <html>
            <head>
                <script type="application/ld+json">
                {
                    "@type": "NewsArticle",
                    "headline": "Duolingo English Test appoints Divya Rao as Marketing Lead",
                    "description": "Duolingo English Test has appointed Divya Rao.",
                    "datePublished": "2026-06-05",
                    "author": {"name": "Editorial Desk"},
                    "articleBody": "Duolingo English Test appointed Divya Rao as marketing lead for India."
                }
                </script>
            </head>
            <body><div id="root"></div></body>
        </html>
        """

        parsed = parse_article_html("https://example.com/news/duolingo-divya-rao", html)

        self.assertEqual(parsed.headline, "Duolingo English Test appoints Divya Rao as Marketing Lead")
        self.assertEqual(parsed.author, "Editorial Desk")
        self.assertEqual(parsed.published_date, "2026-06-05")
        self.assertIn("appointed Divya Rao", parsed.article_text)

    def test_likely_article_url(self) -> None:
        self.assertTrue(is_likely_article_url("https://example.com/2026/06/nike-launches-ai-campaign"))
        self.assertFalse(is_likely_article_url("https://example.com/tag/advertising"))

    def test_likely_article_url_rejects_generic_homepage_links(self) -> None:
        self.assertFalse(
            is_likely_article_url(
                "https://example.com/resources",
                "Marketing Whitepapers and Resources",
                source_url="https://example.com",
            )
        )

    def test_likely_article_url_accepts_www_variant_of_source_domain(self) -> None:
        self.assertTrue(
            is_likely_article_url(
                "https://www.adweek.com/creativity/7-new-things-at-cannes-lions-2026",
                "7 New Things at Cannes Lions 2026",
                source_url="https://adweek.com",
            )
        )

    def test_likely_article_url_accepts_slug_followed_by_numeric_id(self) -> None:
        self.assertTrue(
            is_likely_article_url(
                "https://www.campaignlive.com/article/sport-beach-return-cannes-2026-new-strategic-partner-boardroom/1954843",
                "Sport Beach to return to Cannes 2026 with new strategic partner Boardroom",
                source_url="https://campaignlive.com",
            )
        )

    def test_generic_headline_detection(self) -> None:
        self.assertTrue(is_generic_headline("The leading source of advertising, marketing & media news"))
        self.assertFalse(is_generic_headline("Nike launches AI-powered Olympic campaign"))

    def test_discover_feed_links_from_homepage(self) -> None:
        html = '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
        self.assertEqual(discover_feed_links("https://example.com", html), ["https://example.com/feed.xml"])

    def test_article_link_candidates_include_embedded_js_urls(self) -> None:
        html = """
        <script>
            window.__DATA__ = {
                "url": "https:\\/\\/example.com\\/news\\/coca-cola-media-review-wpp-publicis"
            };
        </script>
        """

        candidates = extract_article_link_candidates("https://example.com", html, limit=None)

        self.assertEqual(
            candidates[0]["article_url"],
            "https://example.com/news/coca-cola-media-review-wpp-publicis",
        )

    def test_build_feed_candidates_prefers_source_rss_url(self) -> None:
        candidates = build_feed_candidates(
            {
                "website_url": "https://example.com/news",
                "rss_url": "https://example.com/custom-feed.xml",
            }
        )

        self.assertEqual(candidates[0], "https://example.com/custom-feed.xml")
        self.assertIn("https://example.com/feed", candidates)

    def test_dedupe_candidates(self) -> None:
        deduped = dedupe_candidates(
            [
                {"article_url": "https://example.com/news/1", "headline": "One"},
                {"article_url": "https://example.com/news/1/", "headline": "One again"},
            ]
        )

        self.assertEqual(len(deduped), 1)
