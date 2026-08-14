from __future__ import annotations

import json
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.case_file_compat import map_legacy_topic_to_case
from src.db import get_connection
from src.domain.case_file import CaseFile, ContentType
from src.domain.errors import EntityNotFoundError, InvalidWorkflowTransition, VersionConflictError
from src.domain.evidence_pack import EvidencePack
from src.domain.workflow import ALLOWED_TRANSITIONS, ActorType, WorkflowStatus
from src.repositories.sqlite_case_repository import CASE_COLUMNS, SQLiteCaseRepository
from src.repositories.sqlite_evidence_repository import SQLiteEvidenceRepository
from src.repositories.sqlite_workflow_repository import SQLiteWorkflowRepository


class CaseLifecycleService:
    """Atomically coordinate Case File state, evidence, links and audit history."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.cases = SQLiteCaseRepository(self.database_path)
        self.evidence = SQLiteEvidenceRepository(self.database_path)
        self.workflow = SQLiteWorkflowRepository(self.database_path)

    def sync_qualification(self, topic: dict[str, object], coverages: list[dict[str, object]], score: dict[str, object], *, qualified: bool, run_id: str) -> CaseFile:
        case = map_legacy_topic_to_case(topic, unique_publication_count=int(score["unique_source_coverage_count"]), priority_score=float(score["total_score"]), current_run_id=run_id)
        target = WorkflowStatus.QUALIFIED if qualified else WorkflowStatus.REJECTED
        with closing(get_connection(self.database_path)) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM cases WHERE topic_id = ?", (case.topic_id,)).fetchone()
            if row is None:
                initial = replace(case, workflow_status=WorkflowStatus.QUALIFICATION_PENDING.value, qualification_status="pending")
                self._insert_case(con, initial)
                current = initial
            else:
                current = self.cases._from_row(row)
            if current.workflow_status == WorkflowStatus.REJECTED.value and target == WorkflowStatus.QUALIFIED:
                current = self._transition(con, current, WorkflowStatus.QUALIFICATION_PENDING, ActorType.SYSTEM, "qualification_engine", "New coverage triggered requalification.", run_id, "qualification")
            elif current.workflow_status not in {WorkflowStatus.QUALIFICATION_PENDING.value, target.value}:
                raise InvalidWorkflowTransition(f"Qualification cannot update case from {current.workflow_status}")
            updated = replace(current, canonical_topic=case.canonical_topic, unique_publication_count=case.unique_publication_count, priority_score=case.priority_score, current_run_id=run_id, qualification_status="qualified" if qualified else "rejected", qualification_reason="Same marketing development covered by at least three distinct approved publications." if qualified else None, rejection_reason=None if qualified else "Fewer than three distinct approved publications cover the exact development.")
            if current.workflow_status != target.value:
                updated = self._transition(con, updated, target, ActorType.SYSTEM, "qualification_engine", "Three-publication qualification evaluated.", run_id, "qualification")
            else:
                updated = self._update_case(con, updated)
            for article in coverages:
                con.execute("INSERT OR IGNORE INTO case_article_links (case_id, article_id, link_role, source_id, contributed_to_qualification, relevance_score, originating_run_id) VALUES (?, ?, 'exact_topic_evidence', ?, 1, ?, ?)", (updated.case_id, int(article["article_id"]), int(article["source_id"]), float(article.get("relevance_score") or 1.0), run_id))
            con.commit()
        return updated

    def transition_topic(self, topic_id: int, target: WorkflowStatus, *, run_id: str, stage: str, reason: str) -> CaseFile:
        with closing(get_connection(self.database_path)) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM cases WHERE topic_id = ?", (topic_id,)).fetchone()
            if not row:
                raise EntityNotFoundError(f"Case for topic {topic_id} not found")
            case = self._transition(con, self.cases._from_row(row), target, ActorType.SYSTEM, stage, reason, run_id, stage)
            con.commit()
            return case

    def store_evidence(self, topic_id: int, pack: EvidencePack, *, run_id: str) -> CaseFile:
        with closing(get_connection(self.database_path)) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM cases WHERE topic_id = ?", (topic_id,)).fetchone()
            if not row:
                raise EntityNotFoundError(f"Case for topic {topic_id} not found")
            case = self.cases._from_row(row)
            version = int(con.execute("SELECT COALESCE(MAX(version),0)+1 FROM evidence_packs WHERE case_id=?", (case.case_id,)).fetchone()[0])
            con.execute("INSERT INTO evidence_packs (evidence_pack_id,case_id,schema_version,version,research_provider,research_model,research_completed_at,payload_json) VALUES (?,?,?,?,?,?,?,?)", (pack.evidence_pack_id, case.case_id, pack.schema_version, version, pack.research_provider, pack.research_model, pack.research_completed_at.isoformat(), json.dumps(pack.to_dict(), ensure_ascii=False)))
            case = replace(case, evidence_pack_id=pack.evidence_pack_id, current_run_id=run_id)
            case = self._transition(con, case, WorkflowStatus.RESEARCH_COMPLETE, ActorType.SYSTEM, "research_engine", "Validated Evidence Pack stored.", run_id, "research")
            con.commit()
            return case

    def link_draft(self, topic_id: int, draft_id: int, *, run_id: str) -> CaseFile:
        with closing(get_connection(self.database_path)) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM cases WHERE topic_id=?", (topic_id,)).fetchone()
            if not row:
                raise EntityNotFoundError(f"Case for topic {topic_id} not found")
            case = replace(self.cases._from_row(row), draft_id=str(draft_id), current_run_id=run_id)
            case = self._transition(con, case, WorkflowStatus.CONTENT_DRAFTED, ActorType.SYSTEM, "content_engine", "Legacy draft stored and linked.", run_id, "content")
            con.commit()
            return case

    def fail_topic(self, topic_id: int, *, run_id: str, stage: WorkflowStatus, error: Exception) -> CaseFile:
        return self.transition_topic(topic_id, WorkflowStatus.FAILED, run_id=run_id, stage=stage.value, reason=f"{type(error).__name__}: {error}")

    def _transition(self, con, case: CaseFile, target: WorkflowStatus, actor_type: ActorType, actor_id: str, reason: str, run_id: str, stage: str) -> CaseFile:
        current = WorkflowStatus(case.workflow_status)
        if current == target:
            return self._update_case(con, case)
        if target not in ALLOWED_TRANSITIONS[current]:
            raise InvalidWorkflowTransition(f"Cannot transition from {current.value} to {target.value}")
        updated = self._update_case(con, replace(case, workflow_status=target.value, updated_at=datetime.now(UTC)))
        con.execute("INSERT INTO workflow_transitions (transition_id,case_id,from_status,to_status,actor_type,actor_id,reason,occurred_at,metadata_json,failed_stage) VALUES (?,?,?,?,?,?,?,?,?,?)", (f"transition_{uuid4().hex}", case.case_id, current.value, target.value, actor_type.value, actor_id, reason, datetime.now(UTC).isoformat(), json.dumps({"run_id":run_id,"topic_id":case.topic_id,"case_id":case.case_id,"stage":stage}), current.value if target == WorkflowStatus.FAILED else None))
        return updated

    def _update_case(self, con, case: CaseFile) -> CaseFile:
        updated = replace(case, version=case.version+1, updated_at=datetime.now(UTC))
        values = self.cases._values(updated)[1:] + (case.case_id, case.version)
        assignments = ", ".join(f"{column}=?" for column in CASE_COLUMNS.split(", ")[1:])
        if con.execute(f"UPDATE cases SET {assignments} WHERE case_id=? AND version=?", values).rowcount != 1:
            raise VersionConflictError(f"Stale Case File {case.case_id}")
        return updated

    def _insert_case(self, con, case: CaseFile) -> None:
        values=self.cases._values(case)
        con.execute(f"INSERT INTO cases ({CASE_COLUMNS}) VALUES ({','.join('?' for _ in values)})", values)
