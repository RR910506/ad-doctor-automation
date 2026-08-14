from __future__ import annotations

from contextlib import closing
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest import mock

from src.db import get_connection, initialize_database
from ui.repositories.ui_read_repository import UIReadRepository
from ui.services.production_query_service import ProductionUIDataSource, QUALITY_MISSING, STRUCTURED_MISSING
from ui.services.ui_action_service import UIActionService
from ui.services.ui_query_service import UIQueryService


PROJECT = Path(__file__).resolve().parents[1]


class Phase4BFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "ui.db"
        initialize_database(self.database, PROJECT / "database" / "schema.sql")
        with closing(get_connection(self.database)) as connection:
            for index, name in enumerate(("Ad Age", "Campaign India", "Marketing Week"), 1):
                connection.execute("INSERT INTO sources(id,source_name,website_url,rss_url,category,country,credibility_tier,active_status) VALUES(?,?,?,?,?,?,?,1)", (index, name, f"https://source{index}.invalid", f"https://source{index}.invalid/rss", "Trade", "India", index))
                connection.execute("INSERT INTO articles(id,source_id,source_name,source_url,article_url,headline,published_date,meta_description,article_text,fetch_run_id) VALUES(?,?,?,?,?,?,?,?,?,?)", (index, index, name, f"https://source{index}.invalid", f"https://source{index}.invalid/article", f"Brand move report {index}", "2026-08-01", "Stored summary", "<p>Extracted safe text</p>", "run-1"))
            connection.execute("INSERT INTO topics(id,exact_topic,normalized_topic,representative_headline,status) VALUES(1,'Brand move','brand move','Brand move report','qualified')")
            connection.execute("INSERT INTO topics(id,exact_topic,normalized_topic,representative_headline,status) VALUES(2,'Early move','early move','Early move report','candidate')")
            for source_id in (1,2,3):connection.execute("INSERT INTO topic_sources(topic_id,source_id,article_id,coverage_url) VALUES(1,?,?,?)",(source_id,source_id,f"https://source{source_id}.invalid/article"))
            for source_id in (1,2):connection.execute("INSERT INTO topic_sources(topic_id,source_id,article_id,coverage_url) VALUES(2,?,?,?)",(source_id,source_id,f"https://source{source_id}.invalid/article"))
            locked=connection.execute("INSERT INTO locked_topics(topic_id,run_id,locked_topic) VALUES(1,'run-1','Brand move')").lastrowid
            connection.execute("INSERT INTO research_dossiers(locked_topic_id,dossier_json,source_urls_json) VALUES(?,?,?)",(locked,'{"source_names":["Ad Age"],"source_articles":["Brand move"],"strategic_insight":"Stored insight","performance_data":["Stored result"]}','["https://source1.invalid/article"]'))
            connection.execute("INSERT INTO dissection_outputs(locked_topic_id,output_markdown) VALUES(?,?)",(locked,"## Diagnosis\nStored diagnosis only"))
            self.output_id=connection.execute("INSERT INTO content_outputs(locked_topic_id,content_type,draft_markdown,approval_status) VALUES(?,'social','# Slide 1: Hook\nStored slide','pending')",(locked,)).lastrowid
            connection.execute("INSERT INTO content_outputs(locked_topic_id,content_type,draft_markdown,approval_status) VALUES(?,'blog','Stored long-form article','pending')",(locked,))
            connection.execute("UPDATE automation_settings SET enabled=1,schedule_time='09:00',schedule_times='[\"09:00\"]',timezone='Asia/Kolkata' WHERE id=1")
            connection.execute("INSERT INTO automation_run_history(schedule_key,scheduled_date,scheduled_time,timezone,run_id,status,started_at,finished_at) VALUES('k','2026-08-01','09:00','Asia/Kolkata','run-1','complete','2026-08-01','2026-08-01')")
            connection.execute("INSERT INTO run_logs(run_id,phase,event_type,message) VALUES('run-1','research','error','password=secret user@example.com')")
            connection.commit()

    def tearDown(self):
        self.temp.cleanup()

    def service(self):
        return UIQueryService(demo=False, database_path=self.database)


