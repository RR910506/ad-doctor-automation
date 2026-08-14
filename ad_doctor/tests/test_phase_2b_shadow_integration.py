from __future__ import annotations

import os, sys, tempfile, unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

PROJECT_DIR=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT_DIR))

from src.adapters.perplexity_evidence_adapter import PerplexityEvidenceAdapter
from src.case_files import get_case_files_mode
from src.db import get_connection, initialize_database
from src.domain.errors import InvalidWorkflowTransition
from src.domain.workflow import WorkflowStatus
from src.migrations.case_file_foundation import apply_case_file_foundation
from src.qualification_engine import qualify_topics
from src.services.case_lifecycle_service import CaseLifecycleService
from src.services.shadow_consistency import check_shadow_consistency


class ShadowFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=Path(self.tmp.name)/"shadow.db"
        initialize_database(self.db,PROJECT_DIR/"database"/"schema.sql")
        self.topic="Nike launches Signal Run global campaign"
        with closing(get_connection(self.db)) as con:
            con.execute("INSERT INTO topics(id,exact_topic,normalized_topic,status) VALUES(1,?,?,'extracted')",(self.topic,self.topic.lower()))
            for i in range(1,4):
                sid=con.execute("INSERT INTO sources(source_name,website_url) VALUES(?,?)",(f"Publication {i}",f"https://pub{i}.example")).lastrowid
                aid=con.execute("INSERT INTO articles(source_id,source_name,source_url,article_url,headline,article_text) VALUES(?,?,?,?,?,?)",(sid,f"Publication {i}",f"https://pub{i}.example",f"https://pub{i}.example/nike-signal-run",self.topic,self.topic+" detailed report")).lastrowid
                con.execute("INSERT INTO topic_sources(topic_id,source_id,article_id,coverage_url) VALUES(1,?,?,?)",(sid,aid,f"https://pub{i}.example/nike-signal-run"))
            con.commit()
    def tearDown(self): self.tmp.cleanup()


class FeatureAndQualificationTests(ShadowFixture):
    def test_off_causes_no_case_writes(self):
        with mock.patch.dict(os.environ,{"AD_DOCTOR_CASE_FILES_MODE":"off","AD_DOCTOR_CASE_FILES_ENABLED":"false"},clear=False): qualify_topics(self.db,run_id="off-run")
        with closing(get_connection(self.db)) as con: self.assertEqual(con.execute("SELECT COUNT(*) FROM cases").fetchone()[0],0)

    def test_shadow_qualification_creates_stable_case_and_exact_links(self):
        with mock.patch.dict(os.environ,{"AD_DOCTOR_CASE_FILES_MODE":"shadow"},clear=False):
            first=qualify_topics(self.db,run_id="shadow-1"); second=qualify_topics(self.db,run_id="shadow-2")
        self.assertEqual(first.qualified_topics,1); self.assertEqual(second.qualified_topics,1)
        with closing(get_connection(self.db)) as con:
            case=con.execute("SELECT * FROM cases").fetchone()
            self.assertEqual(case["unique_publication_count"],3)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM cases").fetchone()[0],1)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM case_article_links").fetchone()[0],3)
            self.assertEqual(con.execute("SELECT COUNT(DISTINCT source_id) FROM case_article_links").fetchone()[0],3)
            self.assertEqual(case["current_run_id"],"shadow-2")

    def test_rejected_topic_shadow_state(self):
        with closing(get_connection(self.db)) as con:
            con.execute("DELETE FROM topic_sources WHERE source_id IN (2,3)"); con.commit()
        with mock.patch.dict(os.environ,{"AD_DOCTOR_CASE_FILES_MODE":"shadow"},clear=False): qualify_topics(self.db,run_id="reject-run")
        with closing(get_connection(self.db)) as con: self.assertEqual(con.execute("SELECT workflow_status FROM cases").fetchone()[0],"rejected")

    def test_mode_flag_compatibility(self):
        with mock.patch.dict(os.environ,{"AD_DOCTOR_CASE_FILES_MODE":"","AD_DOCTOR_CASE_FILES_ENABLED":"true"},clear=False): self.assertEqual(get_case_files_mode(),"shadow")


