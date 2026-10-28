import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

import requests

from src.services.ai_providers import ProviderRegistry, ProviderError


SCHEMA = {'type': 'object', 'required': ['ok'], 'properties': {'ok': {'type': 'boolean'}}, 'additionalProperties': False}


def response(data, code=200):
    result = Mock()
    result.status_code = code
    result.json.return_value = data
    return result


class AIProviderTests(TestCase):
    def registry(self, session=None, client=None, values=None):
        secrets = {'OPENAI_API_KEY': 'test-openai-secret', 'GEMINI_API_KEY': 'test-gemini-secret',
                   'PERPLEXITY_API_KEY': 'test-perplexity-secret', 'ANTHROPIC_API_KEY': 'test-anthropic-secret',
                   'OPENAI_COMPATIBLE_API_KEY': 'test-other-secret', 'OPENAI_COMPATIBLE_BASE_URL': 'https://private.example/v1'}
        secrets.update(values or {})
        return ProviderRegistry(lambda name: secrets.get(name, ''), session=session, gemini_client=client)

    def test_catalog_has_separate_supported_stages_and_missing_key_is_not_configured(self):
        registry = self.registry(values={'OPENAI_API_KEY': ''})
        self.assertEqual(len(registry.catalog()), 5)
        self.assertFalse(registry.configured('openai'))
        self.assertTrue(registry.configured('gemini'))
        registry.catalog()[0]['stages'].clear()
        self.assertEqual(len(registry.catalog()[0]['stages']), 3)
        with self.assertRaises(ProviderError):
            registry.generate_json('openai', 'account-model', 'task', {}, SCHEMA)

    def test_openai_perplexity_and_compatible_use_selected_model_without_fallback(self):
        for provider in ['openai', 'perplexity', 'openai_compatible']:
            with self.subTest(provider=provider):
                session = Mock()
                session.request.return_value = response({'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}],
                                                         'usage': {'prompt_tokens': 4, 'completion_tokens': 3, 'SECRET_FIELD': 'private'}})
                registry = self.registry(session=session)
                result = registry.generate_json(provider, 'selected-model', 'Only supplied evidence', {'text': 'source excerpt'}, SCHEMA)
                self.assertEqual(result['provider'], provider)
                self.assertEqual(result['data'], {'ok': True})
                self.assertEqual(result['model'], 'selected-model')
                self.assertNotIn('SECRET_FIELD', result['usage'])
                self.assertEqual(session.request.call_count, 1)
                request = session.request.call_args.kwargs
                self.assertFalse(request['allow_redirects'])
                self.assertEqual(request['json']['model'], 'selected-model')
                self.assertNotIn('test-', json.dumps(request['json']))

    def test_anthropic_uses_messages_and_server_key_header(self):
        session = Mock()
        session.request.return_value = response({'content': [{'type': 'text', 'text': '{"ok":true}'}],
                                                 'usage': {'input_tokens': 5, 'output_tokens': 2}})
        result = self.registry(session=session).generate_json('anthropic', 'account-claude', 'task', {}, SCHEMA)
        self.assertEqual(result['data'], {'ok': True})
        self.assertEqual(session.request.call_args.args[1], 'https://api.anthropic.com/v1/messages')
        self.assertIn('x-api-key', session.request.call_args.kwargs['headers'])
        self.assertNotIn('Authorization', session.request.call_args.kwargs['headers'])

    def test_gemini_structured_adapter_preserves_metadata(self):
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(text='{"ok":true}', usage_metadata=SimpleNamespace(prompt_token_count=4, candidates_token_count=2, total_token_count=6))
        result = self.registry(client=client).generate_json('gemini', 'gemini-selected', 'task', {}, SCHEMA)
        self.assertEqual(result['usage'], {'input_tokens': 4, 'output_tokens': 2, 'total_tokens': 6})
        self.assertEqual(client.models.generate_content.call_args.kwargs['config']['response_json_schema'], SCHEMA)

    def test_http_and_connection_errors_never_expose_key_or_provider_body(self):
        for code in [400, 401, 403, 404, 429, 503]:
            with self.subTest(code=code):
                session = Mock()
                session.request.return_value = response({'error': 'PRIVATE_KEY_PRIVATE_RESPONSE'}, code)
                with self.assertRaises(ProviderError) as raised:
                    self.registry(session=session).generate_json('openai', 'model', 'task', {}, SCHEMA)
                self.assertNotIn('PRIVATE_KEY', str(raised.exception))
                self.assertNotIn('test-openai-secret', str(raised.exception))
                self.assertEqual(session.request.call_count, 1)
        session.request.side_effect = requests.ConnectionError('PRIVATE_KEY_PRIVATE_RESPONSE')
        with self.assertRaises(ProviderError) as raised:
            self.registry(session=session).generate_json('openai', 'model', 'task', {}, SCHEMA)
        self.assertNotIn('PRIVATE_KEY', str(raised.exception))

    def test_invalid_json_types_refusal_and_truncation_fail_closed(self):
        for value in ['[]', '{"ok":"yes"}', '{"ok":true,"extra":"invented"}', 'not json', '{"ok":NaN}']:
            with self.subTest(value=value):
                session = Mock()
                session.request.return_value = response({'choices': [{'message': {'content': value}}]})
                with self.assertRaises(ProviderError):
                    self.registry(session=session).generate_json('openai', 'model', 'task', {}, SCHEMA)
        session.request.return_value = response({'choices': [{'finish_reason': 'length', 'message': {'content': '{"ok":true}'}}]})
        with self.assertRaises(ProviderError):
            self.registry(session=session).generate_json('openai', 'model', 'task', {}, SCHEMA)

    def test_catalogue_is_explicit_and_perplexity_does_not_invent_access(self):
        session = Mock()
        session.request.return_value = response({'data': [{'id': 'account-text-model'}, {'id': 'text-embedding-model'}]})
        registry = self.registry(session=session)
        self.assertEqual(registry.available_models('openai'), ['account-text-model'])
        before = session.request.call_count
        with self.assertRaises(ProviderError):
            registry.available_models('perplexity')
        self.assertEqual(before, session.request.call_count)

    def test_custom_endpoint_requires_https_and_does_not_follow_redirects(self):
        session = Mock()
        registry = self.registry(session=session, values={'OPENAI_COMPATIBLE_BASE_URL': 'http://insecure.example/v1'})
        with self.assertRaises(ProviderError):
            registry.generate_json('openai_compatible', 'model', 'task', {}, SCHEMA)
        session.request.assert_not_called()

    def test_input_bounds_prevent_requests(self):
        session = Mock()
        registry = self.registry(session=session)
        with self.assertRaises(ProviderError):
            registry.generate_json('openai', 'model', 'task', {'text': 'x' * 100001}, SCHEMA)
        session.request.assert_not_called()
