from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import json

from src.config import DATABASE_PATH
from src.db import get_connection, list_topic_scores, log_run_event


@dataclass(frozen=True)
class PrioritizationSummary:
    run_id: str
    qualified_topics: int
    shortlisted_topics: int
    topic_ids: list[int]


def prioritize_topics(
    database_path=DATABASE_PATH,
    run_id: str | None = None,
    limit: int = 10,
) -> PrioritizationSummary:
    run_id = run_id or datetime.now(UTC).strftime("run_%Y%m%d_%H%M%S")
    with closing(get_connection(database_path)) as connection:
        qualified_topics = list_topic_scores(connection, qualified_only=True)
        shortlisted = qualified_topics[:limit]
        topic_ids = [int(topic["topic_id"]) for topic in shortlisted]
        summary = PrioritizationSummary(
            run_id=run_id,
            qualified_topics=len(qualified_topics),
            shortlisted_topics=len(shortlisted),
            topic_ids=topic_ids,
        )
        log_run_event(
            connection,
            run_id,
            "phase_5_prioritization",
            "complete",
            f"Prioritized {len(shortlisted)} topic(s) from {len(qualified_topics)} qualified topic(s).",
            json.dumps(summary.__dict__),
        )
        connection.commit()
        return summary
