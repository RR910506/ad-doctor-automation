from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.case_file_compat import map_legacy_topic_to_case
from src.db import get_connection
from src.domain.workflow import ActorType, WorkflowStatus, WorkflowTransition
from src.repositories.sqlite_case_repository import SQLiteCaseRepository
from src.repositories.sqlite_workflow_repository import SQLiteWorkflowRepository


@dataclass(frozen=True, slots=True)
class BackfillReport:
    dry_run: bool
    would_create: int
    created: int
    skipped_existing: int
    flagged: int
    topic_ids: tuple[int, ...]


def backfill_case_files(database_path: Path, *, dry_run: bool = True, actor_id: str = "phase_2a_backfill") -> BackfillReport:
    """Map legacy topics into Case Files; defaults to a zero-write dry run."""
    database_path = Path(database_path)
    cases = SQLiteCaseRepository(database_path)
    transitions = SQLiteWorkflowRepository(database_path)
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT t.*, COALESCE(ts.unique_source_coverage_count, 0) AS publication_count,
                   ts.total_score,
                   (SELECT lt.run_id FROM locked_topics lt WHERE lt.topic_id = t.id ORDER BY lt.id DESC LIMIT 1) AS run_id,
                   (SELECT rd.id FROM locked_topics lt JOIN research_dossiers rd ON rd.locked_topic_id = lt.id WHERE lt.topic_id = t.id ORDER BY rd.id DESC LIMIT 1) AS dossier_id,
                   (SELECT co.id FROM locked_topics lt JOIN content_outputs co ON co.locked_topic_id = lt.id WHERE lt.topic_id = t.id ORDER BY co.id DESC LIMIT 1) AS draft_id
            FROM topics t LEFT JOIN topic_scores ts ON ts.topic_id = t.id ORDER BY t.id
            """
        ).fetchall()
    create_count = created = skipped = flagged = 0
    ids: list[int] = []
    for row in rows:
        data = dict(row)
        if not str(data.get("exact_topic") or "").strip():
            flagged += 1
            continue
        case = map_legacy_topic_to_case(data, unique_publication_count=int(data["publication_count"]), priority_score=data.get("total_score"), current_run_id=data.get("run_id"), draft={"id": data["draft_id"]} if data.get("draft_id") else None, dossier={"id": data["dossier_id"]} if data.get("dossier_id") else None)
        if cases.get_by_topic_id(case.topic_id):
            skipped += 1
            continue
        create_count += 1
        ids.append(case.topic_id)
        if dry_run:
            continue
        stored, was_created = cases.create_or_get(case)
        if not was_created:
            skipped += 1
            continue
        created += 1
        status = WorkflowStatus(stored.workflow_status)
        transitions.append_transition(WorkflowTransition(
            transition_id=f"transition_{uuid4().hex}", case_id=stored.case_id,
            from_status=status, to_status=status, actor_type=ActorType.MIGRATION,
            actor_id=actor_id, reason="Imported existing state without inferring missing history.",
            occurred_at=datetime.now(UTC), metadata={"legacy_topic_id": stored.topic_id, "backfill": True},
        ))
    return BackfillReport(dry_run=dry_run, would_create=create_count, created=created, skipped_existing=skipped, flagged=flagged, topic_ids=tuple(ids))
