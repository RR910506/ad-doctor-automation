from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.adapters.google_approval_status import map_google_approval_status
from src.db import get_connection


@dataclass(frozen=True, slots=True)
class ApprovalConsistencyIssue:
    issue_type: str
    severity: str
    case_id: str | None
    draft_id: str | None
    explanation: str

    @property
    def code(self) -> str:  # Phase 2C compatibility
        return self.issue_type


def check_approval_consistency(
    path: Path, *, google_statuses: Mapping[str, str] | None = None
) -> list[ApprovalConsistencyIssue]:
    """Read-only diagnostics; never mutates or repairs approval data."""
    con = get_connection(path)
    issues: list[ApprovalConsistencyIssue] = []

    def add(kind, severity, case_id, draft_id, explanation):
        issues.append(ApprovalConsistencyIssue(kind, severity, case_id, draft_id, explanation))

    try:
        for row in con.execute(
            "SELECT co.id,c.case_id,c.approval_status FROM content_outputs co "
            "JOIN locked_topics lt ON lt.id=co.locked_topic_id LEFT JOIN cases c ON c.topic_id=lt.topic_id "
            "WHERE co.approval_status='approved' AND (c.case_id IS NULL OR c.approval_status!='approved')"
        ):
            add("legacy_approved_without_case_approval", "error", row["case_id"], str(row["id"]),
                "Legacy draft is approved but its Case is missing or not approved.")
        for row in con.execute(
            "SELECT case_id,draft_id FROM cases WHERE approval_status='approved' AND NOT EXISTS "
            "(SELECT 1 FROM approval_events ae WHERE ae.case_id=cases.case_id AND ae.new_status='approved')"
        ):
            add("case_approved_without_event", "error", row["case_id"], row["draft_id"],
                "Case is approved without an approval event.")
        for row in con.execute(
            "SELECT ae.case_id,ae.draft_id FROM approval_events ae LEFT JOIN cases c ON c.case_id=ae.case_id "
            "WHERE c.case_id IS NULL"
        ):
            add("event_missing_case", "error", row["case_id"], row["draft_id"],
                "Approval event refers to a missing Case.")
        for row in con.execute(
            "SELECT ae.case_id,ae.draft_id FROM approval_events ae LEFT JOIN content_outputs co "
            "ON CAST(co.id AS TEXT)=ae.draft_id WHERE co.id IS NULL"
        ):
            add("event_missing_draft", "error", row["case_id"], row["draft_id"],
                "Approval event refers to a missing draft.")
        for row in con.execute(
            "SELECT ae.case_id,ae.draft_id FROM approval_events ae JOIN cases c ON c.case_id=ae.case_id "
            "JOIN content_outputs co ON CAST(co.id AS TEXT)=ae.draft_id "
            "JOIN locked_topics lt ON lt.id=co.locked_topic_id WHERE lt.topic_id!=c.topic_id"
        ):
            add("event_case_draft_ownership_mismatch", "error", row["case_id"], row["draft_id"],
                "Approval event draft belongs to another Case topic.")
        for row in con.execute(
            "SELECT c.case_id,c.draft_id FROM cases c WHERE c.approval_status='approved' AND "
            "(c.draft_id IS NULL OR NOT EXISTS (SELECT 1 FROM approval_events ae WHERE ae.case_id=c.case_id "
            "AND ae.draft_id=c.draft_id AND ae.new_status='approved'))"
        ):
            add("approved_draft_not_linked", "error", row["case_id"], row["draft_id"],
                "Case current draft is not its approved draft.")
        current_sql = (
            "SELECT ae.case_id,ae.draft_id FROM approval_events ae WHERE ae.new_status='approved' "
            "AND NOT EXISTS (SELECT 1 FROM approval_events s WHERE s.case_id=ae.case_id "
            "AND s.draft_id=ae.draft_id AND s.new_status='superseded')"
        )
        for row in con.execute(
            f"SELECT case_id,COUNT(DISTINCT draft_id) n FROM ({current_sql}) GROUP BY case_id HAVING n>1"
        ):
            add("multiple_current_approved_drafts", "error", row["case_id"], None,
                "Case has more than one non-superseded approved draft.")
        for row in con.execute(
            f"SELECT c.case_id,c.draft_id FROM cases c JOIN ({current_sql}) a ON a.case_id=c.case_id "
            "JOIN content_outputs current_co ON CAST(current_co.id AS TEXT)=c.draft_id "
            "JOIN content_outputs approved_co ON CAST(approved_co.id AS TEXT)=a.draft_id "
            "WHERE c.approval_status='approved' AND a.draft_id!=c.draft_id AND "
            "(approved_co.created_at>current_co.created_at OR "
            "(approved_co.created_at=current_co.created_at AND approved_co.id>current_co.id))"
        ):
            add("current_approved_not_newest", "error", row["case_id"], row["draft_id"],
                "Case current approved draft is older than another eligible approved draft.")
        for row in con.execute(
            "SELECT case_id,draft_id,workflow_status,approval_status FROM cases WHERE "
            "(approval_status='approved' AND workflow_status NOT IN ('approved','publishing','published','email_pending','emailed')) "
            "OR (approval_status='revision_requested' AND workflow_status!='revision_requested') "
            "OR (approval_status='rejected' AND workflow_status='approved')"
        ):
            add("workflow_approval_mismatch", "error", row["case_id"], row["draft_id"],
                f"Workflow {row['workflow_status']} conflicts with approval {row['approval_status']}.")
        for row in con.execute(
            "SELECT c.case_id,c.draft_id,c.approval_status,c.publication_status,co.approval_status legacy_status "
            "FROM cases c LEFT JOIN content_outputs co ON CAST(co.id AS TEXT)=c.draft_id WHERE "
            "(co.approval_status='rejected' AND c.publication_status IN ('publishable','publishing','published')) "
            "OR (co.approval_status='pending' AND c.approval_status='approved')"
        ):
            kind = "rejected_draft_publishable" if row["legacy_status"] == "rejected" else "revision_requested_marked_approved"
            add(kind, "error", row["case_id"], row["draft_id"], "Legacy and Case publication/approval state conflict.")
        for row in con.execute(
            "SELECT c.case_id,c.draft_id FROM cases c WHERE c.approval_status='approved' AND EXISTS "
            "(SELECT 1 FROM approval_events ae WHERE ae.case_id=c.case_id AND ae.draft_id=c.draft_id "
            "AND ae.new_status='revision_requested' AND ae.id=(SELECT MAX(x.id) FROM approval_events x "
            "WHERE x.case_id=ae.case_id AND x.draft_id=ae.draft_id))"
        ):
            add("revision_requested_marked_approved", "error", row["case_id"], row["draft_id"],
                "Latest draft decision requests revision while the Case is marked approved.")
        for row in con.execute(
            "SELECT c.case_id,c.draft_id FROM cases c WHERE c.publication_status IN ('publishable','publishing','published') "
            "AND NOT EXISTS (SELECT 1 FROM approval_events ae WHERE ae.case_id=c.case_id "
            "AND ae.draft_id=c.draft_id AND ae.new_status='approved' AND TRIM(ae.reviewer_id)!='')"
        ):
            add("publishable_without_reviewer", "error", row["case_id"], row["draft_id"],
                "Publishable draft lacks an identified approving reviewer.")
        for row in con.execute(
            "SELECT case_id,draft_id FROM approval_events WHERE new_status='superseded' AND draft_id IN "
            "(SELECT draft_id FROM cases WHERE cases.case_id=approval_events.case_id)"
        ):
            add("superseded_draft_still_current", "error", row["case_id"], row["draft_id"],
                "A superseded draft remains the Case current draft.")
        for row in con.execute(
            "SELECT idempotency_key,MIN(case_id) case_id,MIN(draft_id) draft_id FROM approval_events "
            "GROUP BY idempotency_key HAVING COUNT(*)>1"
        ):
            add("duplicate_idempotency_key", "error", row["case_id"], row["draft_id"],
                "Approval events repeat an idempotency key.")
        valid = {
            ("not_ready", "pending_review", "submit_for_review"),
            ("pending_review", "approved", "approve"),
            ("pending_review", "revision_requested", "request_revision"),
            ("pending_review", "rejected", "reject"),
            ("revision_requested", "pending_review", "submit_for_review"),
            ("rejected", "pending_review", "submit_for_review"),
            ("approved", "superseded", "supersede"),
        }
        for row in con.execute("SELECT case_id,draft_id,previous_status,new_status,decision FROM approval_events"):
            triple = (row["previous_status"], row["new_status"], row["decision"])
            if triple not in valid:
                add("approval_decision_transition_mismatch", "error", row["case_id"], row["draft_id"],
                    f"Decision {triple[2]} does not match transition {triple[0]} to {triple[1]}.")
        if google_statuses:
            for draft_id, raw in google_statuses.items():
                row = con.execute(
                    "SELECT c.case_id,c.approval_status FROM cases c WHERE c.draft_id=?", (str(draft_id),)
                ).fetchone()
                try:
                    mapped = map_google_approval_status(raw)
                except ValueError:
                    add("unknown_google_status", "warning", row["case_id"] if row else None, str(draft_id),
                        f"Unknown Google approval status: {raw}")
                    continue
                if row and mapped.value != row["approval_status"]:
                    add("google_local_status_mismatch", "warning", row["case_id"], str(draft_id),
                        f"Google status {mapped.value} differs from local status {row['approval_status']}.")
        return issues
    finally:
        con.close()
