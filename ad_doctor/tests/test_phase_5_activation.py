from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.db import get_connection, initialize_database
from src.domain.case_file import stable_case_id
from src.production_activation import (
    activate_production_backfill,
    assess_production_backfill,
    correct_case_qualification_independence,
    set_shadow_mode_in_dotenv,
)
from src.domain.case_file import ContentType
from src.domain.errors import DomainValidationError
from ui.services.ui_query_service import UIQueryService


class Phase5ActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "phase5.db"
        initialize_database(self.db, PROJECT_DIR / "database" / "schema.sql")
        self._add_topic(1, "Three-publication campaign", "qualified", 3, editorial=True)
        self._add_topic(2, "False legacy qualification", "qualified", 2, editorial=True)
        self._add_topic(3, "Historical noise", "extracted", 3, editorial=False)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _add_topic(self, topic_id: int, title: str, status: str, publication_count: int, *, editorial: bool) -> None:
        with closing(get_connection(self.db)) as connection:
            connection.execute(
                "INSERT INTO topics(id,exact_topic,normalized_topic,status) VALUES(?,?,?,?)",
                (topic_id, title, title.lower(), status),
            )
            connection.execute(
                "INSERT INTO topic_scores(topic_id,active_source_count,unique_source_coverage_count,minimum_required_sources,coverage_percentage,total_score) VALUES(?,?,?,?,?,?)",
                (topic_id, publication_count, publication_count, 3, publication_count / 3 * 100, 80),
            )
            articles = []
            for index in range(publication_count):
                source_id = topic_id * 10 + index
                url = f"https://publication-{source_id}.example/{topic_id}"
                connection.execute(
                    "INSERT INTO sources(id,source_name,website_url,active_status) VALUES(?,?,?,1)",
                    (source_id, f"Publication {source_id}", f"https://publication-{source_id}.example"),
                )
                article_id = connection.execute(
                    "INSERT INTO articles(source_id,source_name,source_url,article_url,headline,article_text) VALUES(?,?,?,?,?,?)",
                    (source_id, f"Publication {source_id}", f"https://publication-{source_id}.example", url, title, "Reported development."),
                ).lastrowid
                connection.execute(
                    "INSERT INTO topic_sources(topic_id,source_id,article_id,coverage_url) VALUES(?,?,?,?)",
                    (topic_id, source_id, article_id, url),
                )
                articles.append({"source_name": f"Publication {source_id}", "headline": title, "article_url": url})
            if editorial:
                locked_id = connection.execute(
                    "INSERT INTO locked_topics(topic_id,run_id,locked_topic) VALUES(?,?,?)",
                    (topic_id, "legacy-run", title),
                ).lastrowid
                dossier = {
                    "topic": title,
                    "source_articles": articles,
                    "perplexity_research": {
                        "status": "completed",
                        "model": "sonar-pro",
                        "summary": "Legacy research summary.",
                        "source_links": [item["article_url"] for item in articles],
                        "verified_data_points": [
                            {"claim": "The development was reported.", "source": articles[0]["article_url"]}
                        ],
                    },
                }
                connection.execute(
                    "INSERT INTO research_dossiers(locked_topic_id,dossier_json,source_urls_json) VALUES(?,?,?)",
                    (locked_id, json.dumps(dossier), json.dumps([item["article_url"] for item in articles])),
                )
                connection.execute(
                    "INSERT INTO content_outputs(locked_topic_id,content_type,draft_markdown,approval_status) VALUES(?,?,?,'pending')",
                    (locked_id, "social", "Legacy draft"),
                )
            connection.commit()

    def test_dry_run_is_zero_write_and_enforces_three_publications(self) -> None:
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        report = assess_production_backfill(self.db)
        after = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.assertEqual(before, after)
        self.assertEqual(report.historical_topics, 3)
        self.assertEqual(report.selected_candidates, 1)
        self.assertEqual(report.flagged, 1)
        self.assertEqual(report.would_create, 1)
        self.assertEqual(report.structured_packages_created, 0)

    def test_backfill_is_truthful_and_idempotent(self) -> None:
        first = activate_production_backfill(self.db)
        second = activate_production_backfill(self.db)
        self.assertEqual(first.created, 1)
        self.assertEqual(first.article_links_created, 3)
        self.assertEqual(first.evidence_packs_created, 1)
        self.assertEqual(first.draft_linkages, 1)
        self.assertEqual(first.approval_mappings, 0)
        self.assertEqual(first.structured_packages_created, 0)
        self.assertEqual(second.created, 0)
        self.assertEqual(second.skipped_existing, 1)
        self.assertEqual(second.article_links_created, 0)
        self.assertEqual(second.evidence_packs_created, 0)
        with closing(get_connection(self.db)) as connection:
            case = dict(connection.execute("SELECT * FROM cases").fetchone())
            self.assertEqual(case["case_id"], stable_case_id(1))
            self.assertEqual(case["workflow_status"], "content_drafted")
            self.assertEqual(case["unique_publication_count"], 3)
            self.assertIsNotNone(case["draft_id"])
            self.assertIsNotNone(case["evidence_pack_id"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM case_article_links").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM evidence_packs").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM content_packages").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM approval_events").fetchone()[0], 0)

    def test_shadow_mode_update_preserves_other_environment_lines(self) -> None:
        dotenv = Path(self.temp.name) / ".env"
        dotenv.write_text("SECRET_TOKEN=keep-me\nAD_DOCTOR_CASE_FILES_MODE=off\n", encoding="utf-8")
        set_shadow_mode_in_dotenv(dotenv)
        contents = dotenv.read_text(encoding="utf-8")
        self.assertIn("SECRET_TOKEN=keep-me", contents)
        self.assertIn("AD_DOCTOR_CASE_FILES_MODE=shadow", contents)
        self.assertNotIn("AD_DOCTOR_CASE_FILES_MODE=off", contents)

    def test_modern_ui_reads_activated_cases_in_shadow_mode(self) -> None:
        activate_production_backfill(self.db)
        with mock.patch.dict("os.environ", {"AD_DOCTOR_CASE_FILES_MODE": "shadow"}, clear=False):
            service = UIQueryService(demo=False, database_path=self.db)
            cases = service.get("cases")
            dashboard = service.get("dashboard")
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0].details["data_source"], "case")
            self.assertEqual(dashboard.metrics["Qualified cases"], "1")
            self.assertEqual(dashboard.health["Case architecture"], "Shadow enabled")
            self.assertEqual(service.capabilities["case_mode"], "shadow")

    def test_independence_correction_preserves_nonqualifying_history(self) -> None:
        activate_production_backfill(self.db)
        with closing(get_connection(self.db)) as connection:
            source_id = 99
            connection.execute("INSERT INTO sources(id,source_name,website_url,active_status) VALUES(?,?,?,1)", (source_id, "Unrelated", "https://unrelated.example"))
            article_id = connection.execute("INSERT INTO articles(source_id,source_name,source_url,article_url,headline,article_text) VALUES(?,?,?,?,?,?)", (source_id, "Unrelated", "https://unrelated.example", "https://unrelated.example/history", "Different event", "Different event")).lastrowid
            connection.execute("INSERT INTO case_article_links(case_id,article_id,link_role,source_id,contributed_to_qualification,relevance_score) VALUES(?,?,?,?,1,1.0)", (stable_case_id(1), article_id, "exact_topic_evidence", source_id))
            qualifying = {int(row[0]): f"Network {index}" for index, row in enumerate(connection.execute("SELECT article_id FROM case_article_links WHERE case_id=? AND article_id!=? ORDER BY article_id", (stable_case_id(1), article_id)), 1)}
            connection.commit()
        result = correct_case_qualification_independence(self.db, case_id=stable_case_id(1), contributing_articles=qualifying, brand="Brand", content_type=ContentType.MARKETING_DIAGNOSIS, actor_id="editor", run_id="live-test")
        self.assertEqual(result["independent_publisher_count"], 3)
        with closing(get_connection(self.db)) as connection:
            case = dict(connection.execute("SELECT * FROM cases WHERE case_id=?", (stable_case_id(1),)).fetchone())
            historical = dict(connection.execute("SELECT * FROM case_article_links WHERE article_id=?", (article_id,)).fetchone())
            self.assertEqual(case["content_type"], "marketing_diagnosis")
            self.assertEqual(case["campaign"], None)
            self.assertEqual(case["unique_publication_count"], 3)
            self.assertEqual(historical["contributed_to_qualification"], 0)
            self.assertEqual(historical["link_role"], "historical_non_qualifying")

    def test_independence_correction_rejects_duplicate_networks(self) -> None:
        activate_production_backfill(self.db)
        with closing(get_connection(self.db)) as connection:
            ids = [int(row[0]) for row in connection.execute("SELECT article_id FROM case_article_links WHERE case_id=? ORDER BY article_id", (stable_case_id(1),))]
        with self.assertRaises(DomainValidationError):
            correct_case_qualification_independence(self.db, case_id=stable_case_id(1), contributing_articles={ids[0]: "Same", ids[1]: "Same", ids[2]: "Same"}, brand="Brand", content_type=ContentType.MARKETING_DIAGNOSIS, actor_id="editor", run_id="live-test")


if __name__ == "__main__":
    unittest.main()
