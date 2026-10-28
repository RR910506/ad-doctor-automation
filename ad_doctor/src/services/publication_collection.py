"""Connect the approved publication list to full-text collection and qualification."""

from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from uuid import uuid4
from src.db import initialize_database, get_connection, list_topics
from src.source_manager import import_sources_from_file
from src.services.layered_collection import (
    fetch_articles,
    source_reports,
    export_frontier,
    import_frontier,
)
from src.topic_extractor import extract_topics
from src.qualification_engine import (
    qualify_topics,
    get_exact_topic_scoring_inputs,
    count_unique_exact_sources,
)

SOURCE_FILE = (
    Path(__file__).resolve().parents[2]
    / "data/input/approved_publications_2026-10-26.csv"
)


def collect_publications(
    database_path,
    *,
    run_id=None,
    source_time_budget_seconds=60,
    max_pages_per_source=60,
    frontier_checkpoint=None,
    progress_callback=None,
    lookback_hours=72,
):
    database_path = Path(database_path)
    initialize_database(database_path)
    with closing(get_connection(database_path)) as connection:
        empty = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    if empty:
        with SOURCE_FILE.open("rb") as source:
            import_sources_from_file(
                source, filename=SOURCE_FILE.name, database_path=database_path
            )
    import_frontier(database_path, frontier_checkpoint)
    run_id = run_id or "collection_" + uuid4().hex
    summary = fetch_articles(
        database_path=database_path,
        run_id=run_id,
        fetch_full_text=True,
        exhaustive_scan=True,
        max_articles_per_source=None,
        source_time_budget_seconds=source_time_budget_seconds,
        source_parallelism=4,
        max_pages_per_source=max_pages_per_source,
        progress_callback=progress_callback,
    )
    extract_topics(
        database_path=database_path, run_id=run_id, article_fetch_run_id=run_id
    )
    qualify_topics(database_path=database_path, run_id=run_id)
    candidates = []
    with closing(get_connection(database_path)) as connection:
        articles_saved = connection.execute(
            "SELECT COUNT(*) FROM articles WHERE fetch_run_id=?", (run_id,)
        ).fetchone()[0]
        previews = [
            dict(row)
            for row in connection.execute(
                """SELECT headline,article_url,source_name,published_date,
            fetch_timestamp AS fetched_at FROM articles WHERE fetch_run_id=? ORDER BY fetch_timestamp DESC,id DESC LIMIT 100""",
                (run_id,),
            )
        ]
        for topic in list_topics(connection, limit=None):
            if topic["status"] != "qualified":
                continue
            articles = get_exact_topic_scoring_inputs(connection, topic)["coverages"]
            # Research only current-run, active-publication full text, one article per publisher.
            selected = {}
            for article in articles:
                active = connection.execute(
                    "SELECT 1 FROM articles a JOIN sources s ON s.id=a.source_id WHERE a.id=? AND a.fetch_run_id=? AND s.active_status=1",
                    (article["article_id"], run_id),
                ).fetchone()
                if (
                    active
                    and _recent(article.get("published_date"), lookback_hours)
                    and len(str(article.get("article_text") or "").strip()) >= 300
                ):
                    network = str(
                        article.get("publisher_network") or article["source_name"]
                    ).lower()
                    selected.setdefault(network, article)
            articles = list(selected.values())[:6]
            if count_unique_exact_sources(articles) < 3:
                continue
            material = "\n\n".join(
                f"Publication: {a['source_name']}\nURL: {a['article_url']}\nHeadline: {a['headline']}\nExcerpt: {a['article_text'][:5000]}"
                for a in articles
            )
            candidates.append(
                {
                    "topic": topic["exact_topic"],
                    "material": material,
                    "publications": len(articles),
                    "sources": [
                        dict(article, approved=True, active_status=1)
                        for article in articles
                    ],
                }
            )
    checkpoint = export_frontier(database_path)
    reports = source_reports(database_path, run_id)
    overflow_by_source = {
        item["website_url"]: item["overflow_urls"] for item in checkpoint["sources"]
    }
    for report in reports:
        report["checkpoint_overflow_urls"] = overflow_by_source.get(
            report.get("website_url"), 0
        )
    return {
        "run_id": run_id,
        "collection": asdict(summary),
        "topics": candidates,
        "articles": previews,
        "articles_saved": articles_saved,
        "source_reports": reports,
        "frontier_checkpoint": checkpoint,
        "checkpoint_overflow_urls": checkpoint["overflow_urls"],
        "coverage_status": "bounded_scan",
        "limitations": f"{source_time_budget_seconds} seconds and {max_pages_per_source} page attempts per source per run. Known unread URLs remain queued. Robots rules, blocks and paywalls are reported. Eligible topics need publication dates within {lookback_hours} hours; undated or older articles cannot become today’s draft. Complete coverage is unknown. Section-only publications use identifiable main-content story cards; unsupported layouts require a source adapter. Remote crawl checkpoints retain all discovered URLs; storage quota failures must be resolved before unattended collection can continue.",
    }


def _recent(value, lookback_hours):
    if not value:
        return False
    try:
        published = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            published = parsedate_to_datetime(str(value))
        except (TypeError, ValueError, OverflowError):
            return False
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    return (
        now - timedelta(hours=lookback_hours)
        <= published
        <= now + timedelta(minutes=15)
    )
