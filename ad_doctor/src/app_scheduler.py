from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
import sqlite3
import threading
import time
import traceback
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.automation_engine import get_automation_settings
from src.config import DATABASE_PATH
from src.db import get_connection, initialize_database, log_run_event
from src.notification_engine import notify_daily_scrape_complete
from src.pipeline_runner import PipelineSummary, run_real_pipeline


DEFAULT_POLL_SECONDS = 60
DEFAULT_CATCHUP_HOURS = 24
WORKER_HEARTBEAT_STALE_SECONDS = 180
DEFAULT_JOB_TIMEOUT_SECONDS = 60 * 60 * 3
TERMINAL_RUN_STATUSES = {"complete", "completed", "failed", "error", "timed_out", "recovered"}


@dataclass(frozen=True)
class AppSchedulerStatus:
    enabled_by_env: bool
    running: bool
    message: str


@dataclass(frozen=True)
class FailedPipelineSummary:
    run_id: str


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


_scheduler_lock = threading.Lock()
_scheduler_thread: threading.Thread | None = None
_scheduler_stop_event = threading.Event()
_last_status = AppSchedulerStatus(
    enabled_by_env=False,
    running=False,
    message="App scheduler has not started.",
)


def is_app_scheduler_enabled() -> bool:
    value = os.getenv("AD_DOCTOR_APP_SCHEDULER_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def start_app_scheduler() -> AppSchedulerStatus:
    global _scheduler_thread, _last_status
    if get_scheduler_mode() == "external":
        _last_status = get_external_scheduler_status()
        return _last_status
    if not is_app_scheduler_enabled():
        _last_status = AppSchedulerStatus(
            enabled_by_env=False,
            running=False,
            message="App scheduler is disabled by AD_DOCTOR_APP_SCHEDULER_ENABLED.",
        )
        return _last_status

    with _scheduler_lock:
        if _scheduler_thread and _scheduler_thread.is_alive():
            _last_status = AppSchedulerStatus(
                enabled_by_env=True,
                running=True,
                message="App scheduler is running.",
            )
            return _last_status

        _scheduler_stop_event.clear()
        _scheduler_thread = threading.Thread(
            target=_scheduler_loop,
            name="ad-doctor-app-scheduler",
            daemon=True,
        )
        _scheduler_thread.start()
        _last_status = AppSchedulerStatus(
            enabled_by_env=True,
            running=True,
            message="App scheduler started.",
        )
        return _last_status


def get_app_scheduler_status() -> AppSchedulerStatus:
    scheduler_mode = get_scheduler_mode()
    if scheduler_mode == "external":
        return get_external_scheduler_status()
    if scheduler_mode == "worker":
        return get_cloud_worker_status()
    if not is_app_scheduler_enabled():
        return AppSchedulerStatus(
            enabled_by_env=False,
            running=False,
            message="App scheduler is disabled by AD_DOCTOR_APP_SCHEDULER_ENABLED.",
        )
    if _scheduler_thread and _scheduler_thread.is_alive():
        return AppSchedulerStatus(
            enabled_by_env=True,
            running=True,
            message="App scheduler is running.",
        )
    return _last_status


def get_scheduler_mode() -> str:
    return os.getenv("AD_DOCTOR_SCHEDULER_MODE", "app").strip().lower()


def get_external_scheduler_status() -> AppSchedulerStatus:
    return AppSchedulerStatus(
        enabled_by_env=True,
        running=False,
        message=(
            "External scheduler mode is configured. Render worker heartbeat is not available."
        ),
    )


def get_cloud_worker_status(database_path=DATABASE_PATH) -> AppSchedulerStatus:
    try:
        with closing(get_connection(database_path)) as connection:
            row = connection.execute(
                """
                SELECT status, message, last_heartbeat_at
                FROM automation_worker_state
                WHERE id = 1
                """
            ).fetchone()
    except sqlite3.Error as exc:
        return AppSchedulerStatus(
            enabled_by_env=True,
            running=False,
            message=f"Cloud scheduler status unavailable: {exc}",
        )
    if not row or not row["last_heartbeat_at"]:
        return AppSchedulerStatus(
            enabled_by_env=True,
            running=False,
            message="Cloud scheduler is waiting for its first heartbeat.",
        )
    try:
        heartbeat = datetime.fromisoformat(str(row["last_heartbeat_at"]))
    except ValueError:
        heartbeat = datetime.min.replace(tzinfo=UTC)
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=UTC)
    is_fresh = (datetime.now(UTC) - heartbeat).total_seconds() <= WORKER_HEARTBEAT_STALE_SECONDS
    return AppSchedulerStatus(
        enabled_by_env=True,
        running=is_fresh and str(row["status"]) in {"idle", "running"},
        message=str(row["message"] or "Cloud scheduler heartbeat received."),
    )


