from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import json

from src.article_fetcher import fetch_articles
from src.config import DATABASE_PATH
from src.coverage_backfill import backfill_near_qualified_topic_coverage, expand_topic_coverage_from_stored_articles
from src.content_engine import generate_content_outputs
from src.db import get_connection, initialize_database, log_run_event
from src.dissection_engine import run_campaign_dissections
from src.prioritization_engine import prioritize_topics
from src.qualification_engine import qualify_topics
from src.research_engine import build_research_dossiers
from src.topic_extractor import extract_topics
from src.topic_lock_engine import lock_top_topics


@dataclass(frozen=True)
class PipelineSummary:
    run_id: str
    articles_seen: int
    articles_inserted: int
    articles_updated: int
    fetch_failures: int
    topics_linked: int
    topics_skipped: int
    qualified_topics: int
    rejected_topics: int
    locked_topics: int
    research_dossiers: int
    dissections: int
    blog_drafts: int
    social_drafts: int


@dataclass(frozen=True)
class ContentRecoverySummary:
    run_id: str
    qualified_topics: int
    locked_topics: int
    research_dossiers: int
    dissections: int
    blog_drafts: int
    social_drafts: int


def generate_content_from_qualified_topics(
    database_path=DATABASE_PATH,
    run_id: str | None = None,
) -> ContentRecoverySummary:
    initialize_database(database_path=database_path)
    run_id = run_id or datetime.now(UTC).strftime("recovery_%Y%m%d_%H%M%S")
    with closing(get_connection(database_path)) as connection:
        qualified_topics = int(
            connection.execute(
                "SELECT COUNT(*) FROM topics WHERE status = 'qualified'"
            ).fetchone()[0]
        )
    if qualified_topics == 0:
        raise ValueError("No qualified topics are available for content generation.")

    prioritize_topics(database_path=database_path, run_id=run_id, limit=qualified_topics)
    lock_summary = lock_top_topics(
        database_path=database_path,
        run_id=run_id,
        limit=qualified_topics,
    )
    research_summary = build_research_dossiers(database_path=database_path, run_id=run_id)
    dissection_summary = run_campaign_dissections(database_path=database_path, run_id=run_id)
    content_summary = generate_content_outputs(database_path=database_path, run_id=run_id)
    summary = ContentRecoverySummary(
        run_id=run_id,
        qualified_topics=qualified_topics,
        locked_topics=lock_summary.locked_count,
        research_dossiers=research_summary.dossiers_created,
        dissections=dissection_summary.dissections_created,
        blog_drafts=content_summary.blog_drafts_created,
        social_drafts=content_summary.social_drafts_created,
    )
    with closing(get_connection(database_path)) as connection:
        log_run_event(
            connection,
            run_id,
            "content_recovery",
            "complete",
            (
                f"Recovered {summary.blog_drafts} blog draft(s) and "
                f"{summary.social_drafts} social draft(s) from qualified topics."
            ),
            json.dumps(summary.__dict__),
        )
        connection.commit()
    return summary


def run_real_pipeline(
    database_path=DATABASE_PATH,
    max_articles_per_source: int | None = None,
    lock_limit: int | None = None,
    fetch_source_time_budget_seconds: int = 90,
    run_id: str | None = None,
) -> PipelineSummary:
    initialize_database(database_path=database_path)
    run_id = run_id or datetime.now(UTC).strftime("auto_%Y%m%d_%H%M%S")
    fetch_summary = fetch_articles(
        max_articles_per_source=max_articles_per_source,
        database_path=database_path,
        run_id=run_id,
        exhaustive_scan=True,
        source_time_budget_seconds=fetch_source_time_budget_seconds,
    )

    if fetch_summary.inserted + fetch_summary.updated == 0:
        summary = PipelineSummary(
            run_id=run_id,
            articles_seen=fetch_summary.articles_seen,
            articles_inserted=fetch_summary.inserted,
            articles_updated=fetch_summary.updated,
            fetch_failures=fetch_summary.failed,
            topics_linked=0,
            topics_skipped=0,
            qualified_topics=0,
            rejected_topics=0,
            locked_topics=0,
            research_dossiers=0,
            dissections=0,
            blog_drafts=0,
            social_drafts=0,
        )
        with closing(get_connection(database_path)) as connection:
            log_run_event(
                connection,
                run_id,
                "pipeline",
                "aborted",
                "No valid current-run articles were stored; existing derived outputs were left unchanged.",
                json.dumps(summary.__dict__),
            )
            connection.commit()
        return summary

    with closing(get_connection(database_path)) as connection:
        log_run_event(
            connection,
            run_id,
            "pipeline",
            "preserve",
            (
                "Preserving existing topics and content while the new run is built. "
                "A failed or interrupted run cannot remove the last successful batch."
            ),
            json.dumps({"content_preservation": True}),
        )
        connection.commit()

    topic_summary = extract_topics(
        database_path=database_path,
        run_id=run_id,
        article_fetch_run_id=None,
        article_limit=None,
    )
    expand_topic_coverage_from_stored_articles(
        database_path=database_path,
        run_id=run_id,
        topic_limit=None,
    )
    qualification_summary = qualify_topics(database_path=database_path, run_id=run_id)
    coverage_search_summary = backfill_near_qualified_topic_coverage(
        database_path=database_path,
        run_id=run_id,
        topic_limit=50,
        max_coverage_gap=None,
        search_urls_per_source=2,
        source_time_budget_seconds=8,
        named_events_only=True,
    )
    if coverage_search_summary.matched_articles:
        expand_topic_coverage_from_stored_articles(
            database_path=database_path,
            run_id=run_id,
            topic_limit=None,
        )
        qualification_summary = qualify_topics(database_path=database_path, run_id=run_id)
    effective_lock_limit = (
        qualification_summary.qualified_topics if lock_limit is None else lock_limit
    )
    prioritize_topics(database_path=database_path, run_id=run_id, limit=effective_lock_limit)
    lock_summary = lock_top_topics(database_path=database_path, run_id=run_id, limit=effective_lock_limit)
    research_summary = build_research_dossiers(database_path=database_path, run_id=run_id)
    dissection_summary = run_campaign_dissections(database_path=database_path, run_id=run_id)
    content_summary = generate_content_outputs(database_path=database_path, run_id=run_id)

    summary = PipelineSummary(
        run_id=run_id,
        articles_seen=fetch_summary.articles_seen,
        articles_inserted=fetch_summary.inserted,
        articles_updated=fetch_summary.updated,
        fetch_failures=fetch_summary.failed,
        topics_linked=topic_summary.linked_coverages,
        topics_skipped=topic_summary.skipped_articles,
        qualified_topics=qualification_summary.qualified_topics,
        rejected_topics=qualification_summary.rejected_topics,
        locked_topics=lock_summary.locked_count,
        research_dossiers=research_summary.dossiers_created,
        dissections=dissection_summary.dissections_created,
        blog_drafts=content_summary.blog_drafts_created,
        social_drafts=content_summary.social_drafts_created,
    )
    with closing(get_connection(database_path)) as connection:
        log_run_event(
            connection,
            run_id,
            "pipeline",
            "complete",
            "Automatic real-source pipeline completed.",
            json.dumps(summary.__dict__),
        )
        connection.commit()
    return summary
