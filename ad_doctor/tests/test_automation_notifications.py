import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest import mock


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.automation_engine import (
    AutomationSettings,
    get_automation_settings,
    install_or_update_daily_task,
    parse_schedule_times,
    save_automation_settings,
    validate_schedule_time,
)
from src.app_scheduler import (
    claim_latest_due_run,
    claim_next_queued_run,
    claim_scheduled_run,
    get_cloud_worker_status,
    is_app_scheduler_enabled,
    list_automation_run_history,
    queue_due_runs,
    record_worker_heartbeat,
    requeue_interrupted_worker_runs,
)
from src.db import get_connection, initialize_database
from src.notification_engine import (
    load_smtp_settings,
    list_notification_events,
    notify_daily_scrape_complete,
    save_smtp_settings,
    send_test_notification_email,
    upsert_app_user,
)


class AutomationSettingsTest(unittest.TestCase):
    def test_default_settings_are_created(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"

            settings = get_automation_settings(database_path=database_path)

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.schedule_time, "09:00")
        self.assertEqual(settings.task_name, "AdDoctorDailyScrape")
        self.assertTrue(settings.notify_on_success)

    def test_settings_can_be_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"

            saved = save_automation_settings(
                enabled=False,
                schedule_time="08:30",
                notify_on_success=False,
                notify_on_failure=True,
                fetch_source_time_budget_seconds=240,
                database_path=database_path,
            )
            reloaded = get_automation_settings(database_path=database_path)

        self.assertFalse(saved.enabled)
        self.assertEqual(reloaded.schedule_time, "08:30")
        self.assertFalse(reloaded.notify_on_success)
        self.assertEqual(reloaded.fetch_source_time_budget_seconds, 240)

    def test_multiple_schedule_times_can_be_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"

            saved = save_automation_settings(
                enabled=True,
                schedule_time="09:00",
                schedule_times=("09:00", "13:30", "18:00"),
                database_path=database_path,
            )
            reloaded = get_automation_settings(database_path=database_path)

        self.assertEqual(saved.schedule_time, "09:00")
        self.assertEqual(reloaded.schedule_times, ("09:00", "13:30", "18:00"))

    def test_parse_schedule_times_dedupes_and_validates(self) -> None:
        self.assertEqual(parse_schedule_times("09:00, 13:30, 09:00"), ("09:00", "13:30"))
        with self.assertRaises(ValueError):
            parse_schedule_times("09:00, 28:00")

    def test_invalid_schedule_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_schedule_time("25:00")

    def test_windows_task_command_quotes_script_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Ad Doctor ") as temporary_directory:
            project_dir = Path(temporary_directory)
            script_dir = project_dir / "scripts"
            script_dir.mkdir()
            script_path = script_dir / "run_daily_automation.bat"
            script_path.write_text("@echo off\n", encoding="utf-8")
            settings = AutomationSettings(
                enabled=True,
                schedule_time="09:00",
                timezone="Asia/Kolkata",
                task_name="AdDoctorDailyScrape",
                notify_on_success=True,
                notify_on_failure=True,
                fetch_source_time_budget_seconds=180,
            )
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")

            with mock.patch("src.automation_engine.platform.system", return_value="Windows"):
                with mock.patch(
                    "src.automation_engine.subprocess.run",
                    side_effect=[completed, completed, completed],
                ) as run_mock:
                    result = install_or_update_daily_task(settings, project_dir=project_dir)

        self.assertTrue(result.success)
        create_args = run_mock.call_args_list[0].args[0]
        task_command = create_args[create_args.index("/TR") + 1]
        self.assertEqual(task_command, f'cmd.exe /c ""{script_path}""')
        settings_args = run_mock.call_args_list[2].args[0]
        self.assertIn("-StartWhenAvailable", settings_args[-1])
        self.assertIn("-WakeToRun", settings_args[-1])
        self.assertIn("-AllowStartIfOnBatteries", settings_args[-1])


