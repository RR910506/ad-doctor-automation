from __future__ import annotations

import os
import tempfile
from contextlib import closing
from pathlib import Path
import unittest

from src.db import (
    clear_generated_data_for_google_store_once,
    get_connection,
    initialize_database,
    upsert_source,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_DIR / "database" / "schema.sql"


class GoogleDraftStoreTest(unittest.TestCase):
    def test_google_mode_clears_generated_tables_once_and_preserves_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "ad_doctor.db"
            previous_mode = os.environ.get("AD_DOCTOR_DRAFT_STORE")
            os.environ["AD_DOCTOR_DRAFT_STORE"] = "sqlite"
            try:
                initialize_database(database_path=database_path, schema_path=SCHEMA_PATH)
                with closing(get_connection(database_path)) as connection:
                    upsert_source(
                        connection,
                        {
                            "source_name": "Example Source",
                            "website_url": "https://example.com",
                            "rss_url": "",
                            "category": "News",
                            "country": "India",
                            "credibility_tier": 1,
                            "active_status": 1,
                        },
                    )
                    source_id = int(
                        connection.execute(
                            "SELECT id FROM sources WHERE website_url = 'https://example.com'"
                        ).fetchone()[0]
                    )
                    connection.execute(
                        """
                        INSERT INTO articles (
                            source_id, source_name, source_url, article_url, headline
                        )
                        VALUES (?, 'Example Source', 'https://example.com', 'https://example.com/a', 'Old article')
                        """,
                        (source_id,),
                    )
                    connection.execute(
                        """
                        INSERT INTO topics (exact_topic, normalized_topic, representative_headline)
                        VALUES ('Old topic', 'old topic', 'Old topic')
                        """
                    )
                    topic_id = int(connection.execute("SELECT id FROM topics").fetchone()[0])
                    connection.execute(
                        """
                        INSERT INTO locked_topics (topic_id, run_id, locked_topic)
                        VALUES (?, 'run_old', 'Old topic')
                        """,
                        (topic_id,),
                    )
                    locked_topic_id = int(connection.execute("SELECT id FROM locked_topics").fetchone()[0])
                    connection.execute(
                        """
                        INSERT INTO content_outputs (
                            locked_topic_id, content_type, template_version, draft_markdown
                        )
                        VALUES (?, 'social', 'test', 'Old draft')
                        """,
                        (locked_topic_id,),
                    )
                    connection.commit()

                    os.environ["AD_DOCTOR_DRAFT_STORE"] = "google"
                    clear_generated_data_for_google_store_once(connection)
                    connection.commit()

                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 1)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM topics").fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM content_outputs").fetchone()[0], 0)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM system_flags").fetchone()[0], 1)

                    connection.execute(
                        """
                        INSERT INTO articles (
                            source_id, source_name, source_url, article_url, headline
                        )
                        VALUES (?, 'Example Source', 'https://example.com', 'https://example.com/new', 'New article')
                        """,
                        (source_id,),
                    )
                    connection.commit()
                    clear_generated_data_for_google_store_once(connection)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 1)
            finally:
                if previous_mode is None:
                    os.environ.pop("AD_DOCTOR_DRAFT_STORE", None)
                else:
                    os.environ["AD_DOCTOR_DRAFT_STORE"] = previous_mode


if __name__ == "__main__":
    unittest.main()
