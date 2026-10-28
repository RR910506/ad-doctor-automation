import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from src.services.ai_providers import ProviderRegistry
from src.services.automation_store import SQLiteStore
from src.services.editorial_pipeline import process_topic
from test_editorial_pipeline import candidate, research, draft, registry, ROUTES, PASS


class TeamAppTests(TestCase):
    def test_saved_case_article_carousel_review_and_publication_gate(self):
        with TemporaryDirectory() as directory:
            path = str(Path(directory) / 'state.db')
            store = SQLiteStore(path)
            case = process_topic(candidate(), registry([research(), draft(), PASS]), ROUTES)
            case.update(case_id='test', created_at='2026-10-28T09:00:00+00:00')
            store.put('case:test', 'case', case)
            entry = str(Path(__file__).resolve().parents[1] / 'ad_doctor_app.py')
            with patch.dict(os.environ, {'AD_DOCTOR_ACCESS_PASSWORD': 'private-team-password', 'AD_DOCTOR_STATE_DATABASE': path}):
                app = AppTest.from_file(entry)
                app.session_state['_access_proof'] = 'private-team-password'
                app.run()
                next(button for button in app.button if button.label == 'My content').click().run()
                self.assertFalse(app.exception)
                self.assertEqual([tab.label for tab in app.tabs], ['Article', 'Carousel', 'Expert details'])
                self.assertIn('SLIDE 8', ' '.join(caption.value for caption in app.caption))
                next(item for item in app.text_input if item.label == 'Reviewer identity').set_value('Rajesh')
                next(item for item in app.checkbox if item.label.startswith('I reviewed')).check()
                next(button for button in app.button if button.label == 'Approve content').click().run()
                self.assertFalse(app.exception)
                self.assertEqual(store.get('case:test')['status'], 'approved')
                next(button for button in app.button if button.label == 'Publishing').click().run()
                self.assertFalse(app.exception)
                self.assertTrue(next(button for button in app.button if button.label == 'Create private Google Doc').disabled)

    def test_provider_choices_save_separately_without_api_calls(self):
        with TemporaryDirectory() as directory:
            path = str(Path(directory) / 'state.db')
            entry = str(Path(__file__).resolve().parents[1] / 'gemini_app.py')
            provider = ProviderRegistry(lambda _: '')
            with patch.dict(os.environ, {'AD_DOCTOR_ACCESS_PASSWORD': 'private-team-password', 'AD_DOCTOR_STATE_DATABASE': path}), patch('ui.team_app.ProviderRegistry', return_value=provider), patch.object(provider, 'generate_json') as generation:
                app = AppTest.from_file(entry)
                app.session_state['_access_proof'] = 'private-team-password'
                app.session_state['team_page'] = 'AI settings'
                app.run()
                app.selectbox[0].set_value('perplexity').run()
                app.text_input[0].set_value('sonar')
                app.selectbox[1].set_value('openai').run()
                app.text_input[1].set_value('writer-model')
                app.selectbox[2].set_value('anthropic').run()
                app.text_input[2].set_value('review-model')
                next(button for button in app.button if button.label == 'Save AI choices').click().run()
                self.assertFalse(app.exception)
                saved = SQLiteStore(path).get('settings')['routes']
                self.assertEqual(saved['research'], {'provider': 'perplexity', 'model': 'sonar'})
                self.assertEqual(saved['writing']['provider'], 'openai')
                self.assertEqual(saved['quality']['provider'], 'anthropic')
                generation.assert_not_called()
