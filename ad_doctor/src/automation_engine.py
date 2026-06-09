from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import platform
import re
import subprocess

from src.config import BASE_DIR, DATABASE_PATH
from src.db import get_connection, initialize_database


TASK_NAME = "AdDoctorDailyScrape"


@dataclass(frozen=True)
class AutomationSettings:
    enabled: bool
    schedule_time: str
    timezone: str
    task_name: str
    notify_on_success: bool
    notify_on_failure: bool
    fetch_source_time_budget_seconds: int
    updated_at: str | None = None


@dataclass(frozen=True)
class SchedulerUpdateResult:
    supported: bool
    success: bool
    message: str


def get_automation_settings(database_path=DATABASE_PATH) -> AutomationSettings:
    initialize_database(database_path=database_path)
    with closing(get_connection(database_path)) as connection:
        row = connection.execute(
            """
            SELECT
                enabled,
                schedule_time,
                timezone,
                task_name,
                notify_on_success,
                notify_on_failure,
                fetch_source_time_budget_seconds,
                updated_at
            FROM automation_settings
            WHERE id = 1
            """
        ).fetchone()
    if row is None:
        return AutomationSettings(
            enabled=True,
            schedule_time="09:00",
            timezone="Asia/Kolkata",
            task_name=TASK_NAME,
            notify_on_success=True,
            notify_on_failure=True,
            fetch_source_time_budget_seconds=180,
        )
    return AutomationSettings(
        enabled=bool(row["enabled"]),
        schedule_time=str(row["schedule_time"]),
        timezone=str(row["timezone"]),
        task_name=str(row["task_name"]),
        notify_on_success=bool(row["notify_on_success"]),
        notify_on_failure=bool(row["notify_on_failure"]),
        fetch_source_time_budget_seconds=int(row["fetch_source_time_budget_seconds"]),
        updated_at=row["updated_at"],
    )


def save_automation_settings(
    *,
    enabled: bool,
    schedule_time: str,
    timezone: str = "Asia/Kolkata",
    task_name: str = TASK_NAME,
    notify_on_success: bool = True,
    notify_on_failure: bool = True,
    fetch_source_time_budget_seconds: int = 180,
    database_path=DATABASE_PATH,
) -> AutomationSettings:
    validate_schedule_time(schedule_time)
    initialize_database(database_path=database_path)
    with closing(get_connection(database_path)) as connection:
        connection.execute(
            """
            INSERT INTO automation_settings (
                id,
                enabled,
                schedule_time,
                timezone,
                task_name,
                notify_on_success,
                notify_on_failure,
                fetch_source_time_budget_seconds,
                updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                enabled = excluded.enabled,
                schedule_time = excluded.schedule_time,
                timezone = excluded.timezone,
                task_name = excluded.task_name,
                notify_on_success = excluded.notify_on_success,
                notify_on_failure = excluded.notify_on_failure,
                fetch_source_time_budget_seconds = excluded.fetch_source_time_budget_seconds,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                1 if enabled else 0,
                schedule_time,
                timezone,
                task_name,
                1 if notify_on_success else 0,
                1 if notify_on_failure else 0,
                int(fetch_source_time_budget_seconds),
            ),
        )
        connection.commit()
    return get_automation_settings(database_path=database_path)


def install_or_update_daily_task(
    settings: AutomationSettings,
    project_dir: Path = BASE_DIR,
) -> SchedulerUpdateResult:
    if platform.system().lower() != "windows":
        return SchedulerUpdateResult(
            supported=False,
            success=False,
            message="Local scheduled task install is only implemented for Windows.",
        )
    script_path = project_dir / "scripts" / "run_daily_automation.bat"
    if not script_path.exists():
        return SchedulerUpdateResult(
            supported=True,
            success=False,
            message=f"Missing scheduler script: {script_path}",
        )
    task_command = f'cmd.exe /c ""{script_path}""'
    create_command = [
        "schtasks.exe",
        "/Create",
        "/F",
        "/SC",
        "DAILY",
        "/ST",
        settings.schedule_time,
        "/TN",
        settings.task_name,
        "/TR",
        task_command,
    ]
    try:
        create_result = subprocess.run(
            create_command,
            capture_output=True,
            check=False,
            text=True,
        )
        if create_result.returncode != 0:
            return SchedulerUpdateResult(
                supported=True,
                success=False,
                message=(create_result.stderr or create_result.stdout or "Unable to create scheduled task.").strip(),
            )
        change_state = "ENABLE" if settings.enabled else "DISABLE"
        state_result = subprocess.run(
            ["schtasks.exe", "/Change", "/TN", settings.task_name, f"/{change_state}"],
            capture_output=True,
            check=False,
            text=True,
        )
        if state_result.returncode != 0:
            return SchedulerUpdateResult(
                supported=True,
                success=False,
                message=(state_result.stderr or state_result.stdout or "Unable to update scheduled task state.").strip(),
            )
        settings_result = apply_windows_task_settings(settings.task_name)
        if settings_result.returncode != 0:
            return SchedulerUpdateResult(
                supported=True,
                success=False,
                message=(
                    settings_result.stderr
                    or settings_result.stdout
                    or "Unable to update scheduled task settings."
                ).strip(),
            )
    except OSError as exc:
        return SchedulerUpdateResult(supported=True, success=False, message=str(exc))
    return SchedulerUpdateResult(
        supported=True,
        success=True,
        message=f"Scheduled task {settings.task_name} set to {settings.schedule_time}.",
    )


def validate_schedule_time(schedule_time: str) -> None:
    if not re.fullmatch(r"\d{2}:\d{2}", schedule_time):
        raise ValueError("Schedule time must use HH:MM format.")
    hour, minute = (int(part) for part in schedule_time.split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Schedule time must be a valid 24-hour time.")


def apply_windows_task_settings(task_name: str) -> subprocess.CompletedProcess[str]:
    escaped_task_name = task_name.replace("'", "''")
    command = (
        "$settings = New-ScheduledTaskSettingsSet "
        "-StartWhenAvailable "
        "-AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries "
        "-ExecutionTimeLimit (New-TimeSpan -Hours 8); "
        f"Set-ScheduledTask -TaskName '{escaped_task_name}' -Settings $settings | Out-Null"
    )
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        check=False,
        text=True,
    )
