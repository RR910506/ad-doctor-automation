from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from src.services.ai_providers import ProviderRegistry
from src.services.automation_service import AutomationService
from src.services.automation_store import SQLiteStore
from test_editorial_pipeline import candidate, research, draft, registry, ROUTES, PASS


class AutomationServiceTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = SQLiteStore(Path(self.directory.name) / 'state.db')
        self.provider = registry([research(), draft(), PASS])
        self.provider.configured.return_value = True
        self.provider.catalog.return_value = ProviderRegistry(lambda _: '').catalog()
        self.service = AutomationService(self.store, self.provider, Path(self.directory.name) / 'collection.db')
        self.service.save_settings({'routes': ROUTES})

    def collection(self):
        return {'run_id': 'collect1', 'topics': [candidate()], 'collection': {'sources_checked': 30, 'inserted': 3, 'updated': 0},
                'coverage_status': 'bounded_scan', 'frontier_checkpoint': {'version': 1, 'sources': []}, 'articles': [], 'source_reports': []}

    def test_complete_flow_separate_routes_persistence_idempotency_approval(self):
        with patch('src.services.automation_service.collect_publications', side_effect=lambda *a, **kw: self.collection()) as collect:
            first = self.service.run_now('same-request')
            repeated = self.service.run_now('same-request')
        self.assertEqual(first, repeated)
        collect.assert_called_once()
        self.assertEqual(self.provider.generate_json.call_count, 3)
        case = self.store.list('case')[0]
        self.assertEqual(case['status'], 'pending_review')
        calls = self.provider.generate_json.call_args_list
        self.assertEqual([call.kwargs['provider'] for call in calls], ['perplexity', 'openai', 'anthropic'])
        reloaded = AutomationService(SQLiteStore(self.store.path), self.provider)
        approved = reloaded.review_case(case['case_id'], True, 'Rajesh')
        self.assertEqual(approved['status'], 'approved')
        self.assertEqual(approved['review_revision'], 1)
        self.assertIn('https://publisher1.example/article', reloaded.markdown(approved))
        self.assertIsNone(self.store.get('automation_lock'))

    def test_automatic_revision_is_bounded_and_preserves_research(self):
        self.provider.generate_json.side_effect = [{'data': value} for value in [research(), draft(), {'passed': False, 'issues': ['Clearer prescription required'], 'summary': 'Revise'}, draft(), PASS]]
        with patch('src.services.automation_service.collect_publications', side_effect=lambda *a, **kw: self.collection()):
            self.service.run_now('revision')
        self.assertEqual(self.provider.generate_json.call_count, 5)
        self.assertEqual(self.store.list('case')[0]['status'], 'pending_review')

    def test_missing_provider_saves_collection_without_spending(self):
        self.provider.configured.return_value = False
        with patch('src.services.automation_service.collect_publications', side_effect=lambda *a, **kw: self.collection()):
            run = self.service.run_now('missing-key')
        self.assertEqual(run['status'], 'configuration_required')
        self.assertEqual(run['collection']['collection']['inserted'], 3)
        self.provider.generate_json.assert_not_called()

    def test_global_lock_blocks_concurrent_work_and_wrong_owner_release(self):
        self.store.create('automation_lock', 'lock', {'owner': 'other', 'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
        self.store.release_claim('automation_lock', 'wrong')
        with patch('src.services.automation_service.collect_publications') as collect:
            run = self.service.run_now('second')
        self.assertEqual(run['status'], 'busy')
        collect.assert_not_called()

    def test_india_schedule_claim_is_unique(self):
        self.store.shared = True
        self.service.save_settings({'enabled': True, 'schedule_time': '09:00'})
        with patch('src.services.automation_service.collect_publications', side_effect=lambda *a, **kw: self.collection()) as collect:
            first = self.service.run_due(datetime(2026, 10, 28, 3, 40, tzinfo=timezone.utc))
            second = self.service.run_due(datetime(2026, 10, 28, 4, 0, tzinfo=timezone.utc))
        self.assertEqual(first, second)
        self.assertEqual(first['request_key'], 'daily:2026-10-28T09:00:00+05:30')
        collect.assert_called_once()

    def test_provider_failure_never_approves_or_leaks(self):
        self.provider.generate_json.side_effect = RuntimeError('PRIVATE_VALUE')
        with patch('src.services.automation_service.collect_publications', side_effect=lambda *a, **kw: self.collection()):
            self.service.run_now('failed')
        case = self.store.list('case')[0]
        self.assertEqual(case['status'], 'blocked')
        self.assertNotIn('PRIVATE_VALUE', str(case))
        with self.assertRaises(ValueError):
            self.service.review_case(case['case_id'], True, 'Rajesh')

    def test_schedule_and_route_validation(self):
        for config in [{'enabled': True}, {'schedule_time': '25:00'}, {'max_topics': 0}, {'routes': {'research': {'provider': 'unknown'}}}]:
            with self.assertRaises(ValueError):
                self.service.save_settings(config)

    def test_chunked_frontier_preserves_large_known_queue(self):
        checkpoint = {'version': 1, 'sources': [{'website_url': 'https://publication.example',
            'rows': [{'url': f'https://publication.example/article-{i}', 'status': 'queued'} for i in range(12003)],
            'total_urls': 12003, 'overflow_urls': 0}]}
        self.service._save_frontier(checkpoint)
        self.assertEqual(self.service._load_frontier(), checkpoint)
        chunks = self.store.list('crawl_chunk', 100)
        self.assertEqual(len(chunks), 13)
        self.assertTrue(all(len(chunk['rows']) <= 1000 for chunk in chunks))
        checkpoint['sources'][0]['rows'] = checkpoint['sources'][0]['rows'][:2]
        self.service._save_frontier(checkpoint)
        self.assertEqual(len(self.store.list('crawl_chunk', 100)), 1)
        self.assertEqual(self.service._load_frontier(), checkpoint)

    def test_switching_quality_provider_reuses_same_evidence_and_draft(self):
        with patch('src.services.automation_service.collect_publications', side_effect=lambda *a, **kw: self.collection()):
            first = self.service.run_now('initial')
            import copy
            routes = copy.deepcopy(ROUTES)
            routes['quality'] = {'provider': 'gemini', 'model': 'new-reviewer'}
            self.service.save_settings({'routes': routes})
            self.provider.generate_json.reset_mock()
            self.provider.generate_json.side_effect = [{'data': PASS}]
            second = self.service.run_now('switch-quality')
        self.assertEqual(self.provider.generate_json.call_count, 1)
        self.assertEqual(self.provider.generate_json.call_args.kwargs['provider'], 'gemini')
        self.assertNotEqual(first['case_ids'], second['case_ids'])
        self.assertEqual(self.store.get('case:' + second['case_ids'][0])['status'], 'pending_review')
