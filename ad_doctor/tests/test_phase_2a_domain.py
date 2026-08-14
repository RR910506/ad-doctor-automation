from __future__ import annotations

import importlib
from contextlib import closing
import shutil
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from src.case_backfill import backfill_case_files
from src.case_file_compat import case_files_enabled, map_legacy_topic_to_case
from src.db import get_connection, initialize_database
from src.domain.case_file import CaseFile, ContentType, stable_case_id
from src.domain.errors import DomainValidationError, InvalidWorkflowTransition, VersionConflictError
from src.domain.evidence_pack import EvidenceItem, EvidencePack, EvidenceSource, VerificationStatus
from src.domain.workflow import ActorType, WorkflowService, WorkflowStatus
from src.migrations.case_file_foundation import apply_case_file_foundation
from src.repositories.sqlite_case_repository import SQLiteCaseRepository
from src.repositories.sqlite_evidence_repository import SQLiteEvidenceRepository
from src.repositories.sqlite_workflow_repository import SQLiteWorkflowRepository


def make_case(topic_id: int = 1, status: WorkflowStatus = WorkflowStatus.DISCOVERED) -> CaseFile:
    return CaseFile.for_topic(topic_id, "Nike launches a new global campaign", workflow_status=status.value, qualification_status="pending")


def make_pack(case_id: str, pack_id: str = "pack-1") -> EvidencePack:
    source = EvidenceSource("source-1", "Campaign", "Nike campaign", "https://example.com/nike", "2026-07-01", "2026-07-02", "publication", 2, True)
    fact = EvidenceItem("fact-1", "Nike launched the campaign.", "fact", ("source-1",), 0.9, VerificationStatus.VERIFIED)
    return EvidencePack("1.0", pack_id, case_id, "Nike launches a new global campaign", "perplexity", "sonar-pro", datetime.now(UTC), (source,), verified_facts=(fact,), research_summary="Verified launch coverage.")


class DomainModelTests(unittest.TestCase):
    def test_case_creation_and_validation(self):
        case = make_case()
        self.assertEqual(case.content_type, ContentType.UNCLASSIFIED)
        with self.assertRaises(DomainValidationError):
            CaseFile.for_topic(0, "", workflow_status="", qualification_status="")

    def test_stable_case_identity(self):
        self.assertEqual(stable_case_id(42), stable_case_id("42"))

    def test_invalid_publication_count(self):
        with self.assertRaises(DomainValidationError):
            CaseFile.for_topic(1, "Topic", workflow_status="discovered", qualification_status="pending", unique_publication_count=-1)

    def test_content_types_are_extensible_contract(self):
        self.assertIn(ContentType.LEGENDS, set(ContentType))


class EvidencePackTests(unittest.TestCase):
    def test_evidence_round_trip(self):
        pack = make_pack("case-1")
        self.assertEqual(EvidencePack.from_dict(pack.to_dict()), pack)

    def test_missing_source_reference(self):
        bad = EvidenceItem("fact", "Claim", "fact", ("missing",), 1, VerificationStatus.VERIFIED)
        with self.assertRaisesRegex(DomainValidationError, "missing source"):
            EvidencePack("1", "p", "c", "Topic", "perplexity", "model", datetime.now(UTC), (), verified_facts=(bad,))

    def test_unsupported_verified_claim(self):
        with self.assertRaisesRegex(DomainValidationError, "supporting source"):
            EvidenceItem("fact", "Claim", "fact", (), 1, VerificationStatus.VERIFIED)

    def test_duplicate_source_ids(self):
        source = EvidenceSource("s", "Pub", "Title", "https://example.com", None, "2026-01-01", "publication", 1, True)
        with self.assertRaisesRegex(DomainValidationError, "Duplicate"):
            EvidencePack("1", "p", "c", "Topic", "perplexity", "model", datetime.now(UTC), (source, source))

    def test_unknown_cannot_be_verified(self):
        source = EvidenceSource("s", "Pub", "Title", "https://example.com", None, "2026-01-01", "publication", 1, True)
        unknown = EvidenceItem("u", "Unknown result", "unknown", ("s",), .5, VerificationStatus.VERIFIED)
        with self.assertRaisesRegex(DomainValidationError, "Unknowns"):
            EvidencePack("1", "p", "c", "Topic", "perplexity", "model", datetime.now(UTC), (source,), unknowns=(unknown,))


class SQLiteFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.db"
        initialize_database(self.db, PROJECT_DIR / "database" / "schema.sql")
        with closing(get_connection(self.db)) as con:
            con.execute("INSERT INTO topics (id, exact_topic, normalized_topic, status) VALUES (1, 'Nike launches a new global campaign', 'nike launches a new global campaign', 'extracted')")
            con.commit()

    def tearDown(self):
        self.temp.cleanup()

    def test_sqlite_case_crud_and_duplicate_idempotency(self):
        repo = SQLiteCaseRepository(self.db)
        stored, created = repo.create_or_get(make_case())
        again, second_created = repo.create_or_get(make_case())
        self.assertTrue(created)
        self.assertFalse(second_created)
        self.assertEqual(stored.case_id, again.case_id)
        self.assertEqual(len(repo.list_cases(workflow_status="discovered")), 1)

    def test_version_conflict(self):
        repo = SQLiteCaseRepository(self.db)
        case = repo.create(make_case())
        updated = repo.update(case.with_changes(brand="Nike"), 1)
        self.assertEqual(updated.version, 2)
        with self.assertRaises(VersionConflictError):
            repo.update(case.with_changes(campaign="Launch"), 1)

    def test_repository_transaction_rolls_back_invalid_foreign_key(self):
        repo = SQLiteCaseRepository(self.db)
        with self.assertRaises(Exception):
            repo.create(make_case(topic_id=999))
        self.assertIsNone(repo.get_by_topic_id(999))

    def test_evidence_repository_versions(self):
        case = SQLiteCaseRepository(self.db).create(make_case())
        repo = SQLiteEvidenceRepository(self.db)
        repo.create(make_pack(case.case_id, "pack-1"))
        repo.create(make_pack(case.case_id, "pack-2"))
        self.assertEqual(repo.get_latest_for_case(case.case_id).evidence_pack_id, "pack-2")
        self.assertEqual(len(repo.list_versions_for_case(case.case_id)), 2)

    def test_valid_transition_and_audit_history(self):
        case = SQLiteCaseRepository(self.db).create(make_case())
        repo = SQLiteWorkflowRepository(self.db)
        updated, event = WorkflowService(repo).transition(case, WorkflowStatus.QUALIFICATION_PENDING, actor_type=ActorType.SYSTEM, actor_id="pipeline", reason="Ready")
        self.assertEqual(updated.workflow_status, "qualification_pending")
        self.assertEqual(repo.get_latest_transition(case.case_id), event)

    def test_invalid_transition(self):
        case = SQLiteCaseRepository(self.db).create(make_case())
        with self.assertRaises(InvalidWorkflowTransition):
            WorkflowService(SQLiteWorkflowRepository(self.db)).transition(case, WorkflowStatus.PUBLISHED, actor_type=ActorType.SYSTEM, actor_id="pipeline", reason="Skip")

    def test_repeated_transition_is_idempotent(self):
        case = SQLiteCaseRepository(self.db).create(make_case(1, WorkflowStatus.QUALIFICATION_PENDING))
        updated, event = WorkflowService(SQLiteWorkflowRepository(self.db)).transition(case, WorkflowStatus.QUALIFICATION_PENDING, actor_type=ActorType.SYSTEM, actor_id="pipeline", reason="Retry")
        self.assertEqual(updated, case)
        self.assertIsNone(event)

    def test_migration_is_idempotent(self):
        with closing(get_connection(self.db)) as con:
            apply_case_file_foundation(con)
            apply_case_file_foundation(con)
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"cases", "evidence_packs", "workflow_transitions"}.issubset(tables))


class CompatibilityAndBackfillTests(unittest.TestCase):
    def test_feature_flag_defaults_off(self):
        self.assertFalse(case_files_enabled())

    def test_compatibility_mapping_uses_supported_legacy_evidence(self):
        case = map_legacy_topic_to_case({"id": 7, "exact_topic": "A qualified campaign topic", "status": "qualified"}, draft={"id": 3})
        self.assertEqual(case.workflow_status, WorkflowStatus.CONTENT_DRAFTED.value)

    def test_backfill_dry_run_and_duplicate_prevention(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "backfill.db"
            initialize_database(db, PROJECT_DIR / "database" / "schema.sql")
            with closing(get_connection(db)) as con:
                con.execute("INSERT INTO topics (id, exact_topic, normalized_topic, status) VALUES (1, 'Campaign topic covered by three publications', 'campaign topic covered by three publications', 'qualified')")
                con.commit()
            preview = backfill_case_files(db)
            self.assertEqual((preview.would_create, preview.created), (1, 0))
            created = backfill_case_files(db, dry_run=False)
            repeated = backfill_case_files(db, dry_run=False)
            self.assertEqual(created.created, 1)
            self.assertEqual(repeated.skipped_existing, 1)

    def test_application_import_smoke(self):
        module = importlib.import_module("app")
        self.assertTrue(callable(module.render_dashboard_shell))


if __name__ == "__main__":
    unittest.main()
