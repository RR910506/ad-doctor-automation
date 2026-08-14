from __future__ import annotations

from typing import Protocol

from src.domain.workflow import WorkflowTransition


class WorkflowRepository(Protocol):
    def append_transition(self, transition: WorkflowTransition) -> WorkflowTransition: ...
    def list_transition_history(self, case_id: str) -> list[WorkflowTransition]: ...
    def get_latest_transition(self, case_id: str) -> WorkflowTransition | None: ...

