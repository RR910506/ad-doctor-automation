from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from src.adapters.perplexity_evidence_adapter import PerplexityEvidenceAdapter
from src.case_file_compat import map_legacy_topic_to_case
from src.db import get_connection
from src.domain.case_file import stable_case_id
from src.domain.workflow import ActorType
from src.repositories.sqlite_case_repository import CASE_COLUMNS, SQLiteCaseRepository


@dataclass(frozen=True, slots=True)
class BackfillPolicy:
    include_qualified: bool = True
    include_rejected_with_editorial_history: bool = True
    include_pending_with_editorial_history: bool = True
    require_three_publications_for_qualified: bool = True
    convert_valid_evidence: bool = True
    link_legacy_drafts: bool = True
    map_approvals_only_with_reviewer_evidence: bool = True


@dataclass(frozen=True, slots=True)
class CandidateAssessment:
    topic_id: int
    topic: str
    legacy_status: str
    decision: str
    reason: str
    publication_count: int
    supporting_publications: tuple[str, ...]
    has_research: bool
    has_draft: bool
    legacy_approval_status: str | None
    evidence_result: str
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class ProductionBackfillReport:
    dry_run: bool
    historical_topics: int
    qualified_topics: int
    editorial_history_topics: int
    selected_candidates: int
    would_create: int
    created: int
    skipped_existing: int
    skipped_policy: int
    flagged: int
    article_links_created: int
    evidence_packs_created: int
    draft_linkages: int
    approval_mappings: int
    structured_packages_created: int
    assessments: tuple[CandidateAssessment, ...]


def assess_production_backfill(database_path: Path, policy: BackfillPolicy | None = None) -> ProductionBackfillReport:
    return _backfill(Path(database_path), policy or BackfillPolicy(), dry_run=True)


def activate_production_backfill(database_path: Path, policy: BackfillPolicy | None = None) -> ProductionBackfillReport:
    return _backfill(Path(database_path), policy or BackfillPolicy(), dry_run=False)


