from __future__ import annotations

import hashlib
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.db import get_connection
from src.domain.approval import (
    ALLOWED_APPROVAL_TRANSITIONS,
    ApprovalActorType,
    ApprovalDecision,
    ApprovalEvent,
    ApprovalStatus,
)
from src.domain.errors import DomainValidationError, EntityNotFoundError, VersionConflictError
from src.domain.workflow import ActorType, WorkflowStatus
from src.repositories.sqlite_approval_repository import SQLiteApprovalRepository
from src.services.case_lifecycle_service import CaseLifecycleService


DECISION_TARGET = {
    ApprovalDecision.SUBMIT_FOR_REVIEW: ApprovalStatus.PENDING_REVIEW,
    ApprovalDecision.REQUEST_REVISION: ApprovalStatus.REVISION_REQUESTED,
    ApprovalDecision.APPROVE: ApprovalStatus.APPROVED,
    ApprovalDecision.REJECT: ApprovalStatus.REJECTED,
    ApprovalDecision.SUPERSEDE: ApprovalStatus.SUPERSEDED,
}


class EditorialApprovalService:
    """Atomically update Case workflow, supersession, and approval history."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lifecycle = CaseLifecycleService(self.path)
        self.events = SQLiteApprovalRepository(self.path)

    def decide(
        self,
        case_id: str,
        draft_id: str,
        decision: ApprovalDecision,
        *,
        reviewer_id: str,
        actor_type: ApprovalActorType = ApprovalActorType.EDITOR,
        notes: str | None = None,
        corrections: str | None = None,
        run_id: str | None = None,
        expected_case_version: int | None = None,
        expected_previous_status: ApprovalStatus | None = None,
        idempotency_key: str | None = None,
    ) -> ApprovalEvent:
        key = idempotency_key or hashlib.sha256(
            f"{case_id}|{draft_id}|{decision.value}|{reviewer_id}|{notes}|{corrections}".encode()
        ).hexdigest()
        with closing(get_connection(self.path)) as con:
            existing = con.execute(
                "SELECT * FROM approval_events WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                return self.events._row(existing)
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
                if not row:
                    raise EntityNotFoundError(f"Case {case_id} not found")
                case = self.lifecycle.cases._from_row(row)
                if expected_case_version is not None and case.version != expected_case_version:
                    raise VersionConflictError(
                        f"Case {case_id} version {expected_case_version} is stale; current version is {case.version}"
                    )
                draft = con.execute(
                    "SELECT co.id,co.created_at,lt.topic_id FROM content_outputs co "
                    "JOIN locked_topics lt ON lt.id=co.locked_topic_id WHERE co.id=?",
                    (int(draft_id),),
                ).fetchone()
                if not draft:
                    raise EntityNotFoundError("Stored draft not found")
                if int(draft["topic_id"]) != case.topic_id:
                    raise DomainValidationError("Draft belongs to a different Case")
                latest = con.execute(
                    "SELECT * FROM approval_events WHERE case_id=? AND draft_id=? ORDER BY id DESC LIMIT 1",
                    (case_id, str(draft_id)),
                ).fetchone()
                previous = ApprovalStatus(latest["new_status"]) if latest else ApprovalStatus.NOT_READY
                if expected_previous_status is not None and previous != expected_previous_status:
                    raise VersionConflictError(
                        f"Approval state {expected_previous_status.value} is stale; current state is {previous.value}"
                    )
                target = DECISION_TARGET[decision]
                if target not in ALLOWED_APPROVAL_TRANSITIONS[previous]:
                    raise DomainValidationError(
                        f"Invalid approval transition {previous.value} to {target.value}"
                    )

                if decision == ApprovalDecision.APPROVE:
                    case = self._supersede_current(con, case, draft, reviewer_id, actor_type, run_id)

                event = self._event(
                    key, case, str(draft_id), previous, target, decision,
                    reviewer_id, actor_type, notes, corrections, run_id,
                )
                workflow_target = {
                    ApprovalStatus.PENDING_REVIEW: WorkflowStatus.EDITORIAL_REVIEW_PENDING,
                    ApprovalStatus.REVISION_REQUESTED: WorkflowStatus.REVISION_REQUESTED,
                    ApprovalStatus.APPROVED: WorkflowStatus.APPROVED,
                }.get(target)
                if workflow_target:
                    case = self.lifecycle._transition(
                        con, case, workflow_target, self._workflow_actor(actor_type),
                        reviewer_id or "system", f"Approval decision: {decision.value}",
                        run_id or case.current_run_id or "", "approval",
                    )
                case = self.lifecycle._update_case(
                    con, replace(case, approval_status=target.value, draft_id=str(draft_id))
                )
                event = replace(event, case_version=case.version)
                self.events.append_in(con, event)
                con.commit()
                return event
            except Exception:
                con.rollback()
                raise

    def _supersede_current(self, con, case, new_draft, reviewer_id, actor_type, run_id):
        current = con.execute(
            "SELECT ae.*,co.created_at AS draft_created_at,co.id AS numeric_draft_id "
            "FROM approval_events ae JOIN content_outputs co ON CAST(co.id AS TEXT)=ae.draft_id "
            "WHERE ae.case_id=? AND ae.new_status='approved' "
            "AND NOT EXISTS (SELECT 1 FROM approval_events s WHERE s.case_id=ae.case_id "
            "AND s.draft_id=ae.draft_id AND s.new_status='superseded') "
            "ORDER BY co.created_at DESC,co.id DESC,ae.id DESC LIMIT 1",
            (case.case_id,),
        ).fetchone()
        if not current or current["draft_id"] == str(new_draft["id"]):
            return case
        old_order = (current["draft_created_at"], int(current["numeric_draft_id"]))
        new_order = (new_draft["created_at"], int(new_draft["id"]))
        if new_order <= old_order:
            raise DomainValidationError("An older draft cannot supersede the current approved draft")
        supersede_key = hashlib.sha256(
            f"{case.case_id}|{current['draft_id']}|supersede|{new_draft['id']}".encode()
        ).hexdigest()
        event = self._event(
            supersede_key, case, current["draft_id"], ApprovalStatus.APPROVED,
            ApprovalStatus.SUPERSEDED, ApprovalDecision.SUPERSEDE, reviewer_id,
            actor_type, f"Superseded by draft {new_draft['id']}", None, run_id,
        )
        self.events.append_in(con, event)
        return case

    @staticmethod
    def _workflow_actor(actor_type: ApprovalActorType) -> ActorType:
        return ActorType(actor_type.value)

    @staticmethod
    def _event(key, case, draft_id, previous, target, decision, reviewer_id,
               actor_type, notes, corrections, run_id):
        return ApprovalEvent(
            f"approval_{uuid4().hex}", key, case.case_id, draft_id, previous,
            target, decision, reviewer_id, actor_type, notes, corrections,
            datetime.now(UTC), case.version, run_id or case.current_run_id,
            {"topic_id": case.topic_id},
        )
