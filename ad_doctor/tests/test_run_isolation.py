import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.db import (
    get_connection,
    initialize_database,
    list_articles,
    list_topics,
    upsert_article,
    upsert_source,
)
from src.topic_extractor import extract_topics


class RunIsolationTest(unittest.TestCase):
    def test_articles_can_be_listed_by_fetch_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )
            with closing(get_connection(database_path)) as connection:
                source_id = self.insert_source(connection)
                self.insert_article(connection, source_id, "run_a", "Nike launches AI-powered Olympic campaign")
                self.insert_article(connection, source_id, "run_b", "Adidas launches World Cup creator campaign")
                connection.commit()

                run_a_articles = list_articles(connection, fetch_run_id="run_a")
                all_articles = list_articles(connection)

        self.assertEqual(len(run_a_articles), 1)
        self.assertEqual(run_a_articles[0]["fetch_run_id"], "run_a")
        self.assertEqual(len(all_articles), 2)

    def test_topic_extraction_reads_only_selected_fetch_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )
            with closing(get_connection(database_path)) as connection:
                source_id = self.insert_source(connection)
                self.insert_article(connection, source_id, "run_a", "Nike launches AI-powered Olympic campaign")
                self.insert_article(connection, source_id, "run_b", "Adidas launches World Cup creator campaign")
                connection.commit()

            summary = extract_topics(
                database_path=database_path,
                run_id="topic_run",
                article_fetch_run_id="run_a",
            )

            with closing(get_connection(database_path)) as connection:
                topics = list_topics(connection)

        self.assertEqual(summary.articles_checked, 1)
        self.assertEqual(len(topics), 1)
        self.assertEqual(topics[0]["exact_topic"], "Nike launches AI-powered Olympic campaign")

    def insert_source(self, connection) -> int:
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
        row = connection.execute("SELECT id FROM sources WHERE website_url = ?", ("https://example.com",)).fetchone()
        return int(row["id"])

    def insert_article(self, connection, source_id: int, run_id: str, headline: str) -> None:
        slug = headline.lower().replace(" ", "-")
        upsert_article(
            connection,
            {
                "source_id": source_id,
                "source_name": "Example Ads",
                "source_url": "https://example.com",
                "article_url": f"https://example.com/news/{slug}",
                "headline": headline,
                "published_date": "2026-06-03",
                "author": "News Desk",
                "meta_description": headline,
                "article_text": f"{headline}. This article has enough detail for the parser.",
                "fetch_run_id": run_id,
            },
        )
