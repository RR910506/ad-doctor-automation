from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import traceback

from src.automation_engine import get_automation_settings
from src.app_scheduler import claim_latest_due_run, run_claimed_schedule
from src.config import DATABASE_PATH
from src.db import get_connection, initialize_database, log_run_event
from src.notification_engine import notify_daily_scrape_complete
from src.pipeline_runner import PipelineSummary, run_real_pipeline


@dataclass(frozen=True)
class FailedPipelineSummary:
    run_id: str


def main() -> int:
    initialize_database(DATABASE_PATH)
    settings = get_automation_settings(DATABASE_PATH)
    run_id = datetime.now(UTC).strftime("daily_%Y%m%d_%H%M%S")
    if not settings.enabled:
        log_daily_event(run_id, "skipped", "Daily automation is disabled.")
        print(json.dumps({"run_id": run_id, "status": "skipped", "reason": "disabled"}))
        return 0

    claimed = claim_latest_due_run(settings, database_path=DATABASE_PATH)
    if not claimed:
        log_daily_event(
            run_id,
            "skipped",
            "No unclaimed scheduled time is due. Another scheduler may already own this run.",
        )
        print(json.dumps({"run_id": run_id, "status": "skipped", "reason": "no_due_slot"}))
        return 0

    schedule_key, schedule_time = claimed
    success = run_claimed_schedule(
        schedule_key=schedule_key,
        schedule_time=schedule_time,
        settings=settings,
        database_path=DATABASE_PATH,
    )
    print(
        json.dumps(
            {
                "schedule_key": schedule_key,
                "scheduled_time": schedule_time,
                "status": "complete" if success else "error",
            },
            indent=2,
        )
    )
    return 0 if success else 1


def build_output(summary: PipelineSummary, notification_summary) -> dict[str, object]:
    return {
        "run_id": summary.run_id,
        "status": "complete",
        "articles_seen": summary.articles_seen,
        "articles_inserted": summary.articles_inserted,
        "articles_updated": summary.articles_updated,
        "fetch_failures": summary.fetch_failures,
        "qualified_topics": summary.qualified_topics,
        "locked_topics": summary.locked_topics,
        "blog_drafts": summary.blog_drafts,
        "social_drafts": summary.social_drafts,
        "notification": notification_summary.__dict__ if notification_summary else None,
    }


def log_daily_event(
    run_id: str,
    event_type: str,
    message: str,
    metadata: dict[str, object] | None = None,
) -> None:
    with closing(get_connection(DATABASE_PATH)) as connection:
        log_run_event(
            connection,
            run_id,
            "daily_automation",
            event_type,
            message,
            json.dumps(metadata) if metadata else None,
        )
        connection.commit()


if __name__ == "__main__":
    raise SystemExit(main())