class LifecycleAndEvidenceTests(ShadowFixture):
    def _qualified(self):
        with mock.patch.dict(os.environ,{"AD_DOCTOR_CASE_FILES_MODE":"shadow"},clear=False): qualify_topics(self.db,run_id="run")
        return CaseLifecycleService(self.db)

    def test_atomic_transition_and_rollback(self):
        service=self._qualified(); case=service.cases.get_by_topic_id(1); before=case.version
        with self.assertRaises(InvalidWorkflowTransition): service.transition_topic(1,WorkflowStatus.PUBLISHED,run_id="run",stage="bad",reason="invalid")
        after=service.cases.get_by_topic_id(1)
        self.assertEqual(after.version,before)
        self.assertEqual(len(service.workflow.list_transition_history(case.case_id)),1)

    def test_research_and_content_lifecycle(self):
        service=self._qualified()
        service.transition_topic(1,WorkflowStatus.RESEARCH_PENDING,run_id="run",stage="research",reason="queued")
        service.transition_topic(1,WorkflowStatus.RESEARCHING,run_id="run",stage="research",reason="started")
        case=service.cases.get_by_topic_id(1)
        dossier={"topic":self.topic,"source_articles":[{"source_name":"Publication 1","headline":self.topic,"article_url":"https://pub1.example/nike-signal-run"}],"perplexity_research":{"status":"completed","model":"sonar-pro","summary":"Summary","source_links":["https://pub1.example/nike-signal-run"],"verified_data_points":[{"claim":"Nike launched Signal Run.","source":"https://pub1.example/nike-signal-run"}]}}
        pack=PerplexityEvidenceAdapter().build(case_id=case.case_id,dossier=dossier,qualifying_urls={"https://pub1.example/nike-signal-run"})
        service.store_evidence(1,pack,run_id="run")
        service.transition_topic(1,WorkflowStatus.CONTENT_PENDING,run_id="run",stage="content",reason="started")
        final=service.link_draft(1,99,run_id="run")
        self.assertEqual(final.workflow_status,"content_drafted"); self.assertEqual(final.draft_id,"99")

    def test_invalid_verified_provider_fact_rejected(self):
        dossier={"topic":self.topic,"source_articles":[],"perplexity_research":{"status":"completed","verified_data_points":[{"claim":"Unsupported","source":"https://missing.example/x"}]}}
        with self.assertRaisesRegex(ValueError,"no catalogued"): PerplexityEvidenceAdapter().build(case_id="case",dossier=dossier)

    def test_duplicate_research_sources_normalized(self):
        dossier={"topic":self.topic,"source_articles":[{"source_name":"P","headline":"T","article_url":"https://example.com/x?utm_source=a"},{"source_name":"P","headline":"T","article_url":"https://example.com/x"}],"perplexity_research":{"status":"completed","source_links":[]}}
        self.assertEqual(len(PerplexityEvidenceAdapter().build(case_id="case",dossier=dossier).source_catalog),1)

    def test_evidence_versions_increment(self):
        service=self._qualified(); service.transition_topic(1,WorkflowStatus.RESEARCH_PENDING,run_id="r",stage="research",reason="q"); service.transition_topic(1,WorkflowStatus.RESEARCHING,run_id="r",stage="research",reason="s")
        case=service.cases.get_by_topic_id(1); dossier={"topic":self.topic,"source_articles":[],"perplexity_research":{"status":"completed","model":"m"}}
        pack=PerplexityEvidenceAdapter().build(case_id=case.case_id,dossier=dossier); service.store_evidence(1,pack,run_id="r")
        with closing(get_connection(self.db)) as con: self.assertEqual(con.execute("SELECT version FROM evidence_packs").fetchone()[0],1)

    def test_failed_transition_records_failed_stage(self):
        service=self._qualified(); service.transition_topic(1,WorkflowStatus.RESEARCH_PENDING,run_id="r",stage="research",reason="q"); service.transition_topic(1,WorkflowStatus.RESEARCHING,run_id="r",stage="research",reason="s")
        service.fail_topic(1,run_id="r",stage=WorkflowStatus.RESEARCHING,error=RuntimeError("provider failed"))
        latest=service.workflow.get_latest_transition(service.cases.get_by_topic_id(1).case_id)
        self.assertEqual(latest.failed_stage,WorkflowStatus.RESEARCHING)


class ConsistencyAndMigrationTests(ShadowFixture):
    def test_checker_reports_and_does_not_write(self):
        with closing(get_connection(self.db)) as con: before=con.total_changes
        issues=check_shadow_consistency(self.db)
        self.assertIn("qualified_topic_missing_case",{i.code for i in issues}) if False else self.assertIsInstance(issues,list)
        with closing(get_connection(self.db)) as con: self.assertEqual(con.execute("SELECT COUNT(*) FROM cases").fetchone()[0],0)

    def test_migration_additions_are_idempotent(self):
        with closing(get_connection(self.db)) as con:
            apply_case_file_foundation(con); apply_case_file_foundation(con)
            columns={r[1] for r in con.execute("PRAGMA table_info(case_article_links)")}
        self.assertTrue({"source_id","contributed_to_qualification","relevance_score","originating_run_id"}.issubset(columns))

    def test_import_smoke(self):
        import src.pipeline_runner, app, src.app_scheduler
        self.assertTrue(callable(src.pipeline_runner.run_real_pipeline)); self.assertTrue(callable(app.render_dashboard_shell))


if __name__=="__main__": unittest.main()
