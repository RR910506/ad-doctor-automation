from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Callable
from uuid import uuid4

from src.config import DATABASE_PATH
from src.security.redaction import redact


@dataclass(frozen=True, slots=True)
class UIActionResult:
    success: bool
    user_message: str
    error_category: str | None = None
    refresh_required: bool = False
    operation_id: str | None = None
    run_id: str | None = None

    def as_dict(self):
        return asdict(self)


class UIActionService:
    """Single controlled mutation boundary for the modern Streamlit UI."""

    _manual_run_lock = Lock()

    def __init__(self, database_path: str | Path = DATABASE_PATH, manual_runner: Callable | None = None):
        self.database_path = Path(database_path)
        self._manual_runner = manual_runner

    @staticmethod
    def unavailable(action):
        return UIActionResult(False, f"{action} is not available in this phase.", "not_available")

    @staticmethod
    def _confirmation(action: str):
        return UIActionResult(False, f"Confirm {action} before continuing.", "confirmation_required")

    @staticmethod
    def _failure(exc: Exception, operation_id: str | None = None):
        return UIActionResult(False, f"The action could not be completed: {redact(str(exc))}", type(exc).__name__, operation_id=operation_id)

    def set_source_active(self, source_id: int, active: bool, *, confirmed: bool = False):
        if not confirmed:
            return self._confirmation("this source status change")
        operation_id = f"source_{uuid4().hex}"
        try:
            from src.source_manager import set_source_active
            changed = set_source_active(source_id, active, database_path=self.database_path)
            if not changed:
                return UIActionResult(False, "The source was not found.", "not_found", operation_id=operation_id)
            return UIActionResult(True, f"Source {'activated' if active else 'deactivated'}.", refresh_required=True, operation_id=operation_id)
        except Exception as exc:
            return self._failure(exc, operation_id)

    def import_sources_from_file(self, file, *, filename: str | None = None, confirmed: bool = False):
        if not confirmed:
            return self._confirmation("the source import")
        operation_id = f"source_import_{uuid4().hex}"
        try:
            from src.source_manager import import_sources_from_file
            result = import_sources_from_file(file, filename=filename, database_path=self.database_path)
            return UIActionResult(True, f"Imported {result.inserted} new and updated {result.updated} existing source(s); skipped {result.skipped_rows} row(s).", refresh_required=True, operation_id=operation_id)
        except Exception as exc:
            return self._failure(exc, operation_id)

    def import_sources_from_google_sheet(self, sheet_url: str, *, confirmed: bool = False):
        if not confirmed:
            return self._confirmation("the Google Sheet source import")
        operation_id = f"sheet_import_{uuid4().hex}"
        try:
            from src.source_manager import import_sources_from_google_sheet
            result = import_sources_from_google_sheet(sheet_url, database_path=self.database_path)
            return UIActionResult(True, f"Imported {result.inserted} new and updated {result.updated} existing source(s).", refresh_required=True, operation_id=operation_id)
        except Exception as exc:
            return self._failure(exc, operation_id)

    def decide_content(self, content_output_id: int, decision: str, *, reviewer_id: str, notes: str = "", confirmed: bool = False):
        if decision not in {"approved", "rejected", "pending"}:
            return UIActionResult(False, "Unsupported approval decision.", "validation")
        if not reviewer_id.strip():
            return UIActionResult(False, "Reviewer identity is required.", "validation")
        if not confirmed:
            return self._confirmation(decision)
        operation_id = f"approval_{uuid4().hex}"
        try:
            from src.approval_engine import set_content_approval
            summary = set_content_approval(int(content_output_id), decision, reviewer_notes=notes or None, database_path=self.database_path, reviewer_id=reviewer_id.strip())
            return UIActionResult(True, f"Content marked {decision}.", refresh_required=True, operation_id=operation_id, run_id=summary.run_id)
        except Exception as exc:
            return self._failure(exc, operation_id)

    def approve_content(self, content_output_id: int, **kwargs):
        return self.decide_content(content_output_id, "approved", **kwargs)

    def reject_content(self, content_output_id: int, **kwargs):
        return self.decide_content(content_output_id, "rejected", **kwargs)

    def request_revision(self, content_output_id: int, **kwargs):
        return self.decide_content(content_output_id, "pending", **kwargs)

    def run_automation_now(self, *, confirmed: bool = False):
        if not confirmed:
            return self._confirmation("a live automation run")
        if not self._manual_run_lock.acquire(blocking=False):
            return UIActionResult(False, "An automation run is already in progress.", "duplicate_run")
        operation_id = f"manual_{uuid4().hex}"
        try:
            from src.db import get_connection
            from contextlib import closing
            with closing(get_connection(self.database_path)) as connection:
                running = connection.execute("SELECT COUNT(*) FROM automation_run_history WHERE status='running'").fetchone()[0]
            if running:
                return UIActionResult(False, "An automation run is already in progress.", "duplicate_run", operation_id=operation_id)
            runner = self._manual_runner
            if runner is None:
                from src.automation_engine import get_automation_settings
                from src.pipeline_runner import run_real_pipeline
                settings = get_automation_settings(database_path=self.database_path)
                runner = lambda: run_real_pipeline(database_path=self.database_path, fetch_source_time_budget_seconds=settings.fetch_source_time_budget_seconds)
            summary = runner()
            run_id = str(getattr(summary, "run_id", None) or (summary.get("run_id") if isinstance(summary, dict) else "") or datetime.now(UTC).strftime("manual_%Y%m%d_%H%M%S"))
            return UIActionResult(True, "Automation run completed. Review the run summary and logs.", refresh_required=True, operation_id=operation_id, run_id=run_id)
        except Exception as exc:
            return self._failure(exc, operation_id)
        finally:
            self._manual_run_lock.release()

    def regenerate(self, target: str):
        return UIActionResult(False, "Available after structured Case activation.", "case_activation_required")
