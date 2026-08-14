from __future__ import annotations

import hashlib
import json
import logging
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from src.case_files import get_case_files_mode
from src.db import get_connection, log_run_event
from src.domain.approval import ApprovalDecision, ApprovalStatus
from src.security.redaction import redact
from src.services.editorial_approval_service import EditorialApprovalService


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LegacyMirrorResult:
    attempted: bool
    mirrored: bool
    case_id: str | None = None
    error_type: str | None = None


class LegacyApprovalCompatibilityService:
    """Best-effort shadow mirror executed only after the legacy commit succeeds."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)

    def mirror_committed(
        self,
        *,
        content_output_id: int,
        legacy_status: str,
        reviewer_id: str,
        reviewer_notes: str | None,
        run_id: str,
    ) -> LegacyMirrorResult:
        if get_case_files_mode() == "off":
            return LegacyMirrorResult(False, False)
        context = self._context(content_output_id)
        case_id = context.get("case_id") if context else None
        payload = {
            "case_id": case_id,
            "topic_id": context.get("topic_id") if context else None,
            "draft_id": str(content_output_id),
            "decision": legacy_status,
            "stage": "legacy_approval_shadow",
            "run_id": run_id,
        }
        try:
            if not context or not case_id:
                raise LookupError("No Case is linked to the legacy draft topic")
            service = EditorialApprovalService(self.database_path)
            latest = service.events.latest(case_id, str(content_output_id))
            previous = latest.new_status if latest else ApprovalStatus.NOT_READY
            decisions: list[ApprovalDecision] = []
            if legacy_status == "approved":
                if previous in {ApprovalStatus.NOT_READY, ApprovalStatus.REVISION_REQUESTED, ApprovalStatus.REJECTED}:
                    decisions.append(ApprovalDecision.SUBMIT_FOR_REVIEW)
                decisions.append(ApprovalDecision.APPROVE)
            elif legacy_status == "rejected":
                if previous in {ApprovalStatus.NOT_READY, ApprovalStatus.REVISION_REQUESTED, ApprovalStatus.REJECTED}:
                    decisions.append(ApprovalDecision.SUBMIT_FOR_REVIEW)
                decisions.append(ApprovalDecision.REJECT)
            elif legacy_status == "pending" and previous in {
                ApprovalStatus.NOT_READY, ApprovalStatus.REVISION_REQUESTED, ApprovalStatus.REJECTED
            }:
                decisions.append(ApprovalDecision.SUBMIT_FOR_REVIEW)
            for decision in decisions:
                key = hashlib.sha256(
                    f"legacy|{run_id}|{content_output_id}|{legacy_status}|{decision.value}".encode()
                ).hexdigest()
                service.decide(
                    case_id, str(content_output_id), decision,
                    reviewer_id=reviewer_id or "legacy_editor",
                    notes=reviewer_notes or ("Legacy reviewer rejected draft." if decision == ApprovalDecision.REJECT else None),
                    corrections=reviewer_notes if decision == ApprovalDecision.REQUEST_REVISION else None,
                    run_id=run_id, idempotency_key=key,
                )
            return LegacyMirrorResult(True, True, case_id)
        except Exception as exc:
            failure = {**payload, "error_type": type(exc).__name__}
            safe = redact(failure)
            LOGGER.error("Legacy approval shadow mirror failed: %s", json.dumps(safe, sort_keys=True))
            with closing(get_connection(self.database_path)) as con:
                log_run_event(
                    con, run_id, "legacy_approval_shadow", "error",
                    "Legacy approval committed; unified shadow mirror failed.",
                    json.dumps(safe, sort_keys=True),
                )
                con.commit()
            return LegacyMirrorResult(True, False, case_id, type(exc).__name__)

    def _context(self, draft_id: int) -> dict[str, object] | None:
        with closing(get_connection(self.database_path)) as con:
            row = con.execute(
                "SELECT lt.topic_id,c.case_id FROM content_outputs co "
                "JOIN locked_topics lt ON lt.id=co.locked_topic_id "
                "LEFT JOIN cases c ON c.topic_id=lt.topic_id WHERE co.id=?",
                (draft_id,),
            ).fetchone()
        return dict(row) if row else None
