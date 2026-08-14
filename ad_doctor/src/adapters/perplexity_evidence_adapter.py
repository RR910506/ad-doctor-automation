from __future__ import annotations

import re
from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.domain.evidence_pack import EvidenceItem, EvidencePack, EvidenceSource, VerificationStatus


def normalize_evidence_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


class PerplexityEvidenceAdapter:
    """Translate an existing dossier/provider result; never performs provider calls."""

    def build(self, *, case_id: str, dossier: dict[str, object], qualifying_urls: set[str] | None = None) -> EvidencePack:
        payload = dict(dossier.get("perplexity_research") or {})
        topic = str(dossier.get("topic") or "").strip()
        if not topic:
            raise ValueError("Research dossier has no canonical topic")
        qualifying = {normalize_evidence_url(url) for url in qualifying_urls or set()}
        source_by_url: dict[str, EvidenceSource] = {}
        for article in list(dossier.get("source_articles") or []):
            url = normalize_evidence_url(str(article.get("article_url") or ""))
            if not url:
                continue
            source_by_url[url] = self._source(url, str(article.get("source_name") or "Approved publication"), str(article.get("headline") or topic), article.get("published_date"), url in qualifying or not qualifying)
        for raw_url in list(payload.get("source_links") or []):
            url = normalize_evidence_url(str(raw_url))
            if url and url not in source_by_url:
                source_by_url[url] = self._source(url, urlsplit(url).netloc, topic, None, url in qualifying)
        sources = tuple(source_by_url.values())
        source_ids = {source.url: source.source_id for source in sources}
        verified: list[EvidenceItem] = []
        for index, point in enumerate(list(payload.get("verified_data_points") or []), start=1):
            if not isinstance(point, dict):
                raise ValueError("verified_data_points must contain objects")
            statement = str(point.get("claim") or point.get("statement") or "").strip()
            reference = normalize_evidence_url(str(point.get("source") or point.get("url") or ""))
            if statement and reference in source_ids:
                verified.append(EvidenceItem(f"verified-{index}", statement, "verified_fact", (source_ids[reference],), .9, VerificationStatus.VERIFIED))
            elif statement:
                raise ValueError(f"Verified fact has no catalogued supporting source: {statement}")
        groups: dict[str, tuple[EvidenceItem, ...]] = {}
        mapping = {
            "brand_context":"brand_strategy", "category_context":"market_context", "consumer_context":"consumer_shift",
            "competitor_context":"competitor_context", "business_problem_candidates":"business_impact",
            "campaign_treatment":"background_context", "industry_reaction":"cultural_relevance", "strategic_signals":"marketing_lesson",
        }
        for target, source_key in mapping.items():
            text = str(payload.get(source_key) or "").strip()
            groups[target] = () if not text or text.lower() == "data unavailable" else (EvidenceItem(f"{target}-1", text, "source_claim", (), .5, VerificationStatus.UNVERIFIED),)
        summary = str(payload.get("summary") or dossier.get("research_summary") or "").strip()
        pack_id = "evidence_" + sha256(f"{case_id}|{topic}|{datetime.now(UTC).isoformat()}".encode()).hexdigest()[:24]
        return EvidencePack(schema_version="1.0", evidence_pack_id=pack_id, case_id=case_id, canonical_topic=topic, research_provider="perplexity" if payload.get("status") == "completed" else str(payload.get("model") or "approved_source_only"), research_model=str(payload.get("model") or "unknown"), research_completed_at=datetime.now(UTC), source_catalog=sources, verified_facts=tuple(verified), source_claims=(), contradictions=(), unknowns=(), results_evidence=(), audience_reaction=(), research_summary=summary, **groups)

    @staticmethod
    def _source(url: str, publication: str, title: str, published_at: object, qualifying: bool) -> EvidenceSource:
        source_id = "source_" + sha256(url.encode()).hexdigest()[:20]
        return EvidenceSource(source_id, publication, title, url, str(published_at) if published_at else None, datetime.now(UTC).isoformat(), "publication", 1, qualifying)

