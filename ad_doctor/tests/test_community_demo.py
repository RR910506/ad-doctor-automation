"""The public entrypoint must render without services or writable data."""
from pathlib import Path
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from ui.app_shell import PAGES
from ui.services.demo_action_service import DemoActionService


class CommunityDemoTests(unittest.TestCase):
    def test_all_pages_render_without_live_services(self):
        entry = str(Path(__file__).resolve().parents[1] / 'community_app.py')
        with patch('ui.services.ui_query_service.ProductionUIDataSource', side_effect=AssertionError('Production data accessed')), patch('requests.sessions.Session.request', side_effect=AssertionError('Network accessed')):
            for page in (*PAGES, 'legacy'):
                with self.subTest(page=page):
                    app = AppTest.from_file(entry)
                    app.query_params['page'] = page
                    app.run(timeout=30)
                    self.assertFalse(app.exception, [str(x.value) for x in app.exception])
                    self.assertFalse(any('Legacy compatibility workspace' in str(x.value) for x in app.markdown))

    def test_mutations_are_blocked(self):
        actions = DemoActionService()
        for name in ('import_sources_from_file', 'import_sources_from_google_sheet', 'approve_content', 'reject_content', 'request_revision', 'run_automation_now', 'set_source_active', 'regenerate'):
            result = getattr(actions, name)(confirmed=True)
            self.assertFalse(result.success)
            self.assertEqual(result.error_category, 'demo_read_only')
