from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import json

from src.config import DATABASE_PATH
from src.db import (
    get_connection,
    insert_approval,
    list_approval_queue,
    log_run_event,
    update_content_approval_status,
)


VALID_APPROVAL_STATUSES = {"approved", "rejected", "pending"}


@dataclass(frozen=True)
class ApprovalSummary:
    run_id: str
    content_output_id: int
    status: str


def set_content_approval(
    content_output_id: int,
    status: str,
    reviewer_notes: str | None = None,
    database_path=DATABASE_PATH,
    run_id: str | None = None,
) -> ApprovalSummary:
    if status not in VALID_APPROVAL_STATUSES:
        raise ValueError(f"Invalid approval status: {status}")
    run_id = run_id or datetime.now(UTC).strftime("run_%Y%m%d_%H%M%S")
    with closing(get_connection(database_path)) as connection:
        update_content_approval_status(connection, content_output_id, status)
        insert_approval(
            connection,
            content_output_id=content_output_id,
            approval_type="manual_review",
            status=status,
            reviewer_notes=reviewer_notes,
        )
        summary = ApprovalSummary(run_id=run_id, content_output_id=content_output_id, status=status)
        log_run_event(
            connection,
            run_id,
            "phase_10_approval",
            "complete",
            f"Content output {content_output_id} marked {status}.",
            json.dumps(summary.__dict__),
        )
        connection.commit()
        return summary


def get_approval_queue(database_path=DATABASE_PATH):
    with closing(get_connection(database_path)) as connection:
        return list_approval_queue(connection)


def process_approval() -> None:
    raise NotImplementedError("Use set_content_approval.")
