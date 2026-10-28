"""Exercise home-screen navigation through actual Streamlit reruns."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from src.db import initialize_database
from ui.services.ui_query_service import UIQueryService


class DashboardNavigationTests(unittest.TestCase):
    def test_workflow_shortcuts_open_their_pages(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / 'ui.db'
            initialize_database(database)
            service = UIQueryService(demo=False, database_path=database)
            with patch('ui.app_shell.UIQueryService', return_value=service):
                for destination in ('sources', 'qualification', 'content_studio', 'publishing'):
                    with self.subTest(destination=destination):
                        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
                        self.assertFalse(app.exception)
                        key = f'home_{destination}' if destination in ('sources', 'content_studio') else f'nav_{destination}'
                        app.button(key=key).click().run(timeout=30)
                        self.assertFalse(app.exception)
                        self.assertEqual(app.query_params['page'], destination)