class ProductionReadTests(Phase4BFixture):
    def test_demo_mode_remains_isolated(self):
        service=UIQueryService(demo=True,database_path=self.database);self.assertTrue(service.demo);self.assertEqual(service.get("sources")[0].title,"Campaign India")

    def test_normal_mode_uses_production_source(self):
        service=self.service();self.assertIsInstance(service._production,ProductionUIDataSource);self.assertEqual(service.demo_label,"Live workspace")

    def test_repository_connection_is_read_only(self):
        with UIReadRepository(self.database).connection() as connection:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0],1)
            with self.assertRaises(sqlite3.OperationalError):connection.execute("INSERT INTO sources(source_name,website_url) VALUES('x','https://x.invalid')")

    def test_opening_all_pages_performs_no_write_or_network(self):
        before=hashlib.sha256(self.database.read_bytes()).hexdigest();service=self.service()
        with mock.patch.object(socket,"create_connection",side_effect=AssertionError("network")):
            for page in ("dashboard","sources","articles","qualification","cases","content_studio","publishing","subscribers","automation","logs","settings"):service.get(page)
        self.assertEqual(before,hashlib.sha256(self.database.read_bytes()).hexdigest())

    def test_missing_optional_tables_are_capabilities_not_errors(self):
        minimal=Path(self.temp.name)/"minimal.db"
        with closing(sqlite3.connect(minimal)) as connection:
            connection.execute("CREATE TABLE sources(id INTEGER PRIMARY KEY,source_name TEXT,website_url TEXT,rss_url TEXT,category TEXT,country TEXT,credibility_tier INTEGER,active_status INTEGER,created_at TEXT,updated_at TEXT)")
            connection.execute("CREATE TABLE articles(id INTEGER PRIMARY KEY,source_id INTEGER,source_name TEXT,source_url TEXT,article_url TEXT,headline TEXT,published_date TEXT,meta_description TEXT,article_text TEXT,fetch_timestamp TEXT,fetch_run_id TEXT)")
            connection.execute("CREATE TABLE topics(id INTEGER PRIMARY KEY,exact_topic TEXT,representative_headline TEXT,status TEXT,created_at TEXT)")
        source=ProductionUIDataSource(minimal);self.assertFalse(source.capabilities.case_files_available);self.assertEqual(source.subscribers(),());self.assertIsNone(source.automation())

    def test_dashboard_real_metrics(self):
        vm=self.service().get("dashboard");self.assertEqual(vm.metrics["Active sources"],"3");self.assertEqual(vm.metrics["Articles scanned"],"3");self.assertEqual(vm.metrics["Qualified topics"],"1")

    def test_sources_mapping_search_filter_and_pagination(self):
        service=self.service();items,meta=service.list("sources",search="Campaign",page_size=1);self.assertEqual(meta["total"],1);self.assertEqual(items[0].details["rss_url"],"https://source2.invalid/rss");items,_=service.list("sources",status="active",sort="A–Z");self.assertEqual(items[0].title,"Ad Age")

    def test_articles_mapping_database_pagination_and_safe_detail(self):
        service=self.service();items,meta=service.list("articles",page_size=2);self.assertEqual(meta["total"],3);self.assertEqual(len(items),2);self.assertNotIn("<p>",items[0].details["article_text"]);self.assertIn("Stored summary",items[0].details["summary"])

    def test_topic_counts_distinct_publications(self):
        items=self.service().get("qualification");qualified=next(x for x in items if x.title=="Brand move");early=next(x for x in items if x.title=="Early move");self.assertEqual(qualified.details["count"],3);self.assertEqual(len(set(qualified.details["publications"])),3);self.assertEqual(early.status,"qualification_pending")

    def test_cases_fall_back_to_legacy_projection(self):
        cases=self.service().get("cases");self.assertEqual(cases[0].details["data_source"],"legacy_projection");self.assertEqual(cases[0].details["topic_id"],1)

    def test_cases_use_case_records_when_present(self):
        with closing(get_connection(self.database)) as connection:
            connection.execute("INSERT INTO cases(case_id,topic_id,canonical_topic,workflow_status,qualification_status,unique_publication_count,approval_status,publication_status,created_at,updated_at,version) VALUES('case-1',1,'Brand move','editorial_review_pending','qualified',3,'pending','not_published',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1)");connection.commit()
        cases=self.service().get("cases");self.assertEqual(cases[0].details["data_source"],"case");self.assertEqual(cases[0].details["case_id"],"case-1")

    def test_content_studio_legacy_fallback_does_not_hallucinate(self):
        studio=self.service().get("content_studio");self.assertTrue(studio.queue);self.assertEqual(studio.selected["diagnosis"]["Diagnosis"],"Stored diagnosis only");self.assertEqual(studio.selected["diagnosis"]["Symptoms"],STRUCTURED_MISSING);self.assertEqual(studio.selected["quality_detail"]["warnings"],[QUALITY_MISSING]);self.assertIn("Stored long-form",studio.selected["article"])

    def test_content_studio_structured_package_and_quality_review(self):
        package={"diagnostic_hook":"Stored structured hook","symptoms":"Stored symptoms","patient_history":"Stored history","diagnosis":"Stored structured diagnosis","treatment":"Stored treatment","results":"Stored results","doctors_opinion":"Stored opinion","prescription":"Stored prescription","article":"Stored structured article","carousel":[{"slide_number":1,"slide_type":"diagnostic_hook","headline":"Stored headline","body":"Stored body","supporting_source_ids":["source-1"]}]}
        evidence={"facts":[{"fact":"Stored verified fact"}],"sources":[{"publication_name":"Ad Age","article_title":"Stored source","url":"https://source1.invalid/article"}]}
        quality={"dimension_scores":{"evidence":91},"warnings":["Stored warning"],"traceability_summary":"Stored traceability"}
        with closing(get_connection(self.database)) as connection:
            connection.execute("INSERT INTO cases(case_id,topic_id,canonical_topic,workflow_status,qualification_status,unique_publication_count,evidence_pack_id,draft_id,quality_status,approval_status,publication_status,created_at,updated_at,version) VALUES('case-structured',1,'Brand move','editorial_review_pending','qualified',3,'evidence-1',?,'pass','pending','not_published',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,1)",(str(self.output_id),))
            connection.execute("INSERT INTO evidence_packs(evidence_pack_id,case_id,schema_version,version,research_provider,research_model,research_completed_at,payload_json) VALUES('evidence-1','case-structured','1.0',1,'fake','fake',CURRENT_TIMESTAMP,?)",(json.dumps(evidence),))
            connection.execute("INSERT INTO content_packages(content_package_id,case_id,evidence_pack_id,draft_id,schema_version,package_version,content_type,generation_provider,generation_model,generated_at,payload_json,created_at,updated_at) VALUES('package-1','case-structured','evidence-1',?,'1.0',1,'social','fake','fake',CURRENT_TIMESTAMP,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",(str(self.output_id),json.dumps(package)))
            connection.execute("INSERT INTO quality_reviews(quality_review_id,case_id,content_package_id,package_version,review_version,gate_status,overall_score,auditor_provider,auditor_model,reviewed_at,payload_json) VALUES('quality-1','case-structured','package-1',1,1,'pass',91,'fake','fake',CURRENT_TIMESTAMP,?)",(json.dumps(quality),));connection.commit()
        studio=self.service().get("content_studio");self.assertTrue(studio.selected["structured"]);self.assertEqual(studio.selected["quality"],91);self.assertEqual(studio.selected["diagnosis"]["Diagnosis"],"Stored structured diagnosis");self.assertEqual(studio.selected["slides"][0]["headline"],"Stored headline")

    def test_automation_is_real_and_settings_hide_secrets(self):
        service=self.service();automation=service.get("automation");self.assertTrue(automation.enabled);self.assertTrue(automation.history);settings=str(service.get("settings"));self.assertNotIn("secret",settings.lower());self.assertNotIn("api_key",settings.lower())

    def test_logs_are_redacted_and_subscribers_not_faked(self):
        logs,_=self.service().list("logs");self.assertNotIn("password=secret",logs[0].title);self.assertIn("u***@example.com",logs[0].title);self.assertEqual(self.service().get("subscribers"),())

    def test_real_subscriber_email_is_masked(self):
        with closing(get_connection(self.database)) as connection:
            connection.execute("INSERT INTO subscribers(subscriber_id,email,normalized_email,name,status,consent_status,consent_source,consent_timestamp,frequency,segment,locale,verification_status,created_at,updated_at) VALUES('subscriber-1','person@example.com','person@example.com','Person','active','confirmed','form',CURRENT_TIMESTAMP,'weekly','leaders','en','verified',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)");connection.commit()
        item=self.service().get("subscribers")[0];self.assertEqual(item.title,"p***@example.com");self.assertNotIn("person@example.com",str(item))

    def test_publishing_is_readiness_only(self):
        vm=self.service().get("publishing");self.assertTrue(vm.blocked);self.assertIn("not yet been activated",vm.warnings[0])


