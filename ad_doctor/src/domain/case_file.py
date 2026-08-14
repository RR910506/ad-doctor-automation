from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import NAMESPACE_URL, uuid5

from src.domain.errors import DomainValidationError


class ContentType(StrEnum):
    UNCLASSIFIED = "unclassified"
    CAMPAIGN_SURGERY = "campaign_surgery"
    MARKETING_DIAGNOSIS = "marketing_diagnosis"
    BRAND_CHECKUP = "brand_checkup"
    EMERGENCY_ROOM = "emergency_room"
    MARKETING_PRESCRIPTION = "marketing_prescription"
    LEGENDS = "legends"


def utc_now() -> datetime:
    return datetime.now(UTC)


def stable_case_id(topic_id: int | str) -> str:
    """Return the same opaque Case File identifier for the same topic identifier."""
    value = str(topic_id).strip()
    if not value:
        raise DomainValidationError("topic_id is required")
    return f"case_{uuid5(NAMESPACE_URL, f'ad-doctor/topic/{value}').hex}"


@dataclass(frozen=True, slots=True)
class CaseFile:
    case_id: str
    topic_id: int
    canonical_topic: str
    workflow_status: str
    qualification_status: str
    brand: str | None = None
    campaign: str | None = None
    content_type: ContentType = ContentType.UNCLASSIFIED
    unique_publication_count: int = 0
    qualification_reason: str | None = None
    rejection_reason: str | None = None
    priority_score: float | None = None
    current_run_id: str | None = None
    evidence_pack_id: str | None = None
    draft_id: str | None = None
    quality_status: str = "pending"
    approval_status: str = "pending"
    publication_status: str = "not_published"
    created_at: datetime = None  # type: ignore[assignment]
    updated_at: datetime = None  # type: ignore[assignment]
    version: int = 1

    def __post_init__(self) -> None:
        now = utc_now()
        if self.created_at is None:
            object.__setattr__(self, "created_at", now)
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", now)
        if not self.case_id.strip() or self.topic_id <= 0:
            raise DomainValidationError("case_id and a positive topic_id are required")
        if not self.canonical_topic.strip():
            raise DomainValidationError("canonical_topic is required")
        if self.unique_publication_count < 0:
            raise DomainValidationError("unique_publication_count cannot be negative")
        if self.version < 1:
            raise DomainValidationError("version must be at least 1")
        for name in ("workflow_status", "qualification_status", "quality_status", "approval_status", "publication_status"):
            if not str(getattr(self, name)).strip():
                raise DomainValidationError(f"{name} is required")

    @classmethod
    def for_topic(cls, topic_id: int, canonical_topic: str, **values: object) -> "CaseFile":
        return cls(case_id=stable_case_id(topic_id), topic_id=topic_id, canonical_topic=canonical_topic, **values)

    def with_changes(self, **changes: object) -> "CaseFile":
        """Return an updated immutable instance; repositories increment versions."""
        return replace(self, updated_at=utc_now(), **changes)

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["content_type"] = self.content_type.value
        result["created_at"] = self.created_at.isoformat()
        result["updated_at"] = self.updated_at.isoformat()
        return result

