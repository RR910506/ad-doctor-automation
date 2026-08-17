from __future__ import annotations

from src.adapters.gemini_evidence_adapter import GeminiEvidenceAdapter
from src.adapters.gemini_research_provider import ResearchProvider
from src.domain.evidence_pack import EvidencePack


class ResearchService:
    """Provider-independent orchestration for one selected Case research run."""

    def __init__(self, provider: ResearchProvider, evidence_adapter: GeminiEvidenceAdapter) -> None:
        self.provider = provider
        self.evidence_adapter = evidence_adapter

    def research_case(
        self,
        *,
        case_id: str,
        topic: str,
        source_articles: list[dict[str, object]],
        research_brief: str,
    ) -> EvidencePack:
        result = self.provider.research(topic=topic, source_articles=source_articles, research_brief=research_brief)
        qualifying_urls = {str(item.get("article_url") or "") for item in source_articles if item.get("article_url")}
        return self.evidence_adapter.build(case_id=case_id, topic=topic, result=result, qualifying_urls=qualifying_urls)