class ActionTests(Phase4BFixture):
    def test_source_change_requires_confirmation_and_delegates(self):
        actions=UIActionService(self.database);self.assertEqual(actions.set_source_active(1,False).error_category,"confirmation_required");result=actions.set_source_active(1,False,confirmed=True);self.assertTrue(result.success)
        with closing(get_connection(self.database)) as connection:self.assertEqual(connection.execute("SELECT active_status FROM sources WHERE id=1").fetchone()[0],0)

    def test_confirmed_source_file_import_delegates_to_backend(self):
        file=BytesIO(b"source_name,website_url\nNew Publication,https://new.invalid\n");result=UIActionService(self.database).import_sources_from_file(file,filename="sources.csv",confirmed=True);self.assertTrue(result.success)
        with closing(get_connection(self.database)) as connection:self.assertEqual(connection.execute("SELECT COUNT(*) FROM sources WHERE website_url='https://new.invalid'").fetchone()[0],1)

    def test_approval_requires_reviewer_and_uses_backend(self):
        actions=UIActionService(self.database);self.assertEqual(actions.approve_content(self.output_id,reviewer_id="",confirmed=True).error_category,"validation");result=actions.approve_content(self.output_id,reviewer_id="editor",confirmed=True);self.assertTrue(result.success)
        with closing(get_connection(self.database)) as connection:self.assertEqual(connection.execute("SELECT approval_status FROM content_outputs WHERE id=?",(self.output_id,)).fetchone()[0],"approved")

    def test_approval_failure_is_redacted_and_safe(self):
        with mock.patch("src.approval_engine.set_content_approval",side_effect=RuntimeError("token=private-value")):
            result=UIActionService(self.database).reject_content(self.output_id,reviewer_id="editor",confirmed=True)
        self.assertFalse(result.success);self.assertNotIn("private-value",result.user_message)

    def test_manual_run_delegates_and_duplicate_is_prevented(self):
        runner=mock.Mock(return_value={"run_id":"manual-test"});actions=UIActionService(self.database,manual_runner=runner);result=actions.run_automation_now(confirmed=True);self.assertTrue(result.success);runner.assert_called_once()
        with closing(get_connection(self.database)) as connection:connection.execute("INSERT INTO automation_run_history(schedule_key,scheduled_date,scheduled_time,timezone,status) VALUES('running','2026-08-02','09:00','Asia/Kolkata','running')");connection.commit()
        duplicate=actions.run_automation_now(confirmed=True);self.assertEqual(duplicate.error_category,"duplicate_run")

    def test_regeneration_is_blocked(self):
        self.assertEqual(UIActionService(self.database).regenerate("section").error_category,"case_activation_required")


class ArchitectureTests(unittest.TestCase):
    def test_pages_have_no_sql_or_provider_calls(self):
        for path in (PROJECT/"ui"/"pages").glob("*.py"):
            text=path.read_text(encoding="utf-8").lower();self.assertNotIn("select ",text);self.assertNotIn("get_connection",text);self.assertNotIn("import openai",text);self.assertNotIn("from openai",text)

    def test_action_service_owns_mutation_delegation(self):
        text=(PROJECT/"ui"/"services"/"ui_action_service.py").read_text(encoding="utf-8");self.assertIn("set_content_approval",text);self.assertIn("import_sources_from_file",text);self.assertIn("run_automation_now",text)


if __name__ == "__main__":
    unittest.main()
