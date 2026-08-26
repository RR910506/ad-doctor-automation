from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.adapters.gemini_evidence_adapter import GeminiEvidenceAdapter
from src.db import get_connection, initialize_database
from src.domain.case_file import CaseFile
from src.domain.content_package import ContentPackage
from src.domain.errors import DomainValidationError
from src.repositories.sqlite_case_repository import SQLiteCaseRepository
from src.repositories.sqlite_evidence_repository import SQLiteEvidenceRepository
from src.security.providers import CompositeSecretProvider, EnvironmentSecretProvider
from src.services.ad_doctor_content_service import AdDoctorContentService, StructuredContentProvider
from src.services.gemini_production_factory import build_gemini_production_providers
from src.services.gemini_strategic_quality_provider import GeminiStrategicQualityProvider
from src.services.quality_audit_service import QualityAuditService, StrategicQualityProvider
from src.services.research_service import ResearchService
from src.services.structured_gemini_content_provider import StructuredGeminiContentProvider
from src.settings_engine import DEFAULT_CONTENT_WRITER_SETTINGS


URL = "https://www.wpp.com/en/news/2026/open-x"


def research_payload():
    return {
        "case_summary": "Open X is the operating-model focus.",
        "event_facts": [{"statement": "WPP operates Open X for Coca-Cola.", "source_urls": [URL], "confidence": .9}],
        "brand_context": [], "agency_context": [], "consumer_context": [], "category_context": [],
        "cultural_context": [], "competitor_context": [], "historical_context": [], "confirmed_results": [],
        "strategic_signals": [{"statement": "Integration may reduce fragmentation.", "source_urls": [URL], "confidence": .7}],
        "contradictions": [], "unknowns": [{"statement": "Outcome evidence is not available.", "source_urls": [], "confidence": 1}],
        "source_catalog": [{"url": URL, "title": "Open X", "publication": "WPP", "credibility_tier": 1}],
    }


def package_payload(case_id: str, evidence_id: str, *, unsupported=False, opinion="A defensible operating model still needs proof.", prescription="Measure whether integration improves distinctive brand effects."):
    now = datetime.now(UTC).isoformat()
    claims = [
        {"claim_id": "c1", "statement": "WPP operates Open X for Coca-Cola.", "classification": "verified_fact", "evidence_item_ids": ["missing" if unsupported else "evidence_1"], "source_ids": ["source_1_" + sha256(URL.encode()).hexdigest()[:8]], "confidence": .9, "generated_section": "symptoms", "verification_status": "verified"},
        {"claim_id": "c2", "statement": "This suggests integration may reduce fragmentation.", "classification": "strategic_inference", "evidence_item_ids": ["evidence_2"], "source_ids": [], "confidence": .7, "generated_section": "diagnosis"},
        {"claim_id": "c3", "statement": opinion or "Missing opinion", "classification": "doctor_opinion", "evidence_item_ids": ["evidence_1"], "source_ids": [], "confidence": .7, "generated_section": "doctors_opinion"},
    ]
    slide_types = ["diagnostic_hook", "symptoms", "patient_history", "hidden_problem", "diagnosis", "treatment", "what_worked", "risk", "doctors_opinion", "prescription"]
    slides = [{"slide_number": index, "slide_type": value, "headline": value.replace("_", " ").title(), "body": f"Distinct strategic point {index}.", "supporting_claim_ids": [], "supporting_source_ids": []} for index, value in enumerate(slide_types, 1)]
    return {
        "content_package_id": ContentPackage.identity(case_id, 1), "schema_version": "1.0", "package_version": 1,
        "case_id": case_id, "evidence_pack_id": evidence_id, "draft_id": None, "content_type": "article",
        "generation_provider": "ignored", "generation_model": "ignored", "generated_at": now, "created_at": now, "updated_at": now,
        "symptoms": "An appointment signals a wider operating-model shift.", "patient_history": "Global brands have long balanced central control and local execution.",
        "diagnosis": "The problem appears to be fragmented attention and capability.", "treatment": "Integrate social, creator, culture and commerce capability.",
        "results": "Outcome evidence is not available.", "prescription": prescription, "doctors_opinion": opinion,
        "executive_diagnosis": "The appointment is a symptom, not the story.", "business_problem": "Coordination complexity.",
        "marketing_problem": "Fragmented attention.", "consumer_tension": "Discovery may shift toward creators.", "category_tension": "Scale versus relevance.",
        "strategic_rationale": "Integration may improve coordination.", "risks": "Centralisation may weaken local nuance.",
        "missed_opportunities": "No proven measurement model is visible.", "alternative_treatment": "Use a federated specialist model.",
        "final_verdict": "Coherence is useful only if it produces distinctive work.", "carousel": slides,
        "social_caption": "Integration is not an outcome.", "article": "A diagnosis of the operating model.",
        "design_direction": "Editorial diagnostic system.", "claims": claims,
    }


