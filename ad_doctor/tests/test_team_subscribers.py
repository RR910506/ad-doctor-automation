from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from src.services.automation_store import SQLiteStore, StoreError
from src.services.team_subscribers import TeamSubscribers, SubscriberError


class TeamSubscriberTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = SQLiteStore(Path(self.directory.name) / 'state.db')
        self.service = TeamSubscribers(self.store)

    def test_explicit_consent_normalizes_duplicate_and_rejects_missing_proof(self):
        with self.assertRaises(SubscriberError):
            self.service.add('reader@example.com', 'Opted in on a call.')
        with self.assertRaises(SubscriberError):
            self.service.add('reader@example.com', '', confirmed=True)
        first = self.service.add('READER@example.com', 'Opted in on a call.', confirmed=True)
        again = self.service.add('reader@example.com', 'Opted in on a call.', confirmed=True)
        self.assertEqual(first, again)
        self.assertEqual(len(self.service.list_subscribers()), 1)
        self.assertEqual(first['email'], 'reader@example.com')
        self.assertEqual(len(first['events']), 1)

    def test_unsubscribe_suppresses_and_reactivation_requires_new_consent_action(self):
        first = self.service.add('reader@example.com', 'Requested newsletter.', confirmed=True)
        suppression = self.service.unsubscribe(first['subscriber_id'])
        self.assertEqual(self.service.unsubscribe(first['subscriber_id']), suppression)
        self.assertEqual(self.service.list_subscribers(active_only=True), [])
        with self.assertRaises(SubscriberError):
            self.service.add('reader@example.com', 'Requested again.', confirmed=False)
        readded = self.service.add('reader@example.com', 'Requested again after opting out.', confirmed=True)
        self.assertEqual(readded['status'], 'active')
        self.assertEqual([event['event'] for event in readded['events']], ['consent_recorded', 'unsubscribed', 'consent_recorded'])

    def test_batch_requires_confirmation_and_rechecks_concurrent_suppression(self):
        contact = self.service.add('reader@example.com', 'Requested newsletter.', confirmed=True)
        publisher = Mock()
        with self.assertRaises(SubscriberError):
            self.service.deliver('approved-case', publisher)
        # Snapshot includes the reader, but opt-out occurs before its send.
        with patch.object(self.service, 'list_subscribers', return_value=[contact]):
            self.service.unsubscribe(contact['subscriber_id'])
            report = self.service.deliver('approved-case', publisher, confirmed=True)
        self.assertEqual(report['suppressed'], 1)
        self.assertEqual(report['attempted'], 0)
        publisher.send_email.assert_not_called()

    def test_batch_respects_unknown_delivery_and_redacts_provider_errors(self):
        self.service.add('one@example.com', 'Requested newsletter.', confirmed=True)
        self.service.add('two@example.com', 'Requested newsletter.', confirmed=True)
        publisher = Mock()
        publisher.send_email.side_effect = [{'status': 'unknown'}, RuntimeError('SMTP_PASSWORD_SECRET')]
        report = self.service.deliver('approved-case', publisher, confirmed=True)
        self.assertEqual(report['held'], 1)
        self.assertEqual(report['failed'], 1)
        self.assertEqual(report['sent'], 0)
        self.assertNotIn('SMTP_PASSWORD_SECRET', str(report))
        self.assertEqual(publisher.send_email.call_count, 2)

    def test_batch_stops_immediately_when_durable_delivery_logging_fails(self):
        self.service.add('one@example.com', 'Requested newsletter.', confirmed=True)
        self.service.add('two@example.com', 'Requested newsletter.', confirmed=True)
        publisher = Mock()
        publisher.send_email.side_effect = StoreError('Private storage unavailable.')
        with self.assertRaises(StoreError):
            self.service.deliver('approved-case', publisher, confirmed=True)
        self.assertEqual(publisher.send_email.call_count, 1)

    def test_later_batch_advances_past_first_hundred_successful_deliveries(self):
        for index in range(105):
            self.service.add(f'reader{index}@example.com', 'Requested newsletter.', confirmed=True)
        class Publisher:
            def __init__(self):
                self.records, self.calls = {}, []

            def delivery_status(self, case_id, recipient):
                return self.records.get((case_id, recipient))

            def send_email(self, case_id, recipient):
                self.calls.append(recipient)
                self.records[(case_id, recipient)] = {'status': 'sent'}
                return self.records[(case_id, recipient)]
        publisher = Publisher()
        first = self.service.deliver('approved-case', publisher, confirmed=True)
        self.assertEqual(first['sent'], 100)
        self.assertEqual(first['remaining'], 5)
        second = self.service.deliver('approved-case', publisher, confirmed=True)
        self.assertEqual(second['already_sent'], 100)
        self.assertEqual(second['sent'], 5)
        self.assertEqual(second['remaining'], 0)
        self.assertEqual(len(publisher.calls), 105)
        self.assertEqual(len(set(publisher.calls)), 105)
