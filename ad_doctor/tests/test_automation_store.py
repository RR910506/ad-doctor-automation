from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from src.services.automation_store import SQLiteStore, SupabaseStore, StoreError


class AutomationStoreTests(TestCase):
    def test_unique_claim_persistence_owner_release_and_expiry(self):
        with TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / 'state.db')
            self.assertTrue(store.create('claim', 'lock', {'owner': 'one', 'expires_at': '2026-10-28T12:00:00+00:00'}))
            self.assertFalse(store.create('claim', 'lock', {'owner': 'two'}))
            store.release_claim('claim', 'two')
            self.assertEqual(SQLiteStore(store.path).get('claim')['owner'], 'one')
            store.release_expired('claim', '2026-10-28T11:00:00+00:00')
            self.assertIsNotNone(store.get('claim'))
            store.release_expired('claim', '2026-10-28T13:00:00+00:00')
            self.assertIsNone(store.get('claim'))

    def test_review_compare_and_set_rejects_stale_decision(self):
        with TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / 'state.db')
            store.put('case', 'case', {'status': 'pending_review'})
            self.assertTrue(store.compare_and_set('case', 0, {'status': 'approved', 'review_revision': 1}))
            self.assertFalse(store.compare_and_set('case', 0, {'status': 'rejected', 'review_revision': 1}))
            self.assertEqual(store.get('case')['status'], 'approved')

    def test_supabase_does_not_follow_redirect_or_expose_credentials(self):
        session = Mock()
        session.request.return_value.status_code = 403
        session.request.return_value.content = b'PRIVATE_RESPONSE'
        store = SupabaseStore('https://project.supabase.co', 'PRIVATE_KEY', session=session)
        with self.assertRaises(StoreError) as error:
            store.get('settings')
        self.assertNotIn('PRIVATE_KEY', str(error.exception))
        self.assertNotIn('PRIVATE_RESPONSE', str(error.exception))
        self.assertFalse(session.request.call_args.kwargs['allow_redirects'])

    def test_supabase_success_empty_representation_means_cas_conflict(self):
        session = Mock()
        session.request.return_value.status_code = 200
        session.request.return_value.content = b'[]'
        session.request.return_value.json.return_value = []
        store = SupabaseStore('https://project.supabase.co', 'PRIVATE_KEY', session=session)
        self.assertFalse(store.compare_and_set('case', 1, {'status': 'approved', 'review_revision': 2}))
        self.assertEqual(session.request.call_args.kwargs['params']['data->>review_revision'], 'eq.1')

    def test_invalid_project_url_rejected(self):
        for url in ('http://project.supabase.co', 'https://user:pass@project.supabase.co', 'https://project.supabase.co/path'):
            with self.assertRaises(StoreError):
                SupabaseStore(url, 'KEY')
