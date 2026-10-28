from pathlib import Path
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, MagicMock

from src.services.automation_store import SQLiteStore
from src.services.team_publishing import TeamPublishing, PublishingError, _plain_document


class TeamPublishingTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = SQLiteStore(Path(self.directory.name) / 'private.db')
        self.case = {'case_id': 'case1', 'topic': 'A marketing development', 'status': 'approved',
                     'draft': {'title': 'The Ad Doctor diagnosis'}, 'quality': {'passed': True},
                     'review_history': [{'decision': 'approved', 'reviewer': 'Team editor'}]}
        self.store.put('case:case1', 'case', self.case)
        self.values = {'GOOGLE_AUTHORIZED_USER_JSON': 'PRIVATE_CREDENTIAL_MARKER',
                       'SMTP_HOST': 'smtp.example.com', 'SMTP_PORT': '587',
                       'SMTP_USERNAME': 'user', 'SMTP_PASSWORD': 'PRIVATE_PASSWORD',
                       'FROM_EMAIL': 'editor@example.com'}
        self.docs, self.drive = Mock(), Mock()
        self.drive.files.return_value.list.return_value.execute.return_value = {'files': []}
        self.drive.files.return_value.create.return_value.execute.return_value = {'id': 'document1'}
        self.docs.documents.return_value.get.return_value.execute.return_value = {'revisionId': 'revision1', 'body': {'content': [{'paragraph': {'elements': [{'textRun': {'content': '\n'}}]}}]}}
        self.smtp = MagicMock()
        self.mail = self.smtp.return_value.__enter__.return_value
        self.mail.send_message.return_value = {}
        self.service = TeamPublishing(self.store, self.values.get, lambda case: '# Title\n\nApproved text.\n', google_factory=lambda: (self.docs, self.drive), smtp_factory=self.smtp)

    def test_only_recorded_human_approval_can_publish_or_email(self):
        for mutation in ({'status': 'pending_review'}, {'quality': {'passed': False}}, {'review_history': []}):
            self.store.put('case:case1', 'case', {**self.case, **mutation})
            with self.assertRaises(PublishingError):
                self.service.publish_document('case1')
            with self.assertRaises(PublishingError):
                self.service.send_email('case1', 'reader@example.com')
        self.drive.files.assert_not_called()
        self.smtp.assert_not_called()

    def test_document_create_is_private_formatted_and_idempotent(self):
        result = self.service.publish_document('case1')
        again = self.service.publish_document('case1')
        self.assertEqual(result, again)
        self.assertEqual(result['status'], 'published')
        self.assertEqual(result['visibility'], 'private')
        self.drive.files.return_value.create.assert_called_once()
        body = self.drive.files.return_value.create.call_args.kwargs['body']
        self.assertEqual(body['mimeType'], 'application/vnd.google-apps.document')
        self.assertIn('ad_doctor_operation', body['appProperties'])
        self.drive.permissions.assert_not_called()
        update = self.docs.documents.return_value.batchUpdate.call_args.kwargs['body']
        self.assertEqual(update['writeControl'], {'requiredRevisionId': 'revision1'})
        self.assertEqual(update['requests'][1]['updateParagraphStyle']['paragraphStyle']['namedStyleType'], 'TITLE')

    def test_existing_remote_operation_is_reused_without_duplicate_insert(self):
        self.drive.files.return_value.list.return_value.execute.return_value = {'files': [{'id': 'already-created'}]}
        self.docs.documents.return_value.get.return_value.execute.return_value = {'body': {'content': [{'paragraph': {'elements': [{'textRun': {'content': 'Title\n\nApproved text.\n'}}]}}]}}
        result = self.service.publish_document('case1')
        self.assertEqual(result['document_id'], 'already-created')
        self.drive.files.return_value.create.assert_not_called()
        self.docs.documents.return_value.batchUpdate.assert_not_called()

    def test_ambiguous_google_create_is_held_without_another_create(self):
        self.drive.files.return_value.create.return_value.execute.side_effect = RuntimeError('PRIVATE_CREDENTIAL_MARKER')
        with self.assertRaises(PublishingError) as error:
            self.service.publish_document('case1')
        self.assertNotIn('PRIVATE_CREDENTIAL_MARKER', str(error.exception))
        operation = self.store.list('publication')[0]
        self.assertEqual(operation['status'], 'unknown')
        self.assertEqual(self.service.publish_document('case1'), operation)
        self.assertEqual(self.drive.files.return_value.create.return_value.execute.call_count, 1)

    def test_email_uses_tls_and_duplicate_recipient_is_not_sent_twice(self):
        result = self.service.send_email('case1', 'reader@example.com')
        self.assertEqual(result['status'], 'sent')
        self.assertEqual(self.service.send_email('case1', 'READER@example.com'), result)
        self.mail.starttls.assert_called_once()
        self.mail.send_message.assert_called_once()
        message = self.mail.send_message.call_args.args[0]
        self.assertEqual(message['To'], 'reader@example.com')
        self.assertNotIn('PRIVATE_PASSWORD', str(self.store.list('delivery')))

    def test_ambiguous_email_failure_blocks_blind_resend_and_redacts_errors(self):
        self.mail.send_message.side_effect = RuntimeError('PRIVATE_PASSWORD')
        with self.assertRaises(PublishingError) as error:
            self.service.send_email('case1', 'reader@example.com')
        self.assertNotIn('PRIVATE_PASSWORD', str(error.exception))
        operation = self.store.list('delivery')[0]
        self.assertEqual(operation['status'], 'unknown')
        self.assertEqual(self.service.send_email('case1', 'reader@example.com'), operation)
        self.mail.send_message.assert_called_once()

    def test_email_before_send_failure_can_retry_after_configuration_fix(self):
        self.mail.login.side_effect = RuntimeError('PRIVATE_PASSWORD')
        with self.assertRaises(PublishingError):
            self.service.send_email('case1', 'reader@example.com')
        held = self.store.list('delivery')[0]
        self.assertEqual(held['status'], 'failed')
        self.assertIs(held['send_started'], False)
        self.mail.send_message.assert_not_called()
        self.mail.login.side_effect = None
        result = self.service.send_email('case1', 'reader@example.com')
        self.assertEqual(result['status'], 'sent')
        self.assertEqual(result['attempts'], 2)
        self.mail.send_message.assert_called_once()

    def test_publication_retry_is_serialized_and_holds_unknown_creation(self):
        self.drive.files.return_value.list.return_value.execute.side_effect = RuntimeError('failure before create')
        with self.assertRaises(PublishingError):
            self.service.publish_document('case1')
        held = self.store.list('publication')[0]
        self.assertEqual(held['status'], 'recovery_required')
        self.assertFalse(held.get('external_create_started'))
        key = 'publication:' + held['operation_id']
        lock = 'publishing_lock:' + key
        self.store.create(lock, 'publishing_lock', {'owner': 'another-worker', 'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()})
        self.drive.files.return_value.list.return_value.execute.side_effect = None
        self.assertEqual(self.service.publish_document('case1'), held)
        self.drive.files.return_value.create.assert_not_called()
        self.store.release_claim(lock, 'another-worker')
        result = self.service.publish_document('case1')
        self.assertEqual(result['status'], 'published')
        self.drive.files.return_value.create.assert_called_once()

    def test_email_retry_lease_blocks_concurrent_before_send_retry(self):
        self.mail.login.side_effect = RuntimeError('pre-send failure')
        with self.assertRaises(PublishingError):
            self.service.send_email('case1', 'reader@example.com')
        held = self.store.list('delivery')[0]
        lock = 'publishing_lock:delivery:' + held['operation_id']
        self.store.create(lock, 'publishing_lock', {'owner': 'another-worker', 'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()})
        self.mail.login.side_effect = None
        self.assertEqual(self.service.send_email('case1', 'reader@example.com'), held)
        self.mail.send_message.assert_not_called()
        self.store.release_claim(lock, 'another-worker')
        self.assertEqual(self.service.send_email('case1', 'reader@example.com')['status'], 'sent')

    def test_crashed_create_retry_cannot_issue_a_duplicate_create(self):
        self.drive.files.return_value.list.return_value.execute.side_effect = RuntimeError('before create')
        with self.assertRaises(PublishingError):
            self.service.publish_document('case1')
        held = self.store.list('publication')[0]
        held['external_create_started'] = True
        self.store.put('publication:' + held['operation_id'], 'publication', held)
        self.drive.files.return_value.list.return_value.execute.side_effect = None
        result = self.service.publish_document('case1')
        self.assertEqual(result['status'], 'unknown')
        self.drive.files.return_value.create.assert_not_called()

    def test_injected_recipient_and_insecure_port_are_rejected_before_send(self):
        with self.assertRaises(PublishingError):
            self.service.send_email('case1', 'reader@example.com\nBcc: hidden@example.com')
        self.values['SMTP_PORT'] = '25'
        with self.assertRaises(PublishingError):
            self.service.send_email('case1', 'reader@example.com')
        self.smtp.assert_not_called()

    def test_unicode_document_headings_use_utf16_ranges_and_keep_links(self):
        text, headings = _plain_document('# Doctor 🩺\n\n[Evidence](https://publisher.example/story)')
        self.assertIn('Evidence — https://publisher.example/story', text)
        self.assertEqual(headings[0]['updateParagraphStyle']['range']['endIndex'], 11)
