from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import json

from src.config import DATABASE_PATH
from src.content_engine import generate_content_outputs
from src.db import clear_derived_pipeline_data, get_connection, initialize_database, list_active_sources, log_run_event, upsert_article, upsert_source
from src.dissection_engine import run_campaign_dissections
from src.qualification_engine import qualify_topics
from src.research_engine import build_research_dossiers
from src.topic_extractor import extract_topics
from src.topic_lock_engine import lock_top_topics


@dataclass(frozen=True)
class DemoRunSummary:
    run_id: str
    demo_articles_inserted: int
    topics_locked: int
    blog_drafts_created: int
    social_drafts_created: int


def run_demo_pipeline(database_path=DATABASE_PATH) -> DemoRunSummary:
    initialize_database(database_path=database_path)
    run_id = datetime.now(UTC).strftime("demo_%Y%m%d_%H%M%S")
    demo_articles_inserted = seed_demo_articles(database_path=database_path, run_id=run_id)
    with closing(get_connection(database_path)) as connection:
        clear_derived_pipeline_data(connection)
        log_run_event(
            connection,
            run_id,
            "phase_10_demo",
            "reset",
            "Cleared derived data before rebuilding demo outputs.",
        )
        connection.commit()
    extract_topics(
        database_path=database_path,
        run_id=run_id,
        article_fetch_run_id=run_id,
        include_demo_articles=True,
    )
    qualify_topics(database_path=database_path, run_id=run_id)
    lock_summary = lock_top_topics(database_path=database_path, run_id=run_id, limit=10)
    build_research_dossiers(database_path=database_path, run_id=run_id)
    run_campaign_dissections(database_path=database_path, run_id=run_id)
    content_summary = generate_content_outputs(database_path=database_path, run_id=run_id)
    summary = DemoRunSummary(
        run_id=run_id,
        demo_articles_inserted=demo_articles_inserted,
        topics_locked=lock_summary.locked_count,
        blog_drafts_created=content_summary.blog_drafts_created,
        social_drafts_created=content_summary.social_drafts_created,
    )
    with closing(get_connection(database_path)) as connection:
        log_run_event(
            connection,
            run_id,
            "phase_10_demo",
            "complete",
            "Demo pipeline completed.",
            json.dumps(summary.__dict__),
        )
        connection.commit()
    return summary


def seed_demo_articles(database_path=DATABASE_PATH, run_id: str | None = None) -> int:
    initialize_database(database_path=database_path)
    run_id = run_id or datetime.now(UTC).strftime("demo_%Y%m%d_%H%M%S")
    with closing(get_connection(database_path)) as connection:
        sources = list_active_sources(connection)
        if len(sources) < 6:
            for index in range(1, 7):
                upsert_source(
                    connection,
                    {
                        "source_name": f"Demo Source {index}",
                        "website_url": f"https://demo-source-{index}.example.com",
                        "rss_url": None,
                        "category": "Demo",
                        "country": "Global",
                        "credibility_tier": 2,
                        "active_status": 1,
                    },
                )
            sources = list_active_sources(connection)

        headlines = [
            "Nike launches AI-powered Olympic campaign",
            "Nike launches AI-powered Olympic campaign",
            "Nike launches AI-powered Olympic campaign",
            "Nike launches AI-powered Olympic campaign",
            "Nike launches AI-powered Olympic campaign",
            "Nike launches AI-powered Olympic campaign",
        ]
        inserted = 0
        for source, headline in zip(sources[:6], headlines):
            outcome = upsert_article(
                connection,
                {
                    "source_id": source["id"],
                    "source_name": source["source_name"],
                    "source_url": source["website_url"],
                    "article_url": f"{source['website_url']}/demo/{run_id}/nike-ai-olympics",
                    "headline": headline,
                    "published_date": "2026-06-03",
                    "author": "Demo Desk",
                    "meta_description": "Demo coverage for Ad Doctor MVP validation.",
                    "article_text": (
                        "Nike launched an AI-powered Olympics campaign using athlete stories, "
                        "YouTube, Instagram, and social media activations. The campaign objective "
                        "is to build cultural attention before the Games. Early coverage notes "
                        "the creative idea, execution, and platform rollout."
                    ),
                    "fetch_run_id": run_id,
                },
            )
            if outcome == "inserted":
                inserted += 1

        log_run_event(
            connection,
            run_id,
            "phase_10_demo",
            "seed",
            f"Seeded {inserted} demo article(s).",
        )
        connection.commit()
        return inserted
