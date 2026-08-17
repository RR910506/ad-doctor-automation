import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.adapters.gemini_evidence_adapter import GeminiEvidenceAdapter
from src.adapters.gemini_research_provider import GEMINI_MODEL, GeminiResearchProvider, GroundedSource, NormalizedResearchResult, ResearchProvider
from src.domain.errors import DomainValidationError
from src.domain.evidence_pack import EvidencePack
from src.security.providers import EnvironmentSecretProvider
from src.security.secret_provider import MissingSecretError
from src.services.research_service import ResearchService
from src.settings_engine import DEFAULT_RESEARCH_SETTINGS, get_research_engine_by_key


URL = "https://www.wpp.com/en/news/2026/open-x"


def payload(*, fact_urls=None, duplicate=False):
    sources = [{"url": URL, "title": "Open X", "publication": "WPP", "credibility_tier": 1}]
    if duplicate:
        sources.append({"url": URL + "#section", "title": "Open X duplicate", "publication": "WPP"})
    return {
        "case_summary": "An operating-model investigation.",
        "event_facts": [{"statement": "WPP operates Open X for Coca-Cola.", "source_urls": fact_urls if fact_urls is not None else [URL], "confidence": .9}],
        "agency_context": [{"statement": "WPP describes Open X as integrated.", "source_urls": [URL], "confidence": .8}],
        "brand_context": [], "consumer_context": [], "category_context": [], "cultural_context": [],
        "competitor_context": [], "historical_context": [], "confirmed_results": [],
        "strategic_signals": [{"statement": "Integration may reduce fragmentation.", "source_urls": [], "confidence": .6}],
        "contradictions": [], "unknowns": [{"statement": "Outcome evidence is not yet available.", "source_urls": [], "confidence": 1}],
        "source_catalog": sources,
    }


class FakeModels:
    def __init__(self, response): self.response=response; self.calls=[]
    def generate_content(self, **kwargs): self.calls.append(kwargs); return self.response


def response(data=None):
    metadata = SimpleNamespace(
        grounding_chunks=[SimpleNamespace(web=SimpleNamespace(uri=URL, title="WPP Open X")), SimpleNamespace(web=SimpleNamespace(uri=URL + "#section", title="Duplicate"))],
        grounding_supports=[SimpleNamespace(segment=SimpleNamespace(text="supported"), grounding_chunk_indices=[0])],
        web_search_queries=["WPP Open X Coca-Cola"],
    )
    return SimpleNamespace(text=json.dumps(data or payload()), candidates=[SimpleNamespace(grounding_metadata=metadata)], usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=20, total_token_count=30))


class GeminiResearchTest(unittest.TestCase):
    def provider(self, data=None):
        models=FakeModels(response(data)); return GeminiResearchProvider(api_key="test-secret", client=SimpleNamespace(models=models)),models

    def test_provider_implements_research_interface(self):
        provider,_=self.provider(); self.assertIsInstance(provider, ResearchProvider)

    def test_secure_key_resolution(self):
        result=EnvironmentSecretProvider({"GEMINI_API_KEY":" key "}).resolve("GEMINI_API_KEY")
        self.assertTrue(result.configured); self.assertEqual(result.value,"key")

    def test_missing_key_is_safe(self):
        with self.assertRaisesRegex(MissingSecretError,"GEMINI_API_KEY is not configured"): GeminiResearchProvider(api_key="")

    def test_key_never_appears_in_provider_error(self):
        class BadModels:
            def generate_content(self,**kwargs): raise RuntimeError("request failed with secret-value")
        provider=GeminiResearchProvider(api_key="secret-value",client=SimpleNamespace(models=BadModels()))
        with self.assertRaises(RuntimeError) as caught: provider.research(topic="t",source_articles=[],research_brief="b")
        self.assertNotIn("secret-value",str(caught.exception))

    def test_search_grounding_is_enabled(self):
        provider,models=self.provider(); provider.research(topic="t",source_articles=[],research_brief="b")
        self.assertEqual(models.calls[0]["config"]["tools"],[{"google_search": {}}])

    def test_grounding_metadata_is_parsed(self):
        provider,_=self.provider(); result=provider.research(topic="t",source_articles=[],research_brief="b")
        self.assertEqual(result.grounding_metadata["web_search_queries"],["WPP Open X Coca-Cola"]);self.assertEqual(result.usage["total_token_count"],30)

    def test_sources_are_deduplicated(self):
        provider,_=self.provider(payload(duplicate=True));result=provider.research(topic="t",source_articles=[],research_brief="b")
        self.assertEqual(len(result.grounded_sources),1)

    def test_evidence_pack_conversion_and_metadata(self):
        provider,_=self.provider(); result=provider.research(topic="t",source_articles=[],research_brief="b")
        pack=GeminiEvidenceAdapter().build(case_id="case",topic="topic",result=result,qualifying_urls={URL})
        self.assertEqual(pack.research_provider,"gemini");self.assertEqual(pack.research_model,GEMINI_MODEL);self.assertEqual(len(pack.verified_facts),1);self.assertTrue(pack.source_catalog[0].is_qualifying_publication)

    def test_unsupported_fact_is_rejected(self):
        provider,_=self.provider(payload(fact_urls=[]));result=provider.research(topic="t",source_articles=[],research_brief="b")
        with self.assertRaisesRegex(DomainValidationError,"Unsupported factual claim"): GeminiEvidenceAdapter().build(case_id="case",topic="topic",result=result)

    def test_historical_perplexity_pack_remains_readable(self):
        provider,_=self.provider(); result=provider.research(topic="t",source_articles=[],research_brief="b")
        value=GeminiEvidenceAdapter().build(case_id="case",topic="topic",result=result).to_dict();value["research_provider"]="perplexity"
        self.assertEqual(EvidencePack.from_dict(value).research_provider,"perplexity")

    def test_gemini_is_default(self):
        self.assertEqual(DEFAULT_RESEARCH_SETTINGS["provider_key"],"gemini");self.assertEqual(DEFAULT_RESEARCH_SETTINGS["model"],GEMINI_MODEL);self.assertEqual(get_research_engine_by_key("gemini")["env_var"],"GEMINI_API_KEY")

    def test_perplexity_key_is_not_mandatory(self):
        self.assertTrue(EnvironmentSecretProvider({"GEMINI_API_KEY":"key"}).resolve("GEMINI_API_KEY").configured)

    def test_no_ui_page_calls_gemini_client(self):
        for path in (PROJECT_DIR/"ui"/"pages").glob("*.py"):
            text=path.read_text(encoding="utf-8");self.assertNotIn("generate_content(",text);self.assertNotIn("google.genai",text)

    def test_mocked_research_service_integration(self):
        provider,_=self.provider(); service=ResearchService(provider,GeminiEvidenceAdapter())
        pack=service.research_case(case_id="case",topic="topic",source_articles=[{"article_url":URL}],research_brief="brief")
        self.assertEqual(pack.research_provider,"gemini");self.assertEqual(len(pack.verified_facts),1)

    def test_response_requires_valid_json(self):
        bad=response();bad.text="not-json";models=FakeModels(bad);provider=GeminiResearchProvider(api_key="key",client=SimpleNamespace(models=models))
        with self.assertRaisesRegex(ValueError,"invalid structured research JSON"):provider.research(topic="t",source_articles=[],research_brief="b")


if __name__ == "__main__": unittest.main()
