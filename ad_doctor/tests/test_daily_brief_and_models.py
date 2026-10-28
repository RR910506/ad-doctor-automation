from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest
from streamlit.testing.v1 import AppTest
from src.model_lab import make_brief, comparison_id, TOOLS
from ui.services.daily_brief_service import daily_brief
from ui.services.ui_query_service import UIQueryService


class DailyBriefTests(unittest.TestCase):
    def test_india_midnight_and_today_are_not_latest_run(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'test.db'
            with sqlite3.connect(path) as c:
                c.execute('CREATE TABLE articles(id INTEGER PRIMARY KEY,headline TEXT,source_name TEXT,meta_description TEXT,article_url TEXT,fetch_timestamp TEXT)')
                for index, stamp in enumerate(('2026-10-24 18:29:59', '2026-10-24 18:30:00', '2026-10-25 18:29:59', '2026-10-25 18:30:00', 'invalid')):
                    c.execute('INSERT INTO articles VALUES(?,?,?,?,?,?)', (index, 'Story', 'Publication', '', 'https://example.com/story', stamp))
            service = UIQueryService(demo=False, database_path=path)
            result = daily_brief(service, datetime(2026, 10, 25, 12, tzinfo=timezone.utc))
            self.assertEqual(result['count'], 2)
            self.assertEqual(len(result['articles']), 2)
            self.assertEqual(daily_brief(service, datetime(2026, 10, 30, tzinfo=timezone.utc))['count'], 0)

    def test_demo_is_labelled_sample(self):
        result = daily_brief(UIQueryService(demo=True))
        self.assertTrue(result['sample'])
        self.assertEqual(result['count'], len(result['articles']))


class ModelLabTests(unittest.TestCase):
    def test_role_boundaries_and_comparison_identity(self):
        research = make_brief('Research', 'Brand move', 'Evidence A')
        writing = make_brief('Writing', 'Brand move', 'Evidence A')
        self.assertIn('Do not write final content', research)
        self.assertIn('Use only the supplied reviewed evidence', writing)
        self.assertNotEqual(comparison_id('Research', 'Brand move', 'Evidence A'), comparison_id('Research', 'Brand move', 'Evidence B'))
        self.assertNotEqual(comparison_id('Research', 'Brand move', 'Evidence A'), comparison_id('Writing', 'Brand move', 'Evidence A'))
        with self.assertRaises(ValueError):
            make_brief('Writing', 'Brand move', '')

    def test_manual_result_is_session_only_and_unverified(self):
        entry = Path(__file__).resolve().parents[1] / 'community_app.py'
        app = AppTest.from_file(str(entry))
        app.query_params['page'] = 'model_lab'
        app.run()
        app.text_input[0].set_value('Brand move')
        app.text_area[0].set_value('https://example.com/story')
        app.run()
        app.text_input[1].set_value('unknown')
        app.text_area[1].set_value('An unverified answer')
        next(x for x in app.button if x.label == 'Add to this comparison').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['model_trials'][0]['status'], 'unverified_manual_result')
