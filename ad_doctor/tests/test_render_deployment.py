from pathlib import Path
import unittest


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_DIR = PROJECT_DIR.parent


class RenderDeploymentTest(unittest.TestCase):
    def test_render_uses_worker_supervisor_and_persistent_disk(self) -> None:
        render_yaml = (REPOSITORY_DIR / "render.yaml").read_text(encoding="utf-8")

        self.assertIn("plan: starter", render_yaml)
        self.assertIn("startCommand: python start_render.py", render_yaml)
        self.assertIn("mountPath: /opt/render/project/src/ad_doctor_data", render_yaml)
        self.assertIn("AD_DOCTOR_DATABASE_PATH", render_yaml)
        self.assertIn("AD_DOCTOR_SCHEDULER_MODE", render_yaml)
        self.assertIn("value: worker", render_yaml)
        self.assertIn("AD_DOCTOR_CLEAR_SQLITE_GENERATED_DATA_FOR_GOOGLE", render_yaml)
        self.assertIn('value: "0"', render_yaml)

    def test_github_actions_is_manual_fallback_only(self) -> None:
        workflow = (REPOSITORY_DIR / ".github" / "workflows" / "daily-ad-doctor.yml").read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("python -m src.daily_automation_runner", workflow)
        self.assertIn("AD_DOCTOR_SCHEDULER_MODE: external", workflow)
        self.assertIn("AD_DOCTOR_DRAFT_STORE: google", workflow)
        self.assertIn("GOOGLE_DRAFTS_SPREADSHEET_ID", workflow)
        self.assertNotIn("git add ad_doctor/database/ad_doctor.db", workflow)

    def test_paid_supervisor_still_starts_streamlit_and_scheduler_worker(self) -> None:
        supervisor = (PROJECT_DIR / "start_render.py").read_text(encoding="utf-8")

        self.assertIn('"streamlit"', supervisor)
        self.assertIn('"src.scheduler_worker"', supervisor)
        self.assertIn('web_environment["AD_DOCTOR_APP_SCHEDULER_ENABLED"] = "0"', supervisor)


if __name__ == "__main__":
    unittest.main()
