from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import os
import threading
import time
import traceback
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.automation_engine import get_automation_settings
from src.config import DATABASE_PATH
from src.db import get_connection, initialize_database, log_run_event
from src.daily_automation_runner import FailedPipelineSummary, build_output
from src.notification_engine import notify_daily_scrape_complete
from src.pipeline_runner import run_real_pipeline


DEFAULT_POLL_SECONDS = 60
DEFAULT_CATCHUP_HOURS = 12


@dataclass(frozen=True)
class AppSchedulerStatus:
    enabled_by_env: bool
    running: bool
    message: str


_scheduler_lock = threading.Lock()
_scheduler_thread: threading.Thread | None = None
_scheduler_stop_event = threading.Event()
_last_status = AppSchedulerStatus(
    enabled_by_env=False,
    running=False,
    message="App scheduler has not started.",
)


def is_app_scheduler_enabled() -> bool:
    value = os.getenv("AD_DOCTOR_APP_SCHEDULER_ENABLED", "0").strip().lower()
    return value not in {"0", "false", "no", "off"}


def start_app_scheduler() -> AppSchedulerStatus:
    global _scheduler_thread, _last_status
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
    initialize_database(database_path)
    settings = get_automation_settings(database_path=database_path)
    if not settings.enabled:
        return []

    timezone = _load_timezone(settings.timezone)
    now = datetime.now(timezone)
    catchup_hours = int(
        os.getenv("AD_DOCTOR_SCHEDULER_CATCHUP_HOURS", str(DEFAULT_CATCHUP_HOURS))
        or DEFAULT_CATCHUP_HOURS
    )
    catchup_after = now - timedelta(hours=max(1, catchup_hours))
    claimed_run_keys = []

    for schedule_time in settings.schedule_times or (settings.schedule_time,):
        scheduled_at = _scheduled_datetime(now, schedule_time)
        if scheduled_at > now or scheduled_at < catchup_after:
            continue
        claim = claim_scheduled_run(
            scheduled_at=scheduled_at,
            schedule_time=schedule_time,
            timezone_name=settings.timezone,
            database_path=database_path,
        )
        if not claim:
            continue
        claimed_run_keys.append(claim)
        run_claimed_schedule(
            schedule_key=claim,
            schedule_time=schedule_time,
            settings=settings,
            database_path=database_path,
        )
    return claimed_run_keys


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
        connection.commit()
    return schedule_key


def run_claimed_schedule(
    *,
    schedule_key: str,
    schedule_time: str,
    settings,
    database_path=DATABASE_PATH,
) -> None:
    try:
        summary = run_real_pipeline(
            database_path=database_path,
            fetch_source_time_budget_seconds=settings.fetch_source_time_budget_seconds,
        )
        notification_summary = (
            notify_daily_scrape_complete(summary, database_path=database_path)
            if settings.notify_on_success
            else None
        )
        with closing(get_connection(database_path)) as connection:
            connection.execute(
                """
                UPDATE automation_run_history
                SET run_id = ?,
                    status = 'complete',
                    finished_at = CURRENT_TIMESTAMP,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE schedule_key = ?
                """,
                (summary.run_id, schedule_key),
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
                SET status = 'error',
                    error_message = ?,
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


def list_automation_run_history(limit: int = 25, database_path=DATABASE_PATH) -> list[dict[str, object]]:
    initialize_database(database_path)
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
                started_at,
                finished_at
            FROM automation_run_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


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
