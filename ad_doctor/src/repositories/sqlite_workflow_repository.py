from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime
from pathlib import Path

from src.db import get_connection
from src.domain.workflow import ActorType, WorkflowStatus, WorkflowTransition


class SQLiteWorkflowRepository:
    """Append-only workflow history adapter; transition IDs make retries idempotent."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def append_transition(self, transition: WorkflowTransition) -> WorkflowTransition:
        with closing(get_connection(self.database_path)) as connection:
            connection.execute("INSERT OR IGNORE INTO workflow_transitions (transition_id, case_id, from_status, to_status, actor_type, actor_id, reason, occurred_at, metadata_json, failed_stage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (transition.transition_id, transition.case_id, transition.from_status.value, transition.to_status.value, transition.actor_type.value, transition.actor_id, transition.reason, transition.occurred_at.isoformat(), json.dumps(dict(transition.metadata), ensure_ascii=False), transition.failed_stage.value if transition.failed_stage else None))
            connection.commit()
        return transition

    def list_transition_history(self, case_id: str) -> list[WorkflowTransition]:
        with closing(get_connection(self.database_path)) as connection:
            rows = connection.execute("SELECT * FROM workflow_transitions WHERE case_id = ? ORDER BY occurred_at, id", (case_id,)).fetchall()
        return [self._from_row(row) for row in rows]

    def get_latest_transition(self, case_id: str) -> WorkflowTransition | None:
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute("SELECT * FROM workflow_transitions WHERE case_id = ? ORDER BY occurred_at DESC, id DESC LIMIT 1", (case_id,)).fetchone()
        return self._from_row(row) if row else None

    @staticmethod
    def _from_row(row) -> WorkflowTransition:
        return WorkflowTransition(transition_id=row["transition_id"], case_id=row["case_id"], from_status=WorkflowStatus(row["from_status"]), to_status=WorkflowStatus(row["to_status"]), actor_type=ActorType(row["actor_type"]), actor_id=row["actor_id"], reason=row["reason"], occurred_at=datetime.fromisoformat(row["occurred_at"]), metadata=json.loads(row["metadata_json"] or "{}"), failed_stage=WorkflowStatus(row["failed_stage"]) if row["failed_stage"] else None)
