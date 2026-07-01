from pathlib import Path
import unittest


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_DIR = PROJECT_DIR.parent


class RenderDeploymentTest(unittest.TestCase):
    def test_render_free_uses_ui_only_entrypoint(self) -> None:
        render_yaml = (REPOSITORY_DIR / "render.yaml").read_text(encoding="utf-8")

        self.assertIn("plan: free", render_yaml)
        self.assertIn("startCommand: streamlit run app.py", render_yaml)
        self.assertIn("AD_DOCTOR_SCHEDULER_MODE", render_yaml)
        self.assertIn("value: external", render_yaml)
        self.assertNotIn("mountPath:", render_yaml)

    def test_github_actions_runs_daily_automation(self) -> None:
        workflow = (REPOSITORY_DIR / ".github" / "workflows" / "daily-ad-doctor.yml").read_text(encoding="utf-8")

        self.assertIn("python -m src.daily_automation_runner", workflow)
        self.assertIn("AD_DOCTOR_SCHEDULER_MODE: external", workflow)
        self.assertIn("git add ad_doctor/database/ad_doctor.db", workflow)

    def test_paid_supervisor_still_starts_streamlit_and_scheduler_worker(self) -> None:
        supervisor = (PROJECT_DIR / "start_render.py").read_text(encoding="utf-8")

        self.assertIn('"streamlit"', supervisor)
        self.assertIn('"src.scheduler_worker"', supervisor)
        self.assertIn('web_environment["AD_DOCTOR_APP_SCHEDULER_ENABLED"] = "0"', supervisor)


if __name__ == "__main__":
    unittest.main()
