from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from src.db import get_connection
from src.domain.case_file import CaseFile, ContentType, utc_now
from src.domain.errors import VersionConflictError


CASE_COLUMNS = "case_id, topic_id, canonical_topic, brand, campaign, content_type, workflow_status, qualification_status, unique_publication_count, qualification_reason, rejection_reason, priority_score, current_run_id, evidence_pack_id, draft_id, quality_status, approval_status, publication_status, created_at, updated_at, version"


class SQLiteCaseRepository:
    """SQLite Case File adapter with topic idempotency and optimistic locking."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def create(self, case: CaseFile) -> CaseFile:
        values = self._values(case)
        with closing(get_connection(self.database_path)) as connection:
            connection.execute(f"INSERT INTO cases ({CASE_COLUMNS}) VALUES ({','.join('?' for _ in values)})", values)
            connection.commit()
        return case

    def get_by_case_id(self, case_id: str) -> CaseFile | None:
        return self._get("case_id = ?", (case_id,))

    def get_by_topic_id(self, topic_id: int) -> CaseFile | None:
        return self._get("topic_id = ?", (topic_id,))

    def exists(self, case_id: str) -> bool:
        return self.get_by_case_id(case_id) is not None

    def create_or_get(self, case: CaseFile) -> tuple[CaseFile, bool]:
        existing = self.get_by_topic_id(case.topic_id)
        if existing:
            return existing, False
        try:
            return self.create(case), True
        except sqlite3.IntegrityError:
            existing = self.get_by_topic_id(case.topic_id)
            if existing:
                return existing, False
            raise

    def update(self, case: CaseFile, expected_version: int) -> CaseFile:
        updated = replace(case, version=expected_version + 1, updated_at=utc_now())
        assignments = ", ".join(f"{column} = ?" for column in CASE_COLUMNS.split(", ")[1:])
        values = self._values(updated)[1:] + (case.case_id, expected_version)
        with closing(get_connection(self.database_path)) as connection:
            cursor = connection.execute(f"UPDATE cases SET {assignments} WHERE case_id = ? AND version = ?", values)
            if cursor.rowcount != 1:
                raise VersionConflictError(f"Case {case.case_id} version {expected_version} is stale")
            connection.commit()
        return updated

    def list_cases(self, *, workflow_status: str | None = None, qualification_status: str | None = None, quality_status: str | None = None, approval_status: str | None = None, publication_status: str | None = None, limit: int = 100, offset: int = 0) -> list[CaseFile]:
        filters = {"workflow_status": workflow_status, "qualification_status": qualification_status, "quality_status": quality_status, "approval_status": approval_status, "publication_status": publication_status}
        clauses, params = [], []
        for column, value in filters.items():
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(get_connection(self.database_path)) as connection:
            rows = connection.execute(f"SELECT {CASE_COLUMNS} FROM cases{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?", (*params, max(1, limit), max(0, offset))).fetchall()
        return [self._from_row(row) for row in rows]

    def _get(self, clause: str, params: tuple[object, ...]) -> CaseFile | None:
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute(f"SELECT {CASE_COLUMNS} FROM cases WHERE {clause}", params).fetchone()
        return self._from_row(row) if row else None

    @staticmethod
    def _values(case: CaseFile) -> tuple[object, ...]:
        return (case.case_id, case.topic_id, case.canonical_topic, case.brand, case.campaign, case.content_type.value, case.workflow_status, case.qualification_status, case.unique_publication_count, case.qualification_reason, case.rejection_reason, case.priority_score, case.current_run_id, case.evidence_pack_id, case.draft_id, case.quality_status, case.approval_status, case.publication_status, case.created_at.isoformat(), case.updated_at.isoformat(), case.version)

    @staticmethod
    def _from_row(row) -> CaseFile:
        from datetime import datetime
        data = dict(row)
        data["content_type"] = ContentType(data["content_type"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return CaseFile(**data)