class NotificationEngineTest(unittest.TestCase):
    def test_missing_smtp_records_skipped_events_for_recipients(self) -> None:
        class Summary:
            run_id = "run_123"
            articles_seen = 10
            articles_inserted = 8
            articles_updated = 2
            fetch_failures = 0
            topics_linked = 5
            qualified_topics = 3
            locked_topics = 3
            research_dossiers = 3
            dissections = 3
            blog_drafts = 3
            social_drafts = 3

        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )
            upsert_app_user("user@example.com", "User", database_path=database_path)
            env_without_smtp = {
                "AD_DOCTOR_SMTP_HOST": "",
                "AD_DOCTOR_SMTP_FROM": "",
                "AD_DOCTOR_SMTP_USERNAME": "",
                "AD_DOCTOR_SMTP_PASSWORD": "",
            }

            with mock.patch.dict(os.environ, env_without_smtp, clear=False):
                notification_summary = notify_daily_scrape_complete(Summary(), database_path=database_path)
            events = list_notification_events(database_path=database_path)

            with closing(get_connection(database_path)) as connection:
                recipient_count = connection.execute("SELECT COUNT(*) AS total FROM app_users").fetchone()["total"]

        self.assertEqual(recipient_count, 1)
        self.assertEqual(notification_summary.recipients, 1)
        self.assertEqual(notification_summary.sent, 0)
        self.assertEqual(events[0]["status"], "skipped")
        self.assertIn("SMTP is not configured", events[0]["error_message"])

    def test_test_email_records_skipped_event_when_smtp_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )
            env_without_smtp = {
                "AD_DOCTOR_SMTP_HOST": "",
                "AD_DOCTOR_SMTP_FROM": "",
                "AD_DOCTOR_SMTP_USERNAME": "",
                "AD_DOCTOR_SMTP_PASSWORD": "",
            }

            with mock.patch.dict(os.environ, env_without_smtp, clear=False):
                notification_summary = send_test_notification_email(
                    "user@example.com",
                    database_path=database_path,
                )
            events = list_notification_events(database_path=database_path)

        self.assertEqual(notification_summary.recipients, 1)
        self.assertEqual(notification_summary.sent, 0)
        self.assertEqual(events[0]["run_id"], "email_test")
        self.assertEqual(events[0]["status"], "skipped")


