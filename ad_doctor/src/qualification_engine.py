from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import json

from src.config import DATABASE_PATH
from src.db import (
    count_active_publisher_networks,
    get_connection,
    list_topics,
    log_run_event,
    update_topic_status,
    upsert_topic_score,
)
from src.research_engine import select_topic_specific_articles
from src.case_files import get_case_lifecycle_service


UNIQUE_SOURCE_QUALIFICATION_GATE = 3


@dataclass(frozen=True)
class QualificationSummary:
    run_id: str
    active_source_count: int
    minimum_required_sources: int
    topics_checked: int
    qualified_topics: int
    rejected_topics: int


def minimum_required_sources(active_source_count: int) -> int:
    if active_source_count <= 0:
        return 0
    return UNIQUE_SOURCE_QUALIFICATION_GATE


def calculate_topic_score(
    topic_id: int,
    active_source_count: int,
    unique_source_coverage_count: int,
    scoring_inputs: dict[str, object],
) -> dict[str, object]:
    minimum_required = minimum_required_sources(active_source_count)
    coverage_percentage = (
        unique_source_coverage_count / active_source_count if active_source_count else 0
    )
    coverages = list(scoring_inputs.get("coverages", []))
    credibility_values = [int(row.get("credibility_tier") or 1) for row in coverages]
    credibility_score = (sum(credibility_values) / max(1, len(credibility_values))) / 5
    data_richness_score = min(
        1.0,
        sum(int(row.get("article_text_length") or 0) for row in coverages) / max(1, len(coverages)) / 4000,
    )
    freshness_score = 1.0 if coverages else 0.0
    official_reference_score = 0.0
    priority_boost_applied = 1 if coverage_percentage >= 0.40 else 0
    total_score = (
        coverage_percentage * 0.40
        + credibility_score * 0.20
        + freshness_score * 0.15
        + data_richness_score * 0.15
        + official_reference_score * 0.05
        + priority_boost_applied * 0.05
    )

    return {
        "topic_id": topic_id,
        "active_source_count": active_source_count,
        "unique_source_coverage_count": unique_source_coverage_count,
        "minimum_required_sources": minimum_required,
        "coverage_percentage": coverage_percentage,
        "credibility_score": round(credibility_score, 4),
        "freshness_score": round(freshness_score, 4),
        "data_richness_score": round(data_richness_score, 4),
        "official_reference_score": round(official_reference_score, 4),
        "priority_boost_applied": priority_boost_applied,
        "total_score": round(total_score, 4),
    }


def qualify_topics(
    database_path=DATABASE_PATH,
    run_id: str | None = None,
) -> QualificationSummary:
    run_id = run_id or datetime.now(UTC).strftime("run_%Y%m%d_%H%M%S")

    shadow_rows: list[tuple[dict[str, object], list[dict[str, object]], dict[str, object], bool]] = []
    with closing(get_connection(database_path)) as connection:
        topics = list_topics(connection, limit=None)
        active_source_count = count_active_publisher_networks(connection)
        minimum_required = minimum_required_sources(active_source_count)
        qualified = 0
        rejected = 0

        log_run_event(
            connection,
            run_id,
            "phase_5_qualification",
            "start",
            f"Starting qualification for {len(topics)} topic(s). Gate: {minimum_required} source(s).",
        )

        for topic in topics:
            topic_id = int(topic["id"])
            scoring_inputs = get_exact_topic_scoring_inputs(connection, topic)
            unique_source_coverage_count = count_unique_exact_sources(scoring_inputs["coverages"])
            score = calculate_topic_score(
                topic_id=topic_id,
                active_source_count=active_source_count,
                unique_source_coverage_count=unique_source_coverage_count,
                scoring_inputs=scoring_inputs,
            )
            upsert_topic_score(connection, score)
            if unique_source_coverage_count >= minimum_required and minimum_required > 0:
                update_topic_status(connection, topic_id, "qualified")
                qualified += 1
            else:
                update_topic_status(connection, topic_id, "rejected")
                rejected += 1
            shadow_rows.append((dict(topic), list(scoring_inputs["coverages"]), score, unique_source_coverage_count >= minimum_required and minimum_required > 0))

        summary = QualificationSummary(
            run_id=run_id,
            active_source_count=active_source_count,
            minimum_required_sources=minimum_required,
            topics_checked=len(topics),
            qualified_topics=qualified,
            rejected_topics=rejected,
        )
        log_run_event(
            connection,
            run_id,
            "phase_5_qualification",
            "complete",
            f"Qualification complete: {qualified} qualified, {rejected} rejected.",
            json.dumps(summary.__dict__),
        )
        connection.commit()
    lifecycle = get_case_lifecycle_service(database_path)
    if lifecycle:
        for topic, coverages, score, is_qualified in shadow_rows:
            try:
                lifecycle.sync_qualification(topic, coverages, score, qualified=is_qualified, run_id=run_id)
            except Exception as exc:
                with closing(get_connection(database_path)) as connection:
                    log_run_event(connection, run_id, "case_shadow_qualification", "error", f"Case shadow write failed for topic {topic['id']}: {exc}", json.dumps({"run_id":run_id,"topic_id":topic["id"],"stage":"qualification","error_type":type(exc).__name__}))
                    connection.commit()
    return summary


def get_exact_topic_scoring_inputs(connection, topic: dict[str, object]) -> dict[str, object]:
    rows = connection.execute(
        """
        SELECT
            ts.source_id,
            s.source_name,
            COALESCE(NULLIF(s.publisher_network,''),s.source_name) AS publisher_network,
            s.credibility_tier,
            a.id AS article_id,
            a.article_url,
            a.headline,
            a.meta_description,
            a.article_text,
            a.published_date,
            a.fetch_timestamp,
            LENGTH(COALESCE(a.article_text, '')) AS article_text_length
        FROM topic_sources ts
        JOIN sources s ON s.id = ts.source_id
        JOIN articles a ON a.id = ts.article_id
        WHERE ts.topic_id = ?
        """,
        (int(topic["id"]),),
    ).fetchall()
    articles = [dict(row) for row in rows]
    exact_articles = select_topic_specific_articles(str(topic["exact_topic"]), articles)
    return {"coverages": exact_articles}


def count_unique_exact_sources(coverages: object) -> int:
    if not isinstance(coverages, list):
        return 0
    return len({str(row.get("publisher_network") or row.get("source_name") or row.get("source_id")).strip().lower() for row in coverages if row.get("publisher_network") or row.get("source_name") or row.get("source_id") is not None})
