from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.db import get_connection
from src.domain.evidence_pack import EvidencePack
import json


@dataclass(frozen=True, slots=True)
class ConsistencyIssue:
    code: str
    topic_id: int | None
    case_id: str | None
    detail: str


def check_shadow_consistency(database_path: Path) -> list[ConsistencyIssue]:
    """Read-only comparison of legacy and shadow state; never repairs records."""
    connection = get_connection(Path(database_path))
    try:
        issues: list[ConsistencyIssue] = []
        rows = connection.execute("SELECT t.id topic_id,t.status,ts.unique_source_coverage_count,c.* FROM topics t LEFT JOIN topic_scores ts ON ts.topic_id=t.id LEFT JOIN cases c ON c.topic_id=t.id").fetchall()
        for row in rows:
            data=dict(row); topic_id=int(data["topic_id"])
            if data["status"] == "qualified" and not data.get("case_id"):
                issues.append(ConsistencyIssue("qualified_topic_missing_case", topic_id, None, "Qualified legacy topic has no Case File.")); continue
            if not data.get("case_id"): continue
            if str(data["status"]) != str(data["qualification_status"]):
                issues.append(ConsistencyIssue("qualification_status_mismatch", topic_id, data["case_id"], "Legacy and Case qualification states differ."))
            if int(data.get("unique_source_coverage_count") or 0) != int(data.get("unique_publication_count") or 0):
                issues.append(ConsistencyIssue("publication_count_mismatch", topic_id, data["case_id"], "Distinct publication counts differ."))
        for row in connection.execute("SELECT c.case_id,c.topic_id FROM cases c LEFT JOIN topics t ON t.id=c.topic_id WHERE t.id IS NULL"):
            issues.append(ConsistencyIssue("case_missing_topic", row["topic_id"], row["case_id"], "Case File references no topic."))
        for row in connection.execute("SELECT c.case_id,c.topic_id FROM cases c JOIN locked_topics lt ON lt.topic_id=c.topic_id JOIN research_dossiers rd ON rd.locked_topic_id=lt.id LEFT JOIN evidence_packs ep ON ep.case_id=c.case_id WHERE ep.evidence_pack_id IS NULL"):
            issues.append(ConsistencyIssue("dossier_missing_evidence_pack", row["topic_id"], row["case_id"], "Research dossier has no Evidence Pack."))
        for row in connection.execute("SELECT c.case_id,c.topic_id FROM cases c JOIN locked_topics lt ON lt.topic_id=c.topic_id JOIN content_outputs co ON co.locked_topic_id=lt.id WHERE c.draft_id IS NULL"):
            issues.append(ConsistencyIssue("draft_missing_case_link", row["topic_id"], row["case_id"], "Legacy draft is not linked to its Case File."))
        for row in connection.execute("SELECT c.case_id,c.topic_id,c.workflow_status,EXISTS(SELECT 1 FROM locked_topics lt JOIN content_outputs co ON co.locked_topic_id=lt.id WHERE lt.topic_id=c.topic_id) has_draft,EXISTS(SELECT 1 FROM locked_topics lt JOIN research_dossiers rd ON rd.locked_topic_id=lt.id WHERE lt.topic_id=c.topic_id) has_research FROM cases c"):
            if row["has_draft"] and row["workflow_status"] not in {"content_drafted","quality_review_pending","quality_review_failed","editorial_review_pending","revision_requested","approved","publishing","published","email_pending","emailed","archived"}:
                issues.append(ConsistencyIssue("workflow_inconsistent_with_draft", row["topic_id"], row["case_id"], "Draft exists but workflow has not reached content_drafted."))
            elif row["has_research"] and row["workflow_status"] in {"discovered","qualification_pending","qualified","research_pending","researching"}:
                issues.append(ConsistencyIssue("workflow_inconsistent_with_research", row["topic_id"], row["case_id"], "Research exists but workflow has not reached research_complete."))
        for row in connection.execute("SELECT evidence_pack_id,case_id,payload_json FROM evidence_packs"):
            try: EvidencePack.from_dict(json.loads(row["payload_json"]))
            except Exception as exc: issues.append(ConsistencyIssue("evidence_pack_invalid_sources", None, row["case_id"], f"Evidence Pack {row['evidence_pack_id']} is invalid: {exc}"))
        for row in connection.execute("SELECT topic_id,COUNT(*) n FROM cases GROUP BY topic_id HAVING n>1"):
            issues.append(ConsistencyIssue("duplicate_case_file", row["topic_id"], None, "More than one Case File exists for the topic."))
        for row in connection.execute("SELECT case_id,article_id,COUNT(*) n FROM case_article_links GROUP BY case_id,article_id HAVING n>1"):
            issues.append(ConsistencyIssue("duplicate_case_article_link", None, row["case_id"], f"Article {row['article_id']} linked more than once."))
        return issues
    finally:
        connection.close()