def _backfill(database_path: Path, policy: BackfillPolicy, *, dry_run: bool) -> ProductionBackfillReport:
    with closing(get_connection(database_path)) as connection:
        historical_topics = int(connection.execute("SELECT COUNT(*) FROM topics").fetchone()[0])
        qualified_topics = int(connection.execute("SELECT COUNT(*) FROM topics WHERE lower(status)='qualified'").fetchone()[0])
        editorial_history_topics = int(connection.execute(
            "SELECT COUNT(DISTINCT t.id) FROM topics t JOIN locked_topics lt ON lt.topic_id=t.id "
            "LEFT JOIN research_dossiers rd ON rd.locked_topic_id=lt.id "
            "LEFT JOIN content_outputs co ON co.locked_topic_id=lt.id WHERE rd.id IS NOT NULL OR co.id IS NOT NULL"
        ).fetchone()[0])
        rows = connection.execute(
            """
            SELECT t.id,t.exact_topic,t.normalized_topic,t.representative_headline,t.status,t.created_at,
                   ts.total_score,
                   (SELECT lt.run_id FROM locked_topics lt WHERE lt.topic_id=t.id ORDER BY lt.id DESC LIMIT 1) run_id,
                   (SELECT rd.id FROM locked_topics lt JOIN research_dossiers rd ON rd.locked_topic_id=lt.id WHERE lt.topic_id=t.id ORDER BY rd.id DESC LIMIT 1) dossier_id,
                   (SELECT rd.dossier_json FROM locked_topics lt JOIN research_dossiers rd ON rd.locked_topic_id=lt.id WHERE lt.topic_id=t.id ORDER BY rd.id DESC LIMIT 1) dossier_json,
                   (SELECT rd.updated_at FROM locked_topics lt JOIN research_dossiers rd ON rd.locked_topic_id=lt.id WHERE lt.topic_id=t.id ORDER BY rd.id DESC LIMIT 1) dossier_updated_at,
                   (SELECT co.id FROM locked_topics lt JOIN content_outputs co ON co.locked_topic_id=lt.id WHERE lt.topic_id=t.id ORDER BY co.updated_at DESC,co.id DESC LIMIT 1) draft_id,
                   (SELECT co.approval_status FROM locked_topics lt JOIN content_outputs co ON co.locked_topic_id=lt.id WHERE lt.topic_id=t.id ORDER BY co.updated_at DESC,co.id DESC LIMIT 1) draft_approval_status,
                   (SELECT a.reviewer_notes FROM locked_topics lt JOIN content_outputs co ON co.locked_topic_id=lt.id JOIN approvals a ON a.content_output_id=co.id WHERE lt.topic_id=t.id ORDER BY a.id DESC LIMIT 1) reviewer_notes
            FROM topics t LEFT JOIN topic_scores ts ON ts.topic_id=t.id ORDER BY t.id
            """
        ).fetchall()
        case_repo = SQLiteCaseRepository(database_path)
        assessments: list[CandidateAssessment] = []
        would_create = created = skipped_existing = skipped_policy = flagged = 0
        article_links_created = evidence_packs_created = draft_linkages = approval_mappings = 0
        for raw in rows:
            row = dict(raw)
            coverages = [dict(item) for item in connection.execute(
                """SELECT ts.article_id,ts.source_id,a.article_url,a.headline,a.published_date,s.source_name,s.credibility_tier,s.active_status
                   FROM topic_sources ts JOIN sources s ON s.id=ts.source_id JOIN articles a ON a.id=ts.article_id
                   WHERE ts.topic_id=? ORDER BY s.source_name,a.id""", (row["id"],)
            ).fetchall()]
            active_coverages = [item for item in coverages if int(item.get("active_status") or 0) == 1]
            publications = tuple(sorted({str(item["source_name"]) for item in active_coverages}))
            publication_count = len(publications)
            status = str(row.get("status") or "pending").lower()
            has_research = bool(row.get("dossier_id"))
            has_draft = bool(row.get("draft_id"))
            selected = (
                (policy.include_qualified and status == "qualified")
                or (policy.include_rejected_with_editorial_history and status == "rejected" and (has_research or has_draft))
                or (policy.include_pending_with_editorial_history and status not in {"qualified", "rejected"} and (has_research or has_draft))
            )
            decision = "would_create" if selected else "skipped"
            reason = "Selected by qualified/current editorial policy." if selected else "Historical topic has no qualifying editorial reason for Case activation."
            warning = None
            if status == "qualified" and policy.require_three_publications_for_qualified and publication_count < 3:
                selected = False; decision = "flagged"; reason = "Legacy qualified state has fewer than three distinct active approved publications."; warning = reason; flagged += 1
            evidence, evidence_result = _convert_evidence(row, active_coverages) if selected and has_research and policy.convert_valid_evidence else (None, "not_applicable" if not has_research else "conversion_disabled")
            if selected and has_research and evidence is None:
                warning = "Legacy research remains linked because it did not validate as a structured Evidence Pack."
            existing = case_repo.get_by_topic_id(int(row["id"])) if selected else None
            if selected:
                if existing:
                    decision = "already_exists"; skipped_existing += 1
                else:
                    would_create += 1
            else:
                skipped_policy += 1
            assessments.append(CandidateAssessment(int(row["id"]), str(row.get("exact_topic") or ""), status, decision, reason, publication_count, publications, has_research, has_draft, str(row.get("draft_approval_status")) if row.get("draft_approval_status") else None, evidence_result, warning))
            if dry_run or not selected:
                continue
            connection.execute("SAVEPOINT phase5_candidate")
            try:
                case = existing
                if case is None:
                    case = map_legacy_topic_to_case(
                        row,
                        unique_publication_count=publication_count,
                        priority_score=row.get("total_score"),
                        current_run_id=row.get("run_id"),
                        draft={"id": row["draft_id"]} if row.get("draft_id") and policy.link_legacy_drafts else None,
                        dossier=None,
                    )
                    values = case_repo._values(case)
                    connection.execute(f"INSERT INTO cases ({CASE_COLUMNS}) VALUES ({','.join('?' for _ in values)})", values)
                    connection.execute(
                        "INSERT INTO workflow_transitions(transition_id,case_id,from_status,to_status,actor_type,actor_id,reason,occurred_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)",
                        (f"transition_{uuid4().hex}", case.case_id, case.workflow_status, case.workflow_status, ActorType.MIGRATION.value, "phase_5_activation", "Imported current legacy state without inferring historical transitions.", datetime.now(UTC).isoformat(), json.dumps({"topic_id":case.topic_id,"phase":"phase5_backfill"})),
                    )
                    created += 1
                    if row.get("draft_id") and policy.link_legacy_drafts:
                        draft_linkages += 1
                for coverage in active_coverages:
                    changed = connection.execute(
                        "INSERT OR IGNORE INTO case_article_links(case_id,article_id,link_role,source_id,contributed_to_qualification,relevance_score,originating_run_id) VALUES(?,?,'exact_topic_evidence',?,1,1.0,?)",
                        (case.case_id, int(coverage["article_id"]), int(coverage["source_id"]), row.get("run_id")),
                    ).rowcount
                    article_links_created += int(changed or 0)
                if evidence is not None and connection.execute("SELECT 1 FROM evidence_packs WHERE case_id=?", (case.case_id,)).fetchone() is None:
                    connection.execute(
                        "INSERT INTO evidence_packs(evidence_pack_id,case_id,schema_version,version,research_provider,research_model,research_completed_at,payload_json) VALUES(?,?,?,?,?,?,?,?)",
                        (evidence.evidence_pack_id, case.case_id, evidence.schema_version, 1, evidence.research_provider, evidence.research_model, evidence.research_completed_at.isoformat(), json.dumps(evidence.to_dict(), ensure_ascii=False)),
                    )
                    connection.execute("UPDATE cases SET evidence_pack_id=?,version=version+1,updated_at=? WHERE case_id=?", (evidence.evidence_pack_id, datetime.now(UTC).isoformat(), case.case_id))
                    evidence_packs_created += 1
                # Historical approvals without an attributable reviewer are deliberately not mirrored.
                if policy.map_approvals_only_with_reviewer_evidence and row.get("reviewer_notes") and row.get("draft_approval_status") in {"approved", "rejected"}:
                    # reviewer_notes alone is not reviewer identity; retain as legacy history only.
                    approval_mappings += 0
                connection.execute("RELEASE SAVEPOINT phase5_candidate")
            except Exception:
                connection.execute("ROLLBACK TO SAVEPOINT phase5_candidate")
                connection.execute("RELEASE SAVEPOINT phase5_candidate")
                raise
        if dry_run:
            connection.rollback()
        else:
            connection.commit()
    return ProductionBackfillReport(dry_run, historical_topics, qualified_topics, editorial_history_topics, sum(1 for item in assessments if item.decision in {"would_create","already_exists"}), would_create, created, skipped_existing, skipped_policy, flagged, article_links_created, evidence_packs_created, draft_linkages, approval_mappings, 0, tuple(assessments))


