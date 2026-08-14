from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from src.db import get_connection
from src.domain.evidence_pack import EvidencePack


class SQLiteEvidenceRepository:
    """Versioned JSON storage for validated provider-independent Evidence Packs."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def create(self, evidence_pack: EvidencePack) -> EvidencePack:
        with closing(get_connection(self.database_path)) as connection:
            version = int(connection.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM evidence_packs WHERE case_id = ?", (evidence_pack.case_id,)).fetchone()[0])
            connection.execute("INSERT INTO evidence_packs (evidence_pack_id, case_id, schema_version, version, research_provider, research_model, research_completed_at, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (evidence_pack.evidence_pack_id, evidence_pack.case_id, evidence_pack.schema_version, version, evidence_pack.research_provider, evidence_pack.research_model, evidence_pack.research_completed_at.isoformat(), json.dumps(evidence_pack.to_dict(), ensure_ascii=False)))
            connection.commit()
        return evidence_pack

    def get_latest_for_case(self, case_id: str) -> EvidencePack | None:
        return self._get("case_id = ? ORDER BY version DESC LIMIT 1", (case_id,))

    def get_by_evidence_pack_id(self, evidence_pack_id: str) -> EvidencePack | None:
        return self._get("evidence_pack_id = ?", (evidence_pack_id,))

    def list_versions_for_case(self, case_id: str) -> list[EvidencePack]:
        with closing(get_connection(self.database_path)) as connection:
            rows = connection.execute("SELECT payload_json FROM evidence_packs WHERE case_id = ? ORDER BY version", (case_id,)).fetchall()
        return [EvidencePack.from_dict(json.loads(row["payload_json"])) for row in rows]

    def _get(self, clause: str, params: tuple[object, ...]) -> EvidencePack | None:
        with closing(get_connection(self.database_path)) as connection:
            row = connection.execute(f"SELECT payload_json FROM evidence_packs WHERE {clause}", params).fetchone()
        return EvidencePack.from_dict(json.loads(row["payload_json"])) if row else None
