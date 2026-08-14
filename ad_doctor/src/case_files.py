from __future__ import annotations

import os
from pathlib import Path

from src.config import DATABASE_PATH
from src.services.case_lifecycle_service import CaseLifecycleService


def get_case_files_mode() -> str:
    """Return off or shadow; active is reserved and behaves as shadow in Phase 2B."""
    mode = os.getenv("AD_DOCTOR_CASE_FILES_MODE", "").strip().lower()
    if mode in {"shadow", "active"}:
        return mode
    enabled = os.getenv("AD_DOCTOR_CASE_FILES_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    return "shadow" if enabled else "off"


def get_case_lifecycle_service(database_path: Path = DATABASE_PATH) -> CaseLifecycleService | None:
    return None if get_case_files_mode() == "off" else CaseLifecycleService(Path(database_path))

