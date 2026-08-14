from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from src.domain.errors import DomainValidationError


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    source_id: str
    publication: str
    title: str
    url: str
    published_at: str | None
    retrieved_at: str
    source_type: str
    credibility_tier: int
    is_qualifying_publication: bool

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.publication.strip() or not self.url.strip():
            raise DomainValidationError("Evidence sources require source_id, publication and URL")
        if self.credibility_tier < 0:
            raise DomainValidationError("credibility_tier cannot be negative")


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_id: str
    statement: str
    evidence_type: str
    source_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    notes: str | None = None

    def __post_init__(self) -> None:
        if not self.evidence_id.strip() or not self.statement.strip() or not self.evidence_type.strip():
            raise DomainValidationError("Evidence items require ID, statement and type")
        if not 0 <= self.confidence <= 1:
            raise DomainValidationError("confidence must be between 0 and 1")
        if self.verification_status == VerificationStatus.VERIFIED and not self.source_ids:
            raise DomainValidationError("Verified evidence must have at least one supporting source")


@dataclass(frozen=True, slots=True)
class EvidencePack:
    schema_version: str
    evidence_pack_id: str
    case_id: str
    canonical_topic: str
    research_provider: str
    research_model: str
    research_completed_at: datetime
    source_catalog: tuple[EvidenceSource, ...]
    verified_facts: tuple[EvidenceItem, ...] = ()
    source_claims: tuple[EvidenceItem, ...] = ()
    brand_context: tuple[EvidenceItem, ...] = ()
    category_context: tuple[EvidenceItem, ...] = ()
    consumer_context: tuple[EvidenceItem, ...] = ()
    competitor_context: tuple[EvidenceItem, ...] = ()
    business_problem_candidates: tuple[EvidenceItem, ...] = ()
    campaign_treatment: tuple[EvidenceItem, ...] = ()
    results_evidence: tuple[EvidenceItem, ...] = ()
    audience_reaction: tuple[EvidenceItem, ...] = ()
    industry_reaction: tuple[EvidenceItem, ...] = ()
    contradictions: tuple[EvidenceItem, ...] = ()
    unknowns: tuple[EvidenceItem, ...] = ()
    strategic_signals: tuple[EvidenceItem, ...] = ()
    research_summary: str = ""

    def __post_init__(self) -> None:
        if not all((self.schema_version.strip(), self.evidence_pack_id.strip(), self.case_id.strip(), self.canonical_topic.strip())):
            raise DomainValidationError("Evidence Pack schema version, IDs and canonical topic are required")
        ids = [source.source_id for source in self.source_catalog]
        if len(ids) != len(set(ids)):
            raise DomainValidationError("Duplicate source IDs are not allowed")
        known = set(ids)
        for item in self.all_items():
            missing = set(item.source_ids) - known
            if missing:
                raise DomainValidationError(f"Evidence {item.evidence_id} references missing source IDs: {sorted(missing)}")
        for fact in self.verified_facts:
            if fact.verification_status != VerificationStatus.VERIFIED or not fact.source_ids:
                raise DomainValidationError("verified_facts must be verified and supported")
        for unknown in self.unknowns:
            if unknown.verification_status == VerificationStatus.VERIFIED:
                raise DomainValidationError("Unknowns cannot be marked as verified")

    def all_items(self) -> tuple[EvidenceItem, ...]:
        groups = (self.verified_facts, self.source_claims, self.brand_context, self.category_context, self.consumer_context, self.competitor_context, self.business_problem_candidates, self.campaign_treatment, self.results_evidence, self.audience_reaction, self.industry_reaction, self.contradictions, self.unknowns, self.strategic_signals)
        return tuple(item for group in groups for item in group)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["research_completed_at"] = self.research_completed_at.isoformat()
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, tuple) and value and isinstance(value[0], EvidenceItem):
                result[name] = [{**asdict(item), "verification_status": item.verification_status.value, "source_ids": list(item.source_ids)} for item in value]
        result["source_catalog"] = [asdict(source) for source in self.source_catalog]
        return result

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvidencePack":
        source_catalog = tuple(EvidenceSource(**item) for item in payload.get("source_catalog", []))
        item_fields = set(cls.__dataclass_fields__) - {"schema_version", "evidence_pack_id", "case_id", "canonical_topic", "research_provider", "research_model", "research_completed_at", "source_catalog", "research_summary"}
        groups = {}
        for name in item_fields:
            groups[name] = tuple(EvidenceItem(**{**item, "source_ids": tuple(item.get("source_ids", [])), "verification_status": VerificationStatus(item.get("verification_status", "unverified"))}) for item in payload.get(name, []))
        return cls(schema_version=payload["schema_version"], evidence_pack_id=payload["evidence_pack_id"], case_id=payload["case_id"], canonical_topic=payload["canonical_topic"], research_provider=payload["research_provider"], research_model=payload["research_model"], research_completed_at=datetime.fromisoformat(payload["research_completed_at"]), source_catalog=source_catalog, research_summary=payload.get("research_summary", ""), **groups)