class AppSchedulerTest(unittest.TestCase):
    def test_app_scheduler_is_enabled_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(is_app_scheduler_enabled())

    def test_schedule_claim_only_happens_once_per_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )
            scheduled_at = datetime(2026, 6, 15, 9, 0)

            first_claim = claim_scheduled_run(
                scheduled_at=scheduled_at,
                schedule_time="09:00",
                timezone_name="Asia/Kolkata",
                database_path=database_path,
            )
            second_claim = claim_scheduled_run(
                scheduled_at=scheduled_at,
                schedule_time="09:00",
                timezone_name="Asia/Kolkata",
                database_path=database_path,
            )
            history = list_automation_run_history(database_path=database_path)

        self.assertEqual(first_claim, "2026-06-15T09:00@Asia/Kolkata")
        self.assertIsNone(second_claim)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "running")

    def test_external_runner_claims_only_latest_due_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            settings = save_automation_settings(
                enabled=True,
                schedule_time="09:00",
                schedule_times=("09:00", "13:30", "18:00"),
                database_path=database_path,
            )

            claimed = claim_latest_due_run(
                settings,
                now=datetime(2026, 6, 15, 14, 0),
                database_path=database_path,
            )
            duplicate = claim_latest_due_run(
                settings,
                now=datetime(2026, 6, 15, 14, 1),
                database_path=database_path,
            )

        self.assertEqual(claimed, ("2026-06-15T13:30@Asia/Kolkata", "13:30"))
        self.assertIsNone(duplicate)

    def test_cloud_worker_queues_every_due_timestamp_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            save_automation_settings(
                enabled=True,
                schedule_time="09:00",
                schedule_times=("09:00", "10:00", "11:00"),
                database_path=database_path,
            )

            queued = queue_due_runs(
                now=datetime(2026, 6, 15, 11, 30),
                database_path=database_path,
            )
            duplicate_queue = queue_due_runs(
                now=datetime(2026, 6, 15, 11, 31),
                database_path=database_path,
            )
            first_claim = claim_next_queued_run(database_path)
            blocked_while_running = claim_next_queued_run(database_path)
            with closing(get_connection(database_path)) as connection:
                connection.execute(
                    "UPDATE automation_run_history SET status='complete' WHERE schedule_key=?",
                    (first_claim[0],),
                )
                connection.commit()
            second_claim = claim_next_queued_run(database_path)

        self.assertEqual(len(queued), 3)
        self.assertEqual(duplicate_queue, [])
        self.assertEqual(first_claim[1], "09:00")
        self.assertIsNone(blocked_while_running)
        self.assertEqual(second_claim[1], "10:00")

    def test_worker_restart_returns_running_timestamp_to_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            save_automation_settings(
                enabled=True,
                schedule_time="09:00",
                database_path=database_path,
            )
            queue_due_runs(
                now=datetime(2026, 6, 15, 9, 5),
                database_path=database_path,
            )
            claim_next_queued_run(database_path)

            recovered = requeue_interrupted_worker_runs(database_path)
            history = list_automation_run_history(database_path=database_path)

        self.assertEqual(recovered, 1)
        self.assertEqual(history[0]["status"], "queued")

    def test_cloud_worker_catches_yesterdays_slot_before_todays_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            save_automation_settings(
                enabled=True,
                schedule_time="09:00",
                database_path=database_path,
            )

            queued = queue_due_runs(
                now=datetime(2026, 6, 16, 8, 0),
                database_path=database_path,
            )

        self.assertEqual(queued, ["2026-06-15T09:00@Asia/Kolkata"])

    def test_cloud_worker_heartbeat_is_visible_to_the_web_app(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )
            record_worker_heartbeat(
                "idle",
                "Waiting for the next timestamp.",
                worker_id="test-worker",
                database_path=database_path,
            )

            status = get_cloud_worker_status(database_path)

        self.assertTrue(status.running)
        self.assertIn("Waiting", status.message)

    def test_stale_running_history_is_marked_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )
            claim_scheduled_run(
                scheduled_at=datetime(2026, 6, 15, 9, 0),
                schedule_time="09:00",
                timezone_name="Asia/Kolkata",
                database_path=database_path,
            )
            with closing(get_connection(database_path)) as connection:
                connection.execute(
                    "UPDATE automation_run_history SET started_at = '2020-01-01 00:00:00'"
                )
                connection.commit()

            history = list_automation_run_history(database_path=database_path)
            with closing(get_connection(database_path)) as connection:
                stored_status = connection.execute(
                    "SELECT status FROM automation_run_history LIMIT 1"
                ).fetchone()["status"]

        self.assertEqual(history[0]["status"], "error")
        self.assertIn("interrupted", history[0]["error_message"].lower())
        self.assertEqual(stored_status, "running")


class SmtpSettingsTest(unittest.TestCase):
    def test_saved_smtp_settings_are_loaded_from_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )
            saved = save_smtp_settings(
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_username="sender@example.com",
                smtp_password="secret",
                smtp_from="sender@example.com",
                use_tls=True,
                database_path=database_path,
            )
            loaded = load_smtp_settings(database_path=database_path)

        self.assertTrue(saved.is_ready)
        self.assertEqual(loaded.host, "smtp.example.com")
        self.assertEqual(loaded.username, "sender@example.com")
        self.assertEqual(loaded.password, "secret")
        self.assertEqual(loaded.from_email, "sender@example.com")
        self.assertEqual(loaded.source, "saved")


if __name__ == "__main__":
    unittest.main()
