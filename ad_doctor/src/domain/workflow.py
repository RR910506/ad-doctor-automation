from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Mapping, Protocol
from uuid import uuid4

from src.domain.case_file import CaseFile
from src.domain.errors import InvalidWorkflowTransition


class WorkflowStatus(StrEnum):
    DISCOVERED = "discovered"
    QUALIFICATION_PENDING = "qualification_pending"
    REJECTED = "rejected"
    QUALIFIED = "qualified"
    RESEARCH_PENDING = "research_pending"
    RESEARCHING = "researching"
    RESEARCH_COMPLETE = "research_complete"
    CONTENT_PENDING = "content_pending"
    CONTENT_DRAFTED = "content_drafted"
    QUALITY_REVIEW_PENDING = "quality_review_pending"
    QUALITY_REVIEW_FAILED = "quality_review_failed"
    EDITORIAL_REVIEW_PENDING = "editorial_review_pending"
    REVISION_REQUESTED = "revision_requested"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    EMAIL_PENDING = "email_pending"
    EMAILED = "emailed"
    FAILED = "failed"
    ARCHIVED = "archived"


class ActorType(StrEnum):
    SYSTEM = "system"
    EDITOR = "editor"
    ADMINISTRATOR = "administrator"
    MIGRATION = "migration"


ALLOWED_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.DISCOVERED: frozenset({WorkflowStatus.QUALIFICATION_PENDING, WorkflowStatus.FAILED}),
    WorkflowStatus.QUALIFICATION_PENDING: frozenset({WorkflowStatus.QUALIFIED, WorkflowStatus.REJECTED, WorkflowStatus.FAILED}),
    WorkflowStatus.REJECTED: frozenset({WorkflowStatus.QUALIFICATION_PENDING, WorkflowStatus.ARCHIVED}),
    WorkflowStatus.QUALIFIED: frozenset({WorkflowStatus.RESEARCH_PENDING, WorkflowStatus.FAILED}),
    WorkflowStatus.RESEARCH_PENDING: frozenset({WorkflowStatus.RESEARCHING, WorkflowStatus.FAILED}),
    WorkflowStatus.RESEARCHING: frozenset({WorkflowStatus.RESEARCH_COMPLETE, WorkflowStatus.FAILED}),
    WorkflowStatus.RESEARCH_COMPLETE: frozenset({WorkflowStatus.CONTENT_PENDING, WorkflowStatus.FAILED}),
    WorkflowStatus.CONTENT_PENDING: frozenset({WorkflowStatus.CONTENT_DRAFTED, WorkflowStatus.FAILED}),
    WorkflowStatus.CONTENT_DRAFTED: frozenset({WorkflowStatus.QUALITY_REVIEW_PENDING, WorkflowStatus.EDITORIAL_REVIEW_PENDING, WorkflowStatus.FAILED}),
    WorkflowStatus.QUALITY_REVIEW_PENDING: frozenset({WorkflowStatus.QUALITY_REVIEW_FAILED, WorkflowStatus.EDITORIAL_REVIEW_PENDING, WorkflowStatus.FAILED}),
    WorkflowStatus.QUALITY_REVIEW_FAILED: frozenset({WorkflowStatus.QUALITY_REVIEW_PENDING, WorkflowStatus.REVISION_REQUESTED, WorkflowStatus.CONTENT_PENDING, WorkflowStatus.ARCHIVED}),
    WorkflowStatus.EDITORIAL_REVIEW_PENDING: frozenset({WorkflowStatus.APPROVED, WorkflowStatus.REVISION_REQUESTED, WorkflowStatus.ARCHIVED}),
    WorkflowStatus.REVISION_REQUESTED: frozenset({WorkflowStatus.CONTENT_PENDING, WorkflowStatus.EDITORIAL_REVIEW_PENDING, WorkflowStatus.ARCHIVED}),
    # A newly stored draft may reopen editorial review without erasing the
    # previously approved draft's immutable approval history.
    WorkflowStatus.APPROVED: frozenset({WorkflowStatus.EDITORIAL_REVIEW_PENDING, WorkflowStatus.PUBLISHING, WorkflowStatus.ARCHIVED}),
    WorkflowStatus.PUBLISHING: frozenset({WorkflowStatus.PUBLISHED, WorkflowStatus.FAILED}),
    WorkflowStatus.PUBLISHED: frozenset({WorkflowStatus.EMAIL_PENDING, WorkflowStatus.ARCHIVED}),
    WorkflowStatus.EMAIL_PENDING: frozenset({WorkflowStatus.EMAILED, WorkflowStatus.FAILED}),
    WorkflowStatus.EMAILED: frozenset({WorkflowStatus.ARCHIVED}),
    WorkflowStatus.FAILED: frozenset({WorkflowStatus.QUALIFICATION_PENDING, WorkflowStatus.RESEARCH_PENDING, WorkflowStatus.CONTENT_PENDING, WorkflowStatus.QUALITY_REVIEW_PENDING, WorkflowStatus.PUBLISHING, WorkflowStatus.EMAIL_PENDING, WorkflowStatus.ARCHIVED}),
    WorkflowStatus.ARCHIVED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class WorkflowTransition:
    transition_id: str
    case_id: str
    from_status: WorkflowStatus
    to_status: WorkflowStatus
    actor_type: ActorType
    actor_id: str
    reason: str
    occurred_at: datetime
    metadata: Mapping[str, object]
    failed_stage: WorkflowStatus | None = None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        for key in ("from_status", "to_status", "actor_type", "failed_stage"):
            value = result[key]
            result[key] = value.value if isinstance(value, StrEnum) else value
        result["occurred_at"] = self.occurred_at.isoformat()
        result["metadata"] = dict(self.metadata)
        return result


class TransitionStore(Protocol):
    def append_transition(self, transition: WorkflowTransition) -> WorkflowTransition: ...


class WorkflowService:
    """Validate state changes and append an immutable audit event."""

    def __init__(self, transition_store: TransitionStore) -> None:
        self.transition_store = transition_store

    def transition(self, case: CaseFile, to_status: WorkflowStatus, *, actor_type: ActorType, actor_id: str, reason: str, metadata: Mapping[str, object] | None = None) -> tuple[CaseFile, WorkflowTransition | None]:
        current = WorkflowStatus(case.workflow_status)
        if current == to_status:
            return case, None
        if to_status not in ALLOWED_TRANSITIONS[current]:
            raise InvalidWorkflowTransition(f"Cannot transition from {current.value} to {to_status.value}")
        if not actor_id.strip() or not reason.strip():
            raise InvalidWorkflowTransition("actor_id and reason are required")
        event = WorkflowTransition(
            transition_id=f"transition_{uuid4().hex}", case_id=case.case_id,
            from_status=current, to_status=to_status, actor_type=actor_type,
            actor_id=actor_id.strip(), reason=reason.strip(), occurred_at=datetime.now(UTC),
            metadata=dict(metadata or {}), failed_stage=current if to_status == WorkflowStatus.FAILED else None,
        )
        self.transition_store.append_transition(event)
        return case.with_changes(workflow_status=to_status.value), event
