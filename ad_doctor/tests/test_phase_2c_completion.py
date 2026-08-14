from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.approval_engine import set_content_approval
from src.case_files import get_case_files_mode
from src.db import get_connection, initialize_database
from src.domain.approval import ApprovalDecision, ApprovalStatus
from src.domain.case_file import CaseFile
from src.domain.errors import DomainValidationError, VersionConflictError
from src.google_workspace_store import load_service_account_info
from src.notification_engine import load_smtp_settings, save_smtp_settings
from src.openai_content_writer import load_writer_runtime_settings
from src.repositories.sqlite_case_repository import SQLiteCaseRepository
from src.research_engine import apply_saved_research_provider_settings
from src.security.redaction import redact
from src.security.secret_audit import audit_secrets
from src.security.secret_provider import MissingSecretError, SecretResult
from src.security.providers import CompositeSecretProvider, EnvironmentSecretProvider, LegacySecretProvider
from src.services.approval_consistency import check_approval_consistency
from src.services.editorial_approval_service import EditorialApprovalService
from src.services.publishing_eligibility import publishing_eligibility
from src.settings_engine import save_content_writer_settings, save_research_settings


FAKES = {
    "OPENAI_API_KEY": "FAKE_OPENAI_DO_NOT_STORE_9x",
    "PERPLEXITY_API_KEY": "FAKE_PERPLEXITY_DO_NOT_STORE_8y",
    "GOOGLE_SERVICE_ACCOUNT_JSON": json.dumps({"type": "service_account", "private_key": "FAKE_GOOGLE_PRIVATE_7z"}),
    "SMTP_HOST": "smtp.invalid",
    "SMTP_PORT": "2525",
    "SMTP_USERNAME": "FAKE_SMTP_USER_6q",
    "SMTP_PASSWORD": "FAKE_SMTP_PASSWORD_5p",
    "SMTP_FROM_EMAIL": "fake@example.invalid",
}


class FakeSecrets:
    def __init__(self, values=None): self.values = values or FAKES
    def resolve(self, key):
        value = self.values.get(key)
        return SecretResult(key, bool(value), "fake", value)


class CompletionFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "phase2c.db"
        initialize_database(self.db, PROJECT_DIR / "database" / "schema.sql")
        with closing(get_connection(self.db)) as con:
            con.execute("INSERT INTO topics(id,exact_topic,normalized_topic,status) VALUES(1,'Topic','topic','qualified')")
            locked = con.execute("INSERT INTO locked_topics(topic_id,run_id,locked_topic) VALUES(1,'run','Topic')").lastrowid
            self.draft1 = con.execute("INSERT INTO content_outputs(locked_topic_id,content_type,draft_markdown,created_at) VALUES(?,'social','one','2026-01-01T00:00:00+00:00')", (locked,)).lastrowid
            self.draft2 = con.execute("INSERT INTO content_outputs(locked_topic_id,content_type,draft_markdown,created_at) VALUES(?,'social','two','2026-01-02T00:00:00+00:00')", (locked,)).lastrowid
            con.commit()
        self.case = SQLiteCaseRepository(self.db).create(
            CaseFile.for_topic(1, "Topic", workflow_status="content_drafted", qualification_status="qualified", draft_id=str(self.draft1))
        )

    def tearDown(self): self.temp.cleanup()


class FeatureModeTests(CompletionFixture):
    def event_count(self):
        with closing(get_connection(self.db)) as con:
            return con.execute("SELECT COUNT(*) FROM approval_events").fetchone()[0]

    def legacy_status(self, draft):
        with closing(get_connection(self.db)) as con:
            return con.execute("SELECT approval_status FROM content_outputs WHERE id=?", (draft,)).fetchone()[0]

    def test_default_and_off_mode_write_no_unified_events(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_case_files_mode(), "off")
            set_content_approval(self.draft1, "approved", database_path=self.db, run_id="off-a")
            set_content_approval(self.draft1, "rejected", reviewer_notes="reason", database_path=self.db, run_id="off-r")
        self.assertEqual(self.event_count(), 0)

    def test_shadow_approve_reject_and_retry(self):
        with mock.patch.dict(os.environ, {"AD_DOCTOR_CASE_FILES_MODE": "shadow"}, clear=False):
            set_content_approval(self.draft1, "approved", database_path=self.db, run_id="shadow-a")
            first = self.event_count()
            set_content_approval(self.draft1, "approved", database_path=self.db, run_id="shadow-a")
        self.assertEqual(first, 2)
        self.assertEqual(self.event_count(), first)
        self.assertEqual(SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id).approval_status, "approved")

    def test_shadow_reject_mirrors(self):
        with mock.patch.dict(os.environ, {"AD_DOCTOR_CASE_FILES_MODE": "shadow"}, clear=False):
            set_content_approval(self.draft1, "rejected", reviewer_notes="bad claims", database_path=self.db, run_id="shadow-r")
        self.assertEqual(self.legacy_status(self.draft1), "rejected")
        self.assertEqual(SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id).approval_status, "rejected")

    def test_shadow_failure_preserves_legacy_approve_and_reject(self):
        with mock.patch.dict(os.environ, {"AD_DOCTOR_CASE_FILES_MODE": "shadow"}, clear=False), \
             mock.patch("src.services.editorial_approval_service.EditorialApprovalService.decide", side_effect=RuntimeError("mirror failed")):
            set_content_approval(self.draft1, "approved", database_path=self.db, run_id="fail-a")
            set_content_approval(self.draft2, "rejected", reviewer_notes="reason", database_path=self.db, run_id="fail-r")
        self.assertEqual(self.legacy_status(self.draft1), "approved")
        self.assertEqual(self.legacy_status(self.draft2), "rejected")
        self.assertEqual(self.event_count(), 0)
        with closing(get_connection(self.db)) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM run_logs WHERE phase='legacy_approval_shadow' AND event_type='error'").fetchone()[0], 2)


