from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import json

from src.config import DATABASE_PATH
from src.db import get_connection, list_locked_topics, list_topic_scores, lock_topic, log_run_event


@dataclass(frozen=True)
class TopicLockSummary:
    run_id: str
    requested_limit: int
    locked_count: int
    topic_ids: list[int]


def lock_top_topics(
    database_path=DATABASE_PATH,
    run_id: str | None = None,
    limit: int = 10,
) -> TopicLockSummary:
    run_id = run_id or datetime.now(UTC).strftime("run_%Y%m%d_%H%M%S")
    with closing(get_connection(database_path)) as connection:
        qualified_topics = list_topic_scores(connection, qualified_only=True)[:limit]
        topic_ids: list[int] = []
        for topic in qualified_topics:
            topic_id = int(topic["topic_id"])
            lock_topic(
                connection,
                topic_id=topic_id,
                run_id=run_id,
                locked_topic=str(topic["exact_topic"]),
            )
            topic_ids.append(topic_id)

        summary = TopicLockSummary(
            run_id=run_id,
            requested_limit=limit,
            locked_count=len(topic_ids),
            topic_ids=topic_ids,
        )
        log_run_event(
            connection,
            run_id,
            "phase_6_topic_lock",
            "complete",
            f"Locked {len(topic_ids)} topic(s) for run {run_id}.",
            json.dumps(summary.__dict__),
        )
        connection.commit()
        return summary


def get_locked_topics(database_path=DATABASE_PATH, run_id: str | None = None):
    with closing(get_connection(database_path)) as connection:
        return list_locked_topics(connection, run_id=run_id)
