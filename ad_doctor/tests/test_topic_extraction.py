import sys
import tempfile
import unittest
from unittest import mock
from contextlib import closing
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.db import get_connection, initialize_database, list_topics, upsert_article, upsert_source
from src.topic_extractor import canonical_topic_title, clean_headline, extract_topics, is_valid_exact_topic
from src.topic_matcher import is_same_news_event, normalize_topic_text, topic_similarity
from src.topic_lock_engine import TopicLockSummary
from src.demo_runner import run_demo_pipeline


class TopicExtractionTest(unittest.TestCase):
    def test_demo_pipeline_creates_pending_drafts_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "demo.db"
            with mock.patch("requests.sessions.Session.request", side_effect=AssertionError("Demo must run offline")):
                summary = run_demo_pipeline(database_path=database_path)
            self.assertEqual(summary.demo_articles_inserted, 6)
            self.assertEqual(summary.topics_locked, 1)
            self.assertEqual(summary.blog_drafts_created, 1)
            self.assertEqual(summary.social_drafts_created, 1)
            with closing(get_connection(database_path)) as connection:
                drafts = connection.execute("SELECT approval_status FROM content_outputs").fetchall()
            self.assertEqual([row["approval_status"] for row in drafts], ["pending", "pending"])

    def test_demo_validation_requires_explicit_opt_in(self) -> None:
        article = {
            "article_url": "https://example.com/demo/nike-ai-olympics",
            "source_url": "https://example.com",
            "fetch_run_id": "demo_test",
        }
        topic = "Nike launches AI-powered Olympic campaign"
        self.assertFalse(is_valid_exact_topic(topic, article))
        self.assertTrue(is_valid_exact_topic(topic, article, include_demo_articles=True))
        self.assertFalse(is_valid_exact_topic("Homepage", article, include_demo_articles=True))

    def test_clean_headline_removes_publication_suffix(self) -> None:
        self.assertEqual(
            clean_headline("Nike launches AI-powered campaign for Olympics | Ad Age"),
            "Nike launches AI-powered campaign for Olympics",
        )

    def test_clean_headline_removes_ads_of_world_suffix(self) -> None:
        self.assertEqual(
            clean_headline("Samsung: When it matters, watch it on a Samsung • Ads of the World™ | Part of The Clio Network"),
            "Samsung: When it matters, watch it on a Samsung",
        )

    def test_topic_matcher_identifies_same_event(self) -> None:
        self.assertTrue(
            is_same_news_event(
                "Nike launches AI-powered Olympic campaign",
                "Nike unveils AI-led Olympics ad film",
            )
        )

    def test_topic_matcher_separates_different_events(self) -> None:
        self.assertFalse(
            is_same_news_event(
                "Nike launches AI campaign",
                "Nike changes media agency",
            )
        )

    def test_normalize_topic_text(self) -> None:
        self.assertEqual(
            normalize_topic_text("Nike's new Olympic campaign uses AI"),
            "nike olympic campaign use ai",
        )

    def test_topic_similarity_scores_related_topics(self) -> None:
        self.assertGreater(
            topic_similarity(
                "Nike launches AI-powered Olympic campaign",
                "Nike unveils AI-led Olympics ad film",
            ),
            0.35,
        )

    def test_topic_lock_summary_records_topic_ids(self) -> None:
        summary = TopicLockSummary(
            run_id="run_test",
            requested_limit=10,
            locked_count=2,
            topic_ids=[1, 2],
        )

        self.assertEqual(summary.locked_count, 2)
        self.assertEqual(summary.topic_ids, [1, 2])

    def test_invalid_exact_topic_rejects_generic_site_copy(self) -> None:
        self.assertFalse(
            is_valid_exact_topic(
                "The leading source of advertising, marketing & media news",
                {"article_url": "https://example.com", "source_url": "https://example.com"},
            )
        )
        self.assertFalse(
            is_valid_exact_topic(
                "Marketing Whitepapers and Resources",
                {"article_url": "https://example.com/resources", "source_url": "https://example.com"},
            )
        )
        self.assertFalse(
            is_valid_exact_topic(
                "Ampersand and Fandango launch solution today announced a new partnership with Fandango and Kochava LEARN MORE",
                {"article_url": "https://example.com/news/ampersand-fandango", "source_url": "https://example.com"},
            )
        )
        self.assertFalse(
            is_valid_exact_topic(
                "Ads of the Week: 12 Campaigns That Caught Our Eye, From Nike to McDonald’s",
                {"article_url": "https://example.com/news/ads-of-the-week", "source_url": "https://example.com"},
            )
        )
        self.assertFalse(
            is_valid_exact_topic(
                "What are Indian creators doing at the 2023 Cannes Film Festival?",
                {"article_url": "https://example.com/news/cannes-film-festival-creators", "source_url": "https://example.com"},
            )
        )

    def test_valid_exact_topic_accepts_news_event(self) -> None:
        self.assertTrue(
            is_valid_exact_topic(
                "Nike launches AI-powered Olympic campaign",
                {
                    "article_url": "https://example.com/news/nike-launches-ai-powered-olympic-campaign",
                    "source_url": "https://example.com",
                },
            )
        )

    def test_valid_exact_topic_accepts_media_account_review(self) -> None:
        self.assertTrue(
            is_valid_exact_topic(
                "Coca-Cola hands Publicis a shot at WPP's global media crown",
                {
                    "article_url": "https://example.com/news/coca-cola-publicis-wpp-global-media-account",
                    "source_url": "https://example.com",
                },
            )
        )

    def test_valid_exact_topic_accepts_event_calendar_topic(self) -> None:
        self.assertTrue(
            is_valid_exact_topic(
                "Cannes Lions 2026 schedule-events for CMOs, creatives, ad tech leaders and more",
                {
                    "article_url": "https://example.com/events/cannes-lions-2026-schedule",
                    "source_url": "https://example.com",
                },
            )
        )

    def test_canonical_topic_title_does_not_merge_cannes_side_story(self) -> None:
        self.assertIsNone(
            canonical_topic_title("Billion Dollar Boy announces content creator roster for Cannes 2026")
        )

    def test_real_topic_extraction_skips_demo_articles_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )
            with closing(get_connection(database_path)) as connection:
                upsert_source(
                    connection,
                    {
                        "source_name": "Example Ads",
                        "website_url": "https://example.com",
                        "rss_url": None,
                        "category": "Advertising",
                        "country": "Global",
                        "credibility_tier": 2,
                        "active_status": 1,
                    },
                )
                row = connection.execute(
                    "SELECT id FROM sources WHERE website_url = ?",
                    ("https://example.com",),
                ).fetchone()
                source_id = int(row["id"])
                upsert_article(
                    connection,
                    {
                        "source_id": source_id,
                        "source_name": "Example Ads",
                        "source_url": "https://example.com",
                        "article_url": "https://example.com/demo/demo_20260604/nike-ai-olympics",
                        "headline": "Nike launches AI-powered Olympic campaign",
                        "published_date": "2026-06-03",
                        "author": "Demo Desk",
                        "meta_description": "Demo coverage.",
                        "article_text": "Demo coverage only.",
                        "fetch_run_id": "demo_20260604",
                    },
                )
                connection.commit()

            summary = extract_topics(database_path=database_path, run_id="real_run")

            with closing(get_connection(database_path)) as connection:
                topics = list_topics(connection)

        self.assertEqual(summary.articles_checked, 1)
        self.assertEqual(summary.skipped_articles, 1)
        self.assertEqual(topics, [])
