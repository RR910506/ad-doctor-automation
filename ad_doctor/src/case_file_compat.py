"""Non-invasive mappings from legacy rows into the Phase 2A domain contract."""
from __future__ import annotations

import os
from typing import Mapping

from src.domain.case_file import CaseFile, ContentType
from src.domain.workflow import WorkflowStatus


def case_files_enabled() -> bool:
    """Return whether future pipeline integration is explicitly enabled."""
    return os.getenv("AD_DOCTOR_CASE_FILES_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def map_legacy_topic_to_case(topic: Mapping[str, object], *, unique_publication_count: int = 0, priority_score: float | None = None, current_run_id: str | None = None, draft: Mapping[str, object] | None = None, dossier: Mapping[str, object] | None = None) -> CaseFile:
    """Map known legacy facts without inventing missing workflow history."""
    status = str(topic.get("status") or "extracted").lower()
    if status == "rejected":
        workflow = WorkflowStatus.REJECTED
        qualification = "rejected"
    elif status == "qualified":
        qualification = "qualified"
        if draft:
            workflow = WorkflowStatus.CONTENT_DRAFTED
        elif dossier:
            workflow = WorkflowStatus.RESEARCH_COMPLETE
        else:
            workflow = WorkflowStatus.QUALIFIED
    else:
        workflow = WorkflowStatus.QUALIFICATION_PENDING
        qualification = "pending"
    return CaseFile.for_topic(
        int(topic["id"]), str(topic.get("exact_topic") or topic.get("canonical_topic") or "").strip(),
        workflow_status=workflow.value, qualification_status=qualification,
        unique_publication_count=unique_publication_count, priority_score=priority_score,
        current_run_id=current_run_id, draft_id=str(draft.get("id")) if draft and draft.get("id") is not None else None,
        evidence_pack_id=str(dossier.get("id")) if dossier and dossier.get("id") is not None else None,
        content_type=ContentType.UNCLASSIFIED,
        qualification_reason="Imported from the legacy three-publication qualification state." if qualification == "qualified" else None,
        rejection_reason="Imported from the existing rejected topic state." if qualification == "rejected" else None,
    )