def get_worker_state(database_path=DATABASE_PATH) -> dict[str, object] | None:
    try:
        with closing(get_connection(database_path)) as connection:
            row = connection.execute(
                """
                SELECT status, message, worker_id, last_heartbeat_at, updated_at
                FROM automation_worker_state
                WHERE id = 1
                """
            ).fetchone()
    except sqlite3.Error:
        return None
    return dict(row) if row else None


def _scheduler_loop() -> None:
    global _last_status
    poll_seconds = int(os.getenv("AD_DOCTOR_SCHEDULER_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)) or DEFAULT_POLL_SECONDS)
    while not _scheduler_stop_event.is_set():
        try:
            check_due_runs()
            _last_status = AppSchedulerStatus(
                enabled_by_env=True,
                running=True,
                message="App scheduler is running.",
            )
        except Exception as exc:
            _last_status = AppSchedulerStatus(
                enabled_by_env=True,
                running=True,
                message=f"Scheduler check failed: {exc}",
            )
        _scheduler_stop_event.wait(max(10, poll_seconds))


def check_due_runs(database_path=DATABASE_PATH) -> list[str]:
    queued_run_keys = queue_due_runs(database_path=database_path)
    settings = get_automation_settings(database_path=database_path)
    while True:
        claimed = claim_next_queued_run(database_path=database_path)
        if not claimed:
            break
        schedule_key, schedule_time = claimed
        run_claimed_schedule(
            schedule_key=schedule_key,
            schedule_time=schedule_time,
            settings=settings,
            database_path=database_path,
        )
    return queued_run_keys


def queue_due_runs(
    *,
    now: datetime | None = None,
    database_path=DATABASE_PATH,
) -> list[str]:
    initialize_database(database_path)
    mark_stale_running_jobs(database_path)
    with closing(get_connection(database_path)) as connection:
        try:
            reconcile_automation_run_history(connection)
        except sqlite3.OperationalError:
            pass
    settings = get_automation_settings(database_path=database_path)
    if not settings.enabled:
        return []

    timezone = _load_timezone(settings.timezone)
    now = now or datetime.now(timezone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone)
    else:
        now = now.astimezone(timezone)
    catchup_hours = int(
        os.getenv("AD_DOCTOR_SCHEDULER_CATCHUP_HOURS", str(DEFAULT_CATCHUP_HOURS))
        or DEFAULT_CATCHUP_HOURS
    )
    catchup_after = now - timedelta(hours=max(1, catchup_hours))
    queued_run_keys = []
    for schedule_time in settings.schedule_times or (settings.schedule_time,):
        scheduled_at = _scheduled_datetime(now, schedule_time)
        if scheduled_at > now:
            scheduled_at -= timedelta(days=1)
        if scheduled_at > now or scheduled_at < catchup_after:
            continue
        queued = queue_scheduled_run(
            scheduled_at=scheduled_at,
            schedule_time=schedule_time,
            timezone_name=settings.timezone,
            database_path=database_path,
        )
        if queued:
            queued_run_keys.append(queued)
    return queued_run_keys


def queue_scheduled_run(
    *,
    scheduled_at: datetime,
    schedule_time: str,
    timezone_name: str,
    database_path=DATABASE_PATH,
) -> str | None:
    schedule_date = scheduled_at.date().isoformat()
    schedule_key = f"{schedule_date}T{schedule_time}@{timezone_name}"
    initialize_database(database_path)
    with closing(get_connection(database_path)) as connection:
        try:
            connection.execute(
                """
                INSERT INTO automation_run_history (
                    schedule_key,
                    scheduled_date,
                    scheduled_time,
                    timezone,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 'queued', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (schedule_key, schedule_date, schedule_time, timezone_name),
            )
        except sqlite3.IntegrityError:
            return None
        connection.commit()
    return schedule_key


def claim_next_queued_run(database_path=DATABASE_PATH) -> tuple[str, str] | None:
    initialize_database(database_path)
    mark_stale_running_jobs(database_path)
    with closing(get_connection(database_path)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT 1 FROM automation_run_history WHERE status = 'running' LIMIT 1"
            ).fetchone()
            if active:
                connection.rollback()
                return None
            queued = connection.execute(
                """
                SELECT schedule_key, scheduled_time
                FROM automation_run_history
                WHERE status = 'queued'
                ORDER BY scheduled_date ASC, scheduled_time ASC, id ASC
                LIMIT 1
                """
            ).fetchone()
            if not queued:
                connection.rollback()
                return None
            updated = connection.execute(
                """
                UPDATE automation_run_history
                SET status = 'running',
                    started_at = CURRENT_TIMESTAMP,
                    finished_at = NULL,
                    error_message = NULL,
                    current_phase = 'queued',
                    current_source = NULL,
                    heartbeat_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE schedule_key = ? AND status = 'queued'
                """,
                (queued["schedule_key"],),
            ).rowcount
            connection.commit()
        except sqlite3.OperationalError:
            connection.rollback()
            return None
    if not updated:
        return None
    return str(queued["schedule_key"]), str(queued["scheduled_time"])


def requeue_interrupted_worker_runs(database_path=DATABASE_PATH) -> int:
    initialize_database(database_path)
    with closing(get_connection(database_path)) as connection:
        updated = connection.execute(
            """
            UPDATE automation_run_history
            SET status = 'recovered',
                finished_at = CURRENT_TIMESTAMP,
                error_message = 'Cloud worker restarted before this run finished; run marked recovered so it cannot stay Running forever.',
                current_phase = COALESCE(current_phase, 'worker_restart'),
                heartbeat_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
            """
        ).rowcount
        connection.commit()
    return int(updated)


def record_worker_heartbeat(
    status: str,
    message: str,
    *,
    worker_id: str | None = None,
    database_path=DATABASE_PATH,
) -> None:
    initialize_database(database_path)
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """
            INSERT INTO automation_worker_state (
                id, status, message, worker_id, last_heartbeat_at, updated_at
            )
            VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                message = excluded.message,
                worker_id = excluded.worker_id,
                last_heartbeat_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            """,
            (status, message, worker_id),
        )
        connection.commit()


def mark_stale_running_jobs(database_path=DATABASE_PATH) -> int:
    initialize_database(database_path)
    heartbeat_timeout = max(
        60,
        int(os.getenv("AD_DOCTOR_WORKER_STALE_SECONDS", str(WORKER_HEARTBEAT_STALE_SECONDS))),
    )
    job_timeout = max(
        heartbeat_timeout,
        int(os.getenv("AD_DOCTOR_JOB_TIMEOUT_SECONDS", str(DEFAULT_JOB_TIMEOUT_SECONDS))),
    )
    heartbeat_modifier = f"-{heartbeat_timeout} seconds"
    job_modifier = f"-{job_timeout} seconds"
    with closing(get_connection(database_path)) as connection:
        updated = connection.execute(
            """
            UPDATE automation_run_history
            SET status = 'timed_out',
                error_message = COALESCE(
                    error_message,
                    'Run timed out because the worker heartbeat stopped or the job exceeded the configured maximum runtime.'
                ),
                finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP),
                current_phase = COALESCE(current_phase, 'timed_out'),
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
              AND started_at IS NOT NULL
              AND (
                    datetime(started_at) < datetime('now', ?)
                 OR (
                        heartbeat_at IS NOT NULL
                    AND datetime(heartbeat_at) < datetime('now', ?)
                    )
                 OR (
                        heartbeat_at IS NULL
                    AND datetime(started_at) < datetime('now', ?)
                    )
              )
            """,
            (job_modifier, heartbeat_modifier, heartbeat_modifier),
        ).rowcount
        connection.commit()
    return int(updated)


def update_run_progress(
    schedule_key: str,
    *,
    database_path=DATABASE_PATH,
    current_phase: str | None = None,
    current_source: str | None = None,
    total_sources: int | None = None,
    completed_sources: int | None = None,
    failed_sources: int | None = None,
    articles_scanned: int | None = None,
    qualified_topics: int | None = None,
    social_drafts_created: int | None = None,
    blog_drafts_created: int | None = None,
    research_calls: int | None = None,
    tracked_tokens: int | None = None,
    estimated_credits: int | None = None,
    worker_id: str | None = None,
) -> None:
    values: list[object] = []
    nullable_fields = {
        "current_phase": current_phase,
        "current_source": current_source,
        "worker_id": worker_id,
    }
    numeric_fields = {
        "total_sources": total_sources,
        "completed_sources": completed_sources,
        "failed_sources": failed_sources,
        "articles_scanned": articles_scanned,
        "qualified_topics": qualified_topics,
        "social_drafts_created": social_drafts_created,
        "blog_drafts_created": blog_drafts_created,
        "research_calls": research_calls,
        "tracked_tokens": tracked_tokens,
        "estimated_credits": estimated_credits,
    }
    assignments = []
    for column, value in nullable_fields.items():
        if value is not None:
            assignments.append(f"{column} = ?")
            values.append(value)
    for column, value in numeric_fields.items():
        if value is not None:
            assignments.append(f"{column} = ?")
            values.append(int(value))
    assignments.extend(["heartbeat_at = CURRENT_TIMESTAMP", "updated_at = CURRENT_TIMESTAMP"])
    if not assignments:
        return
    values.append(schedule_key)
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            f"""
            UPDATE automation_run_history
            SET {', '.join(assignments)}
            WHERE schedule_key = ?
            """,
            tuple(values),
        )
        connection.commit()


def claim_latest_due_run(
    settings,
    *,
    now: datetime | None = None,
    database_path=DATABASE_PATH,
) -> tuple[str, str] | None:
    timezone = _load_timezone(settings.timezone)
    now = now or datetime.now(timezone)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone)
    else:
        now = now.astimezone(timezone)

    due_slots = []
    for schedule_time in settings.schedule_times or (settings.schedule_time,):
        scheduled_at = _scheduled_datetime(now, schedule_time)
        if scheduled_at <= now:
            due_slots.append((scheduled_at, schedule_time))

    if not due_slots:
        return None
    scheduled_at, schedule_time = max(due_slots)
    claim = claim_scheduled_run(
        scheduled_at=scheduled_at,
        schedule_time=schedule_time,
        timezone_name=settings.timezone,
        database_path=database_path,
    )
    return (claim, schedule_time) if claim else None


def claim_scheduled_run(
    *,
    scheduled_at: datetime,
    schedule_time: str,
    timezone_name: str,
    database_path=DATABASE_PATH,
) -> str | None:
    schedule_date = scheduled_at.date().isoformat()
    schedule_key = f"{schedule_date}T{schedule_time}@{timezone_name}"
    initialize_database(database_path)
    with closing(get_connection(database_path)) as connection:
        existing = connection.execute(
            "SELECT status, started_at FROM automation_run_history WHERE schedule_key = ?",
            (schedule_key,),
        ).fetchone()
        if existing:
            if str(existing["status"]) == "running" and _is_stale_running_claim(existing["started_at"]):
                connection.execute(
                    """
                    UPDATE automation_run_history
                    SET status = 'running',
                        started_at = CURRENT_TIMESTAMP,
                        finished_at = NULL,
                        error_message = 'Retrying stale running claim.',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE schedule_key = ?
                    """,
                    (schedule_key,),
                )
                connection.commit()
                return schedule_key
            return None
        try:
            connection.execute(
                """
                INSERT INTO automation_run_history (
                    schedule_key,
                    scheduled_date,
                    scheduled_time,
                    timezone,
                    status,
                    started_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 'running', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (schedule_key, schedule_date, schedule_time, timezone_name),
            )
        except sqlite3.IntegrityError:
            return None
        connection.commit()
    return schedule_key


def run_claimed_schedule(
    *,
    schedule_key: str,
    schedule_time: str,
    settings,
    database_path=DATABASE_PATH,
) -> bool:
    run_id = datetime.now(UTC).strftime("auto_%Y%m%d_%H%M%S")
    worker_id = os.getenv("RENDER_INSTANCE_ID") or os.getenv("AD_DOCTOR_WORKER_ID") or "local-worker"
    source_progress = {
        "total_sources": 0,
        "completed_sources": 0,
        "failed_sources": 0,
        "articles_scanned": 0,
    }
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """
            UPDATE automation_run_history
            SET run_id = ?,
                status = 'running',
                current_phase = 'daily_automation',
                current_source = NULL,
                worker_id = ?,
                heartbeat_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE schedule_key = ?
            """,
            (run_id, worker_id, schedule_key),
        )
        connection.commit()

    def progress_callback(event: dict[str, object]) -> None:
        event_type = str(event.get("event_type") or "")
        phase = str(event.get("phase") or "")
        source_name = str(event.get("source_name") or "") or None
        if not source_name and phase and phase != "phase_3_article_fetch":
            source_name = ""
        if event.get("total_sources") is not None:
            source_progress["total_sources"] = int(event.get("total_sources") or 0)
        if event.get("completed_sources") is not None:
            source_progress["completed_sources"] = int(event.get("completed_sources") or 0)
        if event_type in {"source_failed", "source_timed_out"}:
            source_progress["failed_sources"] += 1
        if event_type == "source_complete" and event.get("candidates_checked") is not None:
            source_progress["articles_scanned"] += int(event.get("candidates_checked") or 0)
        update_run_progress(
            schedule_key,
            database_path=database_path,
            current_phase=phase or ("phase_3_article_fetch" if event_type else "daily_automation"),
            current_source=source_name,
            total_sources=source_progress["total_sources"],
            completed_sources=source_progress["completed_sources"],
            failed_sources=source_progress["failed_sources"],
            articles_scanned=source_progress["articles_scanned"],
            worker_id=worker_id,
        )

    try:
        summary = run_real_pipeline(
            database_path=database_path,
            fetch_source_time_budget_seconds=settings.fetch_source_time_budget_seconds,
            run_id=run_id,
            progress_callback=progress_callback,
        )
        notification_summary = (
            notify_daily_scrape_complete(summary, database_path=database_path)
            if settings.notify_on_success
            else None
        )
        usage = get_research_usage_for_run(summary.run_id, database_path=database_path)
        with closing(get_connection(database_path)) as connection:
            connection.execute(
                """
                UPDATE automation_run_history
                SET run_id = ?,
                    status = 'complete',
                    current_phase = 'complete',
                    current_source = NULL,
                    articles_scanned = ?,
                    qualified_topics = ?,
                    social_drafts_created = ?,
                    blog_drafts_created = ?,
                    research_calls = ?,
                    tracked_tokens = ?,
                    estimated_credits = ?,
                    heartbeat_at = CURRENT_TIMESTAMP,
                    finished_at = CURRENT_TIMESTAMP,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE schedule_key = ?
                """,
                (
                    summary.run_id,
                    summary.articles_seen,
                    summary.qualified_topics,
                    summary.social_drafts,
                    summary.blog_drafts,
                    usage["research_calls"],
                    usage["tracked_tokens"],
                    usage["estimated_credits"],
                    schedule_key,
                ),
            )
            log_run_event(
                connection,
                summary.run_id,
                "daily_automation",
                "complete",
                (
                    f"Scheduled run {schedule_time} complete: "
                    f"{summary.qualified_topics} qualified, {summary.locked_topics} locked."
                ),
                json.dumps(
                    {
                        "summary": summary.__dict__,
                        "schedule_key": schedule_key,
                        "notification_summary": notification_summary.__dict__
                        if notification_summary
                        else None,
                        "output": build_output(summary, notification_summary),
                    }
                ),
            )
            connection.commit()
        return True
    except Exception as exc:
        error_text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        failed_summary = FailedPipelineSummary(run_id=schedule_key)
        notification_summary = (
            notify_daily_scrape_complete(
                failed_summary,
                database_path=database_path,
                error_message=error_text,
            )
            if settings.notify_on_failure
            else None
        )
        with closing(get_connection(database_path)) as connection:
            connection.execute(
                """
                UPDATE automation_run_history
                SET status = 'failed',
                    error_message = ?,
                    current_phase = COALESCE(current_phase, 'failed'),
                    heartbeat_at = CURRENT_TIMESTAMP,
                    finished_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE schedule_key = ?
                """,
                (error_text, schedule_key),
            )
            log_run_event(
                connection,
                schedule_key,
                "daily_automation",
                "error",
                f"Scheduled run {schedule_time} failed: {error_text}",
                json.dumps(
                    {
                        "schedule_key": schedule_key,
                        "traceback": traceback.format_exc(),
                        "notification_summary": notification_summary.__dict__
                        if notification_summary
                        else None,
                    }
                ),
            )
            connection.commit()
        return False


def get_research_usage_for_run(run_id: str, database_path=DATABASE_PATH) -> dict[str, int]:
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT rd.dossier_json
            FROM research_dossiers rd
            JOIN locked_topics lt ON lt.id = rd.locked_topic_id
            WHERE lt.run_id = ?
            """,
            (run_id,),
        ).fetchall()
    research_calls = 0
    tracked_tokens = 0
    for row in rows:
        try:
            payload = json.loads(row["dossier_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        research_payload = payload.get("perplexity_research") if isinstance(payload, dict) else None
        if not isinstance(research_payload, dict):
            continue
        if research_payload.get("status") == "completed":
            research_calls += 1
        usage = research_payload.get("usage")
        if isinstance(usage, dict):
            tracked_tokens += int(
                usage.get("total_tokens")
                or usage.get("total")
                or usage.get("tokens")
                or 0
            )
    return {
        "research_calls": research_calls,
        "tracked_tokens": tracked_tokens,
        "estimated_credits": research_calls,
    }


def list_automation_run_history(limit: int = 25, database_path=DATABASE_PATH) -> list[dict[str, object]]:
    mark_stale_running_jobs(database_path)
    with closing(get_connection(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT
                schedule_key,
                scheduled_date,
                scheduled_time,
                timezone,
                run_id,
                status,
                error_message,
                current_phase,
                current_source,
                total_sources,
                completed_sources,
                failed_sources,
                articles_scanned,
                qualified_topics,
                social_drafts_created,
                blog_drafts_created,
                research_calls,
                tracked_tokens,
                estimated_credits,
                heartbeat_at,
                worker_id,
                started_at,
                finished_at,
                (
                    SELECT rl.message
                    FROM run_logs rl
                    WHERE rl.run_id = automation_run_history.run_id
                    ORDER BY rl.id DESC
                    LIMIT 1
                ) AS latest_message,
                (
                    SELECT rl.created_at
                    FROM run_logs rl
                    WHERE rl.run_id = automation_run_history.run_id
                    ORDER BY rl.id DESC
                    LIMIT 1
                ) AS latest_event_at
            FROM automation_run_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def reconcile_automation_run_history(connection) -> None:
    connection.execute(
        """
        UPDATE automation_run_history
        SET status = 'timed_out',
            error_message = COALESCE(error_message, 'Run timed out before completion.'),
            finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP),
            heartbeat_at = COALESCE(heartbeat_at, CURRENT_TIMESTAMP),
            updated_at = CURRENT_TIMESTAMP
        WHERE status = 'running'
          AND started_at IS NOT NULL
          AND datetime(started_at) < datetime('now', '-6 hours')
        """
    )
    running_rows = connection.execute(
        """
        SELECT schedule_key, run_id
        FROM automation_run_history
        WHERE status = 'running' AND run_id IS NOT NULL
        """
    ).fetchall()
    for row in running_rows:
        event = connection.execute(
            """
            SELECT event_type, message, created_at
            FROM run_logs
            WHERE run_id = ? AND phase = 'daily_automation'
              AND event_type IN ('complete', 'error')
            ORDER BY id DESC
            LIMIT 1
            """,
            (row["run_id"],),
        ).fetchone()
        if not event:
            continue
        connection.execute(
            """
            UPDATE automation_run_history
            SET status = ?,
                error_message = CASE WHEN ? = 'error' THEN ? ELSE NULL END,
                finished_at = ?,
                current_phase = ?,
                heartbeat_at = COALESCE(heartbeat_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE schedule_key = ?
            """,
            (
                "failed" if event["event_type"] == "error" else "complete",
                event["event_type"],
                event["message"],
                event["created_at"],
                event["event_type"],
                row["schedule_key"],
            ),
        )
    connection.commit()


def _scheduled_datetime(now: datetime, schedule_time: str) -> datetime:
    hour_text, minute_text = schedule_time.split(":", 1)
    return now.replace(
        hour=int(hour_text),
        minute=int(minute_text),
        second=0,
        microsecond=0,
    )


def _load_timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Kolkata")


def _is_stale_running_claim(started_at: str | None) -> bool:
    if not started_at:
        return True
    try:
        started = datetime.fromisoformat(str(started_at))
    except ValueError:
        return True
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return datetime.now(UTC) - started > timedelta(hours=6)
