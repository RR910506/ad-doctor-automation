import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from streamlit.testing.v1 import AppTest
from src.services.gemini_workspace import GeminiWorkspace
from src.adapters.gemini_research_provider import GeminiProviderError


class GeminiWorkspaceTests(unittest.TestCase):
    def test_model_list_filters_non_generation_and_media(self):
        client = Mock()
        client.models.list.return_value = [
            SimpleNamespace(name='models/gemini-account-model', supported_actions=['generateContent']),
            SimpleNamespace(name='models/gemini-image', supported_actions=['generateContent']),
            SimpleNamespace(name='models/embed', supported_actions=['embedContent']),
        ]
        self.assertEqual(GeminiWorkspace(client=client).available_models(), ['gemini-account-model'])

    def test_model_list_prefers_current_flash_model(self):
        client = Mock()
        client.models.list.return_value = [
            SimpleNamespace(name='models/gemini-2.5-flash', supported_actions=['generateContent']),
            SimpleNamespace(name='models/gemma-4-31b-it', supported_actions=['generateContent']),
            SimpleNamespace(name='models/gemini-3.8-flash', supported_actions=['generateContent']),
        ]
        self.assertEqual(GeminiWorkspace(client=client).available_models(), ['gemini-3.8-flash', 'gemma-4-31b-it'])

    def test_stages_and_no_automatic_fallback(self):
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(text='A sourced memo', usage_metadata=None)
        service = GeminiWorkspace(client=client)
        result = service.run(stage='Research', model='account-model', topic='Topic', material='Real excerpts')
        self.assertEqual(result['status'], 'unverified_research')
        self.assertIn('Do not write final', client.models.generate_content.call_args.kwargs['config']['system_instruction'])
        with self.assertRaises(ValueError):
            service.run(stage='Writing', model='account-model', topic='Topic', material='Memo')
        result = service.run(stage='Writing', model='account-model', topic='Topic', material='Reviewed memo', reviewed=True)
        self.assertEqual(result['status'], 'pending_review')
        client.models.generate_content.reset_mock()
        client.models.generate_content.side_effect = RuntimeError('SECRET_RESPONSE_MARKER')
        with self.assertRaises(GeminiProviderError) as error:
            service.run(stage='Research', model='account-model', topic='Topic', material='Excerpts')
        self.assertNotIn('SECRET_RESPONSE_MARKER', str(error.exception))
        self.assertEqual(client.models.generate_content.call_count, 1)

    def test_empty_output_and_input_limits(self):
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(text='', usage_metadata=None)
        service = GeminiWorkspace(client=client)
        with self.assertRaises(ValueError):
            service.run(stage='Research', model='m', topic='t', material='x' * 40001)
        client.models.generate_content.assert_not_called()
        with self.assertRaises(ValueError):
            service.run(stage='Research', model='m', topic='t', material='excerpts')

    def test_gate_prevents_api_client_before_login(self):
        entry = str(Path(__file__).resolve().parents[1] / 'gemini_app.py')
        with patch.dict(os.environ, {'AD_DOCTOR_ACCESS_PASSWORD': 'test-workspace-password', 'GEMINI_API_KEY': 'fake-test-key'}), patch('src.services.gemini_workspace.GeminiWorkspace') as factory:
            app = AppTest.from_file(entry).run()
            self.assertFalse(app.exception)
            factory.assert_not_called()
            app.text_input[0].set_value('wrong')
            app.button[0].click().run()
            factory.assert_not_called()
            self.assertTrue(app.error)

    def test_authenticated_real_input_flow_and_duplicate_click(self):
        entry = str(Path(__file__).resolve().parents[1] / 'gemini_app.py')
        client = Mock()
        client.models.list.return_value = [SimpleNamespace(name='models/account-model', supported_actions=['generateContent'])]
        client.models.generate_content.return_value = SimpleNamespace(text='Source-linked memo', usage_metadata=None)
        service = GeminiWorkspace(client=client)
        with patch.dict(os.environ, {'AD_DOCTOR_ACCESS_PASSWORD': 'test-workspace-password', 'GEMINI_API_KEY': 'fake-test-key'}), patch('src.services.gemini_workspace.GeminiWorkspace', return_value=service):
            app = AppTest.from_file(entry)
            app.session_state['_access_proof'] = 'test-workspace-password'
            app.run()
            next(b for b in app.button if b.label.startswith('Check connection')).click().run()
            app.text_input[0].set_value('Brand campaign')
            app.text_area[0].set_value('Three supplied publication excerpts and URLs')
            app.checkbox[0].check().run()
            next(b for b in app.button if b.label == 'Research with Gemini').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(client.models.generate_content.call_count, 1)
            next(b for b in app.button if b.label == 'Research with Gemini').click().run()
            self.assertEqual(client.models.generate_content.call_count, 1)
            app.checkbox[1].check().run()
            next(b for b in app.button if b.label == 'Create draft with Gemini').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(client.models.generate_content.call_count, 2)
            self.assertEqual(app.session_state['gemini_draft']['status'], 'pending_review')