class SupersessionConcurrencyTests(CompletionFixture):
    def test_newer_approval_supersedes_old_transactionally_and_idempotently(self):
        service = EditorialApprovalService(self.db)
        service.decide(self.case.case_id, str(self.draft1), ApprovalDecision.SUBMIT_FOR_REVIEW, reviewer_id="one")
        service.decide(self.case.case_id, str(self.draft1), ApprovalDecision.APPROVE, reviewer_id="one")
        service.decide(self.case.case_id, str(self.draft2), ApprovalDecision.SUBMIT_FOR_REVIEW, reviewer_id="two")
        approved = service.decide(self.case.case_id, str(self.draft2), ApprovalDecision.APPROVE, reviewer_id="two")
        retry = service.decide(self.case.case_id, str(self.draft2), ApprovalDecision.APPROVE, reviewer_id="two")
        history = service.events.history(self.case.case_id)
        self.assertEqual(approved.approval_event_id, retry.approval_event_id)
        self.assertEqual(sum(e.decision == ApprovalDecision.SUPERSEDE for e in history), 1)
        current = SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id)
        self.assertEqual(current.draft_id, str(self.draft2))
        self.assertFalse(publishing_eligibility(current, True, newer_unreviewed_draft=True).allowed)

    def test_older_draft_cannot_supersede_newer(self):
        service = EditorialApprovalService(self.db)
        service.decide(self.case.case_id, str(self.draft2), ApprovalDecision.SUBMIT_FOR_REVIEW, reviewer_id="e")
        service.decide(self.case.case_id, str(self.draft2), ApprovalDecision.APPROVE, reviewer_id="e")
        service.decide(self.case.case_id, str(self.draft1), ApprovalDecision.SUBMIT_FOR_REVIEW, reviewer_id="e")
        with self.assertRaises(DomainValidationError):
            service.decide(self.case.case_id, str(self.draft1), ApprovalDecision.APPROVE, reviewer_id="e")
        self.assertEqual(SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id).draft_id, str(self.draft1))
        self.assertEqual(len([e for e in service.events.history(self.case.case_id) if e.new_status == ApprovalStatus.SUPERSEDED]), 0)

    def test_stale_case_and_approval_assumptions_write_nothing(self):
        service = EditorialApprovalService(self.db)
        before = SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id)
        with self.assertRaises(VersionConflictError):
            service.decide(self.case.case_id, str(self.draft1), ApprovalDecision.SUBMIT_FOR_REVIEW,
                           reviewer_id="e", expected_case_version=before.version - 1)
        self.assertEqual(service.events.history(self.case.case_id), [])
        current = SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id)
        self.assertEqual(current.version, before.version)
        with self.assertRaises(VersionConflictError):
            service.decide(self.case.case_id, str(self.draft1), ApprovalDecision.SUBMIT_FOR_REVIEW,
                           reviewer_id="e", expected_previous_status=ApprovalStatus.APPROVED)
        self.assertEqual(service.events.history(self.case.case_id), [])


