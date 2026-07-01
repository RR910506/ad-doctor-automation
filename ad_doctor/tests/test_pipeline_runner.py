from __future__ import annotations

from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


from src.article_fetcher import FetchSummary
from src.db import get_connection, initialize_database
from src.pipeline_runner import run_real_pipeline


class PipelinePreservationTest(unittest.TestCase):
    def test_successful_batch_survives_the_next_pipeline_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )
            with closing(get_connection(database_path)) as connection:
                source_id = connection.execute(
                    "INSERT INTO sources (source_name, website_url) VALUES ('Source', 'https://example.com')"
                ).lastrowid
                article_id = connection.execute(
                    """
                    INSERT INTO articles (
                        source_id, source_name, source_url, headline, article_url
                    )
                    VALUES (
                        ?, 'Source', 'https://example.com',
                        'Existing story', 'https://example.com/story'
                    )
                    """,
                    (source_id,),
                ).lastrowid
                topic_id = connection.execute(
                    """
                    INSERT INTO topics (exact_topic, normalized_topic, status)
                    VALUES ('Existing topic', 'existing topic', 'qualified')
                    """
                ).lastrowid
                connection.execute(
                    """
                    INSERT INTO topic_sources (topic_id, source_id, article_id, coverage_url)
                    VALUES (?, ?, ?, 'https://example.com/story')
                    """,
                    (topic_id, source_id, article_id),
                )
                locked_topic_id = connection.execute(
                    """
                    INSERT INTO locked_topics (topic_id, run_id, locked_topic)
                    VALUES (?, 'successful_run', 'Existing topic')
                    """,
                    (topic_id,),
                ).lastrowid
                connection.execute(
                    """
                    INSERT INTO content_outputs (
                        locked_topic_id, content_type, template_version, draft_markdown
                    ) VALUES (?, 'social', 'test', 'Existing publishable draft')
                    """,
                    (locked_topic_id,),
                )
                connection.commit()

            fetch_summary = FetchSummary(
                run_id="next_run",
                sources_checked=1,
                articles_seen=1,
                inserted=1,
                updated=0,
                failed=0,
                messages=[],
            )
            patches = (
                mock.patch("src.pipeline_runner.fetch_articles", return_value=fetch_summary),
                mock.patch(
                    "src.pipeline_runner.extract_topics",
                    return_value=SimpleNamespace(linked_coverages=1, skipped_articles=0),
                ),
                mock.patch("src.pipeline_runner.expand_topic_coverage_from_stored_articles"),
                mock.patch(
                    "src.pipeline_runner.qualify_topics",
                    return_value=SimpleNamespace(qualified_topics=1, rejected_topics=0),
                ),
                mock.patch(
                    "src.pipeline_runner.backfill_near_qualified_topic_coverage",
                    return_value=SimpleNamespace(matched_articles=0),
                ),
                mock.patch("src.pipeline_runner.prioritize_topics"),
                mock.patch(
                    "src.pipeline_runner.lock_top_topics",
                    return_value=SimpleNamespace(locked_count=1),
                ),
                mock.patch(
                    "src.pipeline_runner.build_research_dossiers",
                    return_value=SimpleNamespace(dossiers_created=1),
                ),
                mock.patch(
                    "src.pipeline_runner.run_campaign_dissections",
                    return_value=SimpleNamespace(dissections_created=1),
                ),
                mock.patch(
                    "src.pipeline_runner.generate_content_outputs",
                    return_value=SimpleNamespace(blog_drafts_created=1, social_drafts_created=1),
                ),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9]:
                run_real_pipeline(database_path=database_path, run_id="next_run")

            with closing(get_connection(database_path)) as connection:
                stored_drafts = connection.execute(
                    "SELECT draft_markdown FROM content_outputs"
                ).fetchall()

        self.assertEqual([row["draft_markdown"] for row in stored_drafts], ["Existing publishable draft"])


if __name__ == "__main__":
    unittest.main()
