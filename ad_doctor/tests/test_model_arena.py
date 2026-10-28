from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from src.services.ai_providers import ProviderError
from src.services.automation_store import SQLiteStore
from src.services.model_arena import ModelArena


class ModelArenaTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = SQLiteStore(Path(self.directory.name) / 'experiments.db')
        self.registry = Mock()
        self.registry.generate_json.return_value = {'data': {'answer': 'A source-bound test answer.'}, 'usage': {'input_tokens': 100}}
        self.arena = ModelArena(self.store, self.registry)

    def test_same_brief_comparison_and_subjective_rating_stays_isolated(self):
        material = {'evidence': {'reports': ['An actual collected statement.']}}
        self.store.put('case:live', 'case', {'status': 'pending_review'})
        first = self.arena.run('writing', material, {'provider': 'gemini', 'model': 'test-model'})
        second = self.arena.run('writing', material, {'provider': 'openai', 'model': 'other-model'})
        self.assertEqual(first['brief_hash'], second['brief_hash'])
        self.assertNotEqual(first['experiment_id'], second['experiment_id'])
        self.assertEqual(first['status'], 'unverified')
        self.assertIn('latency_seconds', first)
        self.assertEqual(first['usage']['input_tokens'], 100)
        rated = self.arena.rate(first['experiment_id'], 4, 'Clearer structure.')
        self.assertEqual(rated['rating'], 4)
        self.assertEqual(self.store.get('case:live'), {'status': 'pending_review'})
        self.assertEqual(self.store.list('experiment')[0]['verification'], 'unverified_experiment_only')

    def test_stage_and_material_changes_are_not_comparable(self):
        route = {'provider': 'gemini', 'model': 'test'}
        one = self.arena.run('research', {'source': 'a'}, route)
        two = self.arena.run('writing', {'source': 'a'}, route)
        three = self.arena.run('research', {'source': 'b'}, route)
        self.assertEqual(len({one['brief_hash'], two['brief_hash'], three['brief_hash']}), 3)

    def test_failed_provider_call_persists_safe_record_without_output(self):
        self.registry.generate_json.side_effect = ProviderError('gemini', 'provider_unavailable', 503)
        result = self.arena.run('quality', {'draft': 'text'}, {'provider': 'gemini', 'model': 'test'})
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['output'])
        self.assertIn('provider_unavailable', result['error'])
        with self.assertRaises(ValueError):
            self.arena.rate(result['experiment_id'], 5)
        self.registry.generate_json.side_effect = RuntimeError('PRIVATE_SECRET_RESPONSE')
        result = self.arena.run('quality', {'draft': 'text'}, {'provider': 'gemini', 'model': 'test'})
        self.assertNotIn('PRIVATE_SECRET_RESPONSE', str(result))

    def test_storage_failure_prevents_spending_and_invalid_rating_is_rejected(self):
        store = Mock()
        store.put.side_effect = RuntimeError('store unavailable')
        with self.assertRaises(RuntimeError):
            ModelArena(store, self.registry).run('writing', {'evidence': 'x'}, {'provider': 'gemini', 'model': 'test'})
        self.registry.generate_json.assert_not_called()
        for score in [0, 6, True, '4']:
            with self.assertRaises(ValueError):
                self.arena.rate('unknown', score)
