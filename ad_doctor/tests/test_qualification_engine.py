import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.db import (
    get_article_id_by_url,
    get_connection,
    initialize_database,
    link_topic_source,
    list_topic_scores,
    upsert_article,
    upsert_source,
    upsert_topic,
)
from src.qualification_engine import qualify_topics


class QualificationEnginePlaceholderTest(unittest.TestCase):
    def test_phase_one_placeholder(self) -> None:
        self.assertTrue(True)

    def test_qualification_uses_exact_topic_references_not_raw_broad_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )
            with closing(get_connection(database_path)) as connection:
                source_ids = [
                    self.insert_source(connection, "Marketing Interactive", "https://marketing-interactive.com"),
                    self.insert_source(connection, "Campaign India", "https://campaignindia.in"),
                    self.insert_source(connection, "Branding in Asia", "https://brandinginasia.com"),
                ]
                topic_id = upsert_topic(
                    connection,
                    "Coca-Cola crowned eCommerce Brand of the Year at Asia eCommerce Awards 2025",
                    "coca cola crowned ecommerce brand year asia ecommerce awards 2025",
                    "Coca-Cola crowned eCommerce Brand of the Year at Asia eCommerce Awards 2025",
                )
                self.insert_article_and_link(
                    connection,
                    topic_id,
                    source_ids[0],
                    "Marketing Interactive",
                    "https://marketing-interactive.com/coca-cola-ecommerce-award",
                    "Coca-Cola crowned eCommerce Brand of the Year at Asia eCommerce Awards 2025",
                )
                self.insert_article_and_link(
                    connection,
                    topic_id,
                    source_ids[1],
                    "Campaign India",
                    "https://campaignindia.in/fcb-india-famous-innovations-mccann-awards",
                    "FCB India, Famous Innovations, McCann win big at the South Asia Agency of the Year awards",
                )
                self.insert_article_and_link(
                    connection,
                    topic_id,
                    source_ids[2],
                    "Branding in Asia",
                    "https://brandinginasia.com/about-spotlight",
                    "About Spotlight | Publishing with Branding in Asia",
                )
                connection.commit()

            summary = qualify_topics(database_path=database_path, run_id="qualification_test")

            with closing(get_connection(database_path)) as connection:
                scores = list_topic_scores(connection)

        self.assertEqual(summary.qualified_topics, 0)
        self.assertEqual(summary.rejected_topics, 1)
        self.assertEqual(scores[0]["status"], "rejected")
        self.assertEqual(scores[0]["unique_source_coverage_count"], 1)

    def insert_source(self, connection, source_name: str, website_url: str) -> int:
        upsert_source(
            connection,
            {
                "source_name": source_name,
                "website_url": website_url,
                "rss_url": None,
                "category": "Advertising",
                "country": "Global",
                "credibility_tier": 2,
                "active_status": 1,
            },
        )
        row = connection.execute("SELECT id FROM sources WHERE website_url = ?", (website_url,)).fetchone()
        return int(row["id"])

    def insert_article_and_link(
        self,
        connection,
        topic_id: int,
        source_id: int,
        source_name: str,
        article_url: str,
        headline: str,
    ) -> None:
        upsert_article(
            connection,
            {
                "source_id": source_id,
                "source_name": source_name,
                "source_url": article_url.split("/", 3)[0] + "//" + article_url.split("/", 3)[2],
                "article_url": article_url,
                "headline": headline,
                "published_date": "2026-06-08",
                "author": "News Desk",
                "meta_description": headline,
                "article_text": f"{headline}. Stored article text for scoring.",
                "fetch_run_id": "test_fetch",
            },
        )
        article_id = get_article_id_by_url(connection, article_url)
        assert article_id is not None
        link_topic_source(connection, topic_id, source_id, article_id, article_url)