def _convert_evidence(row: dict[str, object], coverages: list[dict[str, object]]):
    try:
        dossier = json.loads(str(row.get("dossier_json") or "{}"))
        if not isinstance(dossier, dict):
            return None, "invalid_dossier"
        case_id = stable_case_id(int(row["id"]))
        qualifying_urls = {str(item["article_url"]) for item in coverages}
        pack = PerplexityEvidenceAdapter().build(case_id=case_id, dossier=dossier, qualifying_urls=qualifying_urls)
        if not pack.source_catalog:
            return None, "no_source_catalog"
        stable_id = "evidence_legacy_" + sha256(f"{case_id}|{row.get('dossier_id')}".encode()).hexdigest()[:20]
        completed = datetime.fromisoformat(str(row["dossier_updated_at"]).replace("Z", "+00:00")) if row.get("dossier_updated_at") else datetime.now(UTC)
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=UTC)
        pack = replace(pack, evidence_pack_id=stable_id, research_completed_at=completed)
        pack.__post_init__()
        return pack, "validated"
    except Exception as exc:
        return None, f"validation_warning:{type(exc).__name__}"


def set_shadow_mode_in_dotenv(dotenv_path: Path) -> bool:
    """Persist only the Case mode key without reading or exposing secret values."""
    path = Path(dotenv_path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    key = "AD_DOCTOR_CASE_FILES_MODE"
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith(key + "="):
            updated.append(f"{key}=shadow");replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(f"{key}=shadow")
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    return True
