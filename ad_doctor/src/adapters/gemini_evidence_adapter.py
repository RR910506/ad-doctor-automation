from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import urldefrag

from src.adapters.gemini_research_provider import NormalizedResearchResult
from src.domain.errors import DomainValidationError
from src.domain.evidence_pack import EvidenceItem, EvidencePack, EvidenceSource, VerificationStatus


class GeminiEvidenceAdapter:
    """Convert grounded Gemini output into the provider-independent Evidence Pack."""

    def build(self, *, case_id: str, topic: str, result: NormalizedResearchResult, qualifying_urls: set[str] | None = None) -> EvidencePack:
        payload = result.structured
        catalog_payload = payload.get("source_catalog", []) if isinstance(payload.get("source_catalog"), list) else []
        by_url = {self._url(source.url): source for source in result.grounded_sources}
        metadata_by_url = {self._url(item.get("url")): item for item in catalog_payload if isinstance(item, dict) and self._url(item.get("url"))}
        qualifying = {self._url(url) for url in (qualifying_urls or set())}
        sources = []
        url_to_id = {}
        for index, (url, grounded) in enumerate(by_url.items(), start=1):
            meta = metadata_by_url.get(url, {})
            source_id = f"source_{index}_{sha256(url.encode()).hexdigest()[:8]}"
            url_to_id[url] = source_id
            sources.append(EvidenceSource(
                source_id=source_id,
                publication=str(meta.get("publication") or grounded.title or "Web source"),
                title=str(meta.get("title") or grounded.title or "Grounded web source"),
                url=url,
                published_at=str(meta.get("published_at")) if meta.get("published_at") else None,
                retrieved_at=datetime.now(UTC).isoformat(),
                source_type=str(meta.get("source_type") or "grounded_web"),
                credibility_tier=max(0, int(meta.get("credibility_tier") or 2)),
                is_qualifying_publication=url in qualifying,
            ))
        if not sources:
            raise DomainValidationError("Grounded Gemini research produced no source catalog")

        counter = 0
        def items(field: str, evidence_type: str, status: VerificationStatus, *, require_sources: bool = False):
            nonlocal counter
            output = []
            raw_items = payload.get(field, [])
            if not isinstance(raw_items, list):
                return tuple()
            for raw in raw_items:
                if not isinstance(raw, dict) or not str(raw.get("statement") or "").strip():
                    continue
                refs = tuple(dict.fromkeys(url_to_id[url] for url in (self._url(v) for v in raw.get("source_urls", []) or []) if url in url_to_id))
                if require_sources and not refs:
                    raise DomainValidationError(f"Unsupported factual claim in {field}")
                counter += 1
                output.append(EvidenceItem(
                    evidence_id=f"evidence_{counter}", statement=str(raw["statement"]).strip(), evidence_type=evidence_type,
                    source_ids=refs, confidence=max(0.0, min(1.0, float(raw.get("confidence") or 0))),
                    verification_status=status, notes=str(raw.get("notes")) if raw.get("notes") else None,
                ))
            return tuple(output)

        pack_id = "evidence_gemini_" + sha256(f"{case_id}|{result.final_text}".encode()).hexdigest()[:20]
        return EvidencePack(
            schema_version="1.0", evidence_pack_id=pack_id, case_id=case_id, canonical_topic=topic,
            research_provider="gemini", research_model=result.model, research_completed_at=datetime.now(UTC),
            source_catalog=tuple(sources), verified_facts=items("event_facts", "verified_fact", VerificationStatus.VERIFIED, require_sources=True),
            source_claims=items("agency_context", "source_claim", VerificationStatus.UNVERIFIED),
            brand_context=items("brand_context", "brand_context", VerificationStatus.UNVERIFIED),
            category_context=items("category_context", "category_context", VerificationStatus.UNVERIFIED),
            consumer_context=items("consumer_context", "consumer_context", VerificationStatus.UNVERIFIED),
            competitor_context=items("competitor_context", "competitor_context", VerificationStatus.UNVERIFIED),
            results_evidence=items("confirmed_results", "verified_result", VerificationStatus.VERIFIED, require_sources=True),
            industry_reaction=items("cultural_context", "cultural_context", VerificationStatus.UNVERIFIED),
            contradictions=items("contradictions", "contradiction", VerificationStatus.DISPUTED),
            unknowns=items("unknowns", "unknown", VerificationStatus.UNKNOWN),
            strategic_signals=items("strategic_signals", "strategic_inference", VerificationStatus.UNVERIFIED),
            research_summary=str(payload.get("case_summary") or "").strip(),
        )

    @staticmethod
    def _url(value: object) -> str:
        return urldefrag(str(value or "").strip())[0].rstrip("/")
