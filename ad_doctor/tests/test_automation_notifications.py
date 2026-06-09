import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.automation_engine import (
    AutomationSettings,
    get_automation_settings,
    install_or_update_daily_task,
    save_automation_settings,
    validate_schedule_time,
)
from src.db import get_connection, initialize_database
from src.notification_engine import (
    list_notification_events,
    notify_daily_scrape_complete,
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


if __name__ == "__main__":
    unittest.main()
