from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from src.domain.errors import DomainValidationError
class ApprovalStatus(StrEnum):
    NOT_READY="not_ready"; PENDING_REVIEW="pending_review"; REVISION_REQUESTED="revision_requested"; APPROVED="approved"; REJECTED="rejected"; SUPERSEDED="superseded"
class ApprovalDecision(StrEnum):
    SUBMIT_FOR_REVIEW="submit_for_review"; REQUEST_REVISION="request_revision"; APPROVE="approve"; REJECT="reject"; SUPERSEDE="supersede"
class ApprovalActorType(StrEnum): EDITOR="editor"; ADMINISTRATOR="administrator"; SYSTEM="system"; MIGRATION="migration"
ALLOWED_APPROVAL_TRANSITIONS={
 ApprovalStatus.NOT_READY:{ApprovalStatus.PENDING_REVIEW}, ApprovalStatus.PENDING_REVIEW:{ApprovalStatus.APPROVED,ApprovalStatus.REVISION_REQUESTED,ApprovalStatus.REJECTED},
 ApprovalStatus.REVISION_REQUESTED:{ApprovalStatus.PENDING_REVIEW},ApprovalStatus.APPROVED:{ApprovalStatus.SUPERSEDED},ApprovalStatus.REJECTED:{ApprovalStatus.PENDING_REVIEW},ApprovalStatus.SUPERSEDED:set()}
@dataclass(frozen=True,slots=True)
class ApprovalEvent:
    approval_event_id:str; idempotency_key:str; case_id:str; draft_id:str; previous_status:ApprovalStatus; new_status:ApprovalStatus; decision:ApprovalDecision; reviewer_id:str; actor_type:ApprovalActorType; reviewer_notes:str|None; requested_corrections:str|None; created_at:datetime; case_version:int; originating_run_id:str|None; metadata:dict[str,object]
    def __post_init__(self):
        if self.actor_type in {ApprovalActorType.EDITOR,ApprovalActorType.ADMINISTRATOR} and not self.reviewer_id.strip(): raise DomainValidationError("Reviewer identity is required")
        if self.decision in {ApprovalDecision.REJECT,ApprovalDecision.REQUEST_REVISION} and not (self.reviewer_notes or self.requested_corrections): raise DomainValidationError("Rejection and revision require a reason")