class DynamicModels:
    def __init__(self, case_id: str, evidence_id: str): self.case_id=case_id;self.evidence_id=evidence_id;self.calls=[];self.override_content=None
    def generate_content(self, **kwargs):
        self.calls.append(kwargs);prompt=str(kwargs["contents"])
        usage=SimpleNamespace(prompt_token_count=10,candidates_token_count=20,total_token_count=30)
        if kwargs["config"].get("tools"):
            metadata=SimpleNamespace(grounding_chunks=[SimpleNamespace(web=SimpleNamespace(uri=URL,title="WPP Open X"))],grounding_supports=[],web_search_queries=["WPP Open X"])
            return SimpleNamespace(text=json.dumps(research_payload()),candidates=[SimpleNamespace(grounding_metadata=metadata)],usage_metadata=usage)
        if "generate_ad_doctor_content_package" in prompt:
            return SimpleNamespace(text=json.dumps(self.override_content or package_payload(self.case_id,self.evidence_id)),candidates=[],usage_metadata=usage)
        return SimpleNamespace(text=json.dumps({"dimension_scores":{"diagnosis_depth":92,"doctors_opinion_strength":93,"prescription_usefulness":91},"findings":[],"reviewer_summary":"Strategically specific and evidence disciplined."}),candidates=[],usage_metadata=usage)


class GeminiOnlyProductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Path(self.tmp.name)/"test.db";initialize_database(self.db,PROJECT/"database"/"schema.sql")
        with closing(get_connection(self.db)) as c:c.execute("INSERT INTO topics(id,exact_topic,normalized_topic,status) VALUES(1,'WPP Open X','wpp open x','qualified')");c.commit()
        self.case=SQLiteCaseRepository(self.db).create(CaseFile.for_topic(1,"WPP Open X",workflow_status="content_drafted",qualification_status="qualified"))
        raw=json.dumps(research_payload());self.evidence_id="evidence_gemini_"+sha256(f"{self.case.case_id}|{raw}".encode()).hexdigest()[:20]
        self.models=DynamicModels(self.case.case_id,self.evidence_id);self.client=SimpleNamespace(models=self.models)
        self.secrets=CompositeSecretProvider(EnvironmentSecretProvider({"GEMINI_API_KEY":"fake-gemini-key"}))
    def tearDown(self):self.tmp.cleanup()

    def test_gemini_content_and_quality_satisfy_provider_interfaces(self):
        providers=build_gemini_production_providers(self.db,secret_provider=self.secrets,client=self.client)
        self.assertIsInstance(providers.content,StructuredContentProvider);self.assertIsInstance(providers.quality,StrategicQualityProvider)

    def test_content_request_has_no_search_tool_and_generates_package(self):
        provider=StructuredGeminiContentProvider(api_key="fake",client=self.client)
        raw=provider.generate(case={},evidence={},framework={},configuration={},prompts={})
        self.assertEqual(raw["generation_provider"],"gemini");self.assertNotIn("tools",self.models.calls[-1]["config"])

    def test_quality_request_has_no_search_tool(self):
        provider=GeminiStrategicQualityProvider(api_key="fake",client=self.client)
        result=provider.audit(package={},evidence={},deterministic_findings=[])
        self.assertEqual(result["dimension_scores"]["diagnosis_depth"],92);self.assertNotIn("tools",self.models.calls[-1]["config"])

    def test_only_gemini_key_is_required_and_openai_absence_does_not_block(self):
        providers=build_gemini_production_providers(self.db,secret_provider=self.secrets,client=self.client)
        self.assertEqual(providers.content.provider_name,"gemini");self.assertEqual(DEFAULT_CONTENT_WRITER_SETTINGS["provider_key"],"gemini")

    def test_missing_mandatory_opinion_and_prescription_are_blocked(self):
        for field in ("doctors_opinion","prescription"):
            raw=package_payload(self.case.case_id,self.evidence_id);raw[field]=""
            with self.assertRaisesRegex(DomainValidationError,"Missing mandatory"):
                ContentPackage.from_dict(raw)

    def test_unsupported_fact_is_blocked_by_traceability(self):
        raw=package_payload(self.case.case_id,self.evidence_id,unsupported=True)
        pack=self._research_pack();self.models.override_content=raw
        with self.assertRaisesRegex(DomainValidationError,"unknown evidence IDs"):
            AdDoctorContentService(self.db,StructuredGeminiContentProvider(api_key="fake",client=self.client)).generate(self.case,pack)

    def test_inference_cannot_be_promoted_to_verified_fact(self):
        raw=package_payload(self.case.case_id,self.evidence_id);raw["claims"][0]["evidence_item_ids"]=["evidence_3"]
        pack=self._research_pack();self.models.override_content=raw
        with self.assertRaisesRegex(DomainValidationError,"non-verified evidence as fact"):
            AdDoctorContentService(self.db,StructuredGeminiContentProvider(api_key="fake",client=self.client)).generate(self.case,pack)

    def test_mocked_complete_three_call_gemini_only_orchestration(self):
        providers=build_gemini_production_providers(self.db,secret_provider=self.secrets,client=self.client)
        with mock.patch.object(socket,"create_connection",side_effect=AssertionError("network call")):
            evidence=ResearchService(providers.research,GeminiEvidenceAdapter()).research_case(case_id=self.case.case_id,topic=self.case.canonical_topic,source_articles=[{"article_url":URL}],research_brief="Operating model")
            SQLiteEvidenceRepository(self.db).create(evidence)
            with closing(get_connection(self.db)) as c:c.execute("UPDATE cases SET evidence_pack_id=? WHERE case_id=?",(evidence.evidence_pack_id,self.case.case_id));c.commit()
            self.case=SQLiteCaseRepository(self.db).get_by_case_id(self.case.case_id)
            package=AdDoctorContentService(self.db,providers.content).generate(self.case,evidence)
            review=QualityAuditService(self.db,providers.quality).audit(package,evidence)
        self.assertEqual(len(self.models.calls),3)
        self.assertEqual(self.models.calls[0]["config"]["tools"],[{"google_search":{}}])
        self.assertNotIn("tools",self.models.calls[1]["config"]);self.assertNotIn("tools",self.models.calls[2]["config"])
        self.assertEqual(package.generation_provider,"gemini");self.assertEqual(review.auditor_provider,"gemini")

    def _research_pack(self):
        providers=build_gemini_production_providers(self.db,secret_provider=self.secrets,client=self.client)
        return ResearchService(providers.research,GeminiEvidenceAdapter()).research_case(case_id=self.case.case_id,topic=self.case.canonical_topic,source_articles=[{"article_url":URL}],research_brief="Operating model")


if __name__=="__main__":unittest.main()