class SecurityCompletionTests(CompletionFixture):
    def test_all_provider_loaders_accept_injected_secrets_without_network(self):
        provider = FakeSecrets()
        self.assertEqual(load_writer_runtime_settings(self.db, secret_provider=provider)["api_key"], FAKES["OPENAI_API_KEY"])
        import src.perplexity_research as perplexity
        old = (perplexity.ACTIVE_RESEARCH_PROVIDER, perplexity.PERPLEXITY_API_KEY, perplexity.PERPLEXITY_MODEL, perplexity.PERPLEXITY_ENDPOINT)
        try: apply_saved_research_provider_settings(self.db, secret_provider=provider)
        finally: perplexity.ACTIVE_RESEARCH_PROVIDER, perplexity.PERPLEXITY_API_KEY, perplexity.PERPLEXITY_MODEL, perplexity.PERPLEXITY_ENDPOINT = old
        self.assertIn("FAKE_GOOGLE_PRIVATE", load_service_account_info(secret_provider=provider)["private_key"])
        self.assertEqual(load_smtp_settings(self.db, secret_provider=provider).password, FAKES["SMTP_PASSWORD"])

    def test_new_settings_never_persist_fake_secrets(self):
        save_research_settings(provider_key="perplexity", api_key=FAKES["PERPLEXITY_API_KEY"], model="m", endpoint="https://example.invalid", database_path=self.db)
        save_content_writer_settings(provider_key="openai", enabled=True, api_key=FAKES["OPENAI_API_KEY"], model="m", endpoint="https://example.invalid", database_path=self.db)
        save_smtp_settings(smtp_host=FAKES["SMTP_HOST"], smtp_port=2525, smtp_username=FAKES["SMTP_USERNAME"], smtp_password=FAKES["SMTP_PASSWORD"], smtp_from=FAKES["SMTP_FROM_EMAIL"], use_tls=True, database_path=self.db)
        with closing(get_connection(self.db)) as con:
            serialized = json.dumps([dict(r) for table in ("research_provider_settings", "content_writer_settings", "email_settings") for r in con.execute(f"SELECT * FROM {table}")])
        for secret in FAKES.values(): self.assertNotIn(secret, serialized)

    def test_redaction_audit_warning_and_missing_error_are_value_free(self):
        marker = "KNOWN_TEST_SECRET_123"
        provider = CompositeSecretProvider(EnvironmentSecretProvider({"OPENAI_API_KEY": marker}))
        self.assertNotIn(marker, repr(audit_secrets(provider)))
        warning = LegacySecretProvider(lambda key: marker).resolve("OPENAI_API_KEY").warning
        self.assertNotIn(marker, warning)
        self.assertNotIn(marker, str(redact({"outer": {"password": marker}, "message": f"token={marker}"})))
        error = MissingSecretError("OPENAI_API_KEY is missing")
        self.assertEqual(str(error), "OPENAI_API_KEY is missing")

    def test_external_call_sentinel(self):
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("live network attempted")), \
             mock.patch("smtplib.SMTP", side_effect=AssertionError("SMTP attempted")), \
             mock.patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP attempted")), \
             mock.patch("urllib.request.urlopen", side_effect=AssertionError("URL open attempted")):
            self.test_all_provider_loaders_accept_injected_secrets_without_network()


class ConsistencyAndSmokeTests(CompletionFixture):
    def test_checker_is_read_only_and_reports_fixture_counts(self):
        with closing(get_connection(self.db)) as con:
            con.execute("UPDATE content_outputs SET approval_status='approved' WHERE id=?", (self.draft1,))
            con.execute("UPDATE cases SET approval_status='approved',workflow_status='content_drafted',publication_status='published' WHERE case_id=?", (self.case.case_id,))
            con.commit()
        before_hash = hashlib.sha256(self.db.read_bytes()).hexdigest()
        with closing(get_connection(self.db)) as con:
            before_rows = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("cases", "content_outputs", "approval_events")}
        issues = check_approval_consistency(self.db, google_statuses={str(self.draft1): "mystery"})
        counts = {kind: sum(i.issue_type == kind for i in issues) for kind in {i.issue_type for i in issues}}
        self.assertEqual(counts["case_approved_without_event"], 1)
        self.assertEqual(counts["workflow_approval_mismatch"], 1)
        self.assertEqual(counts["publishable_without_reviewer"], 1)
        self.assertEqual(counts["unknown_google_status"], 1)
        with closing(get_connection(self.db)) as con:
            after_rows = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before_rows}
        self.assertEqual(before_rows, after_rows)
        self.assertEqual(before_hash, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_bounded_streamlit_import(self):
        smoke_db = Path(self.temp.name) / "smoke.db"
        env = {**os.environ, "AD_DOCTOR_DATABASE_PATH": str(smoke_db), "AD_DOCTOR_CASE_FILES_MODE": "off"}
        process = subprocess.Popen([sys.executable, "-B", "-c", "import app; print('IMPORT_OK')"], cwd=PROJECT_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            process.terminate()
            try: stdout, stderr = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill(); stdout, stderr = process.communicate(timeout=3)
        self.assertNotIn("Traceback (most recent call last)", stderr)
        self.assertIsNotNone(process.returncode)


if __name__ == "__main__":
    unittest.main()
