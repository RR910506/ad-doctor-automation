"""Explicit approved-only document publication and single-recipient delivery.

Neither operation runs as part of collection or AI generation. External side
effects are claimed before execution. Ambiguous email/create outcomes are held
for reconciliation instead of retried blindly.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from hashlib import sha256
import json
import re
import smtplib
import ssl
from uuid import uuid4

from src.services.automation_store import StoreError


class PublishingError(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _email(value):
    value = str(value or '').strip()
    if len(value) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value):
        raise PublishingError('Enter one valid recipient email address.')
    return value


def _plain_document(markdown):
    """Keep citation URLs in a clean Google document; return named-style ranges."""
    output, headings, index = [], [], 1
    for line in str(markdown).splitlines():
        heading = re.match(r'^(#{1,3})\s+(.+)$', line)
        text = heading.group(2) if heading else line
        text = re.sub(r'\[([^\]]+)\]\((https?://[^\s)]+)\)', r'\1 — \2', text)
        text = text.replace('**', '') + '\n'
        size = len(text.encode('utf-16-le')) // 2
        if heading:
            style = {'#': 'TITLE', '##': 'HEADING_1', '###': 'HEADING_2'}[heading.group(1)]
            headings.append({'updateParagraphStyle': {'range': {'startIndex': index, 'endIndex': index + size},
                                                     'paragraphStyle': {'namedStyleType': style},
                                                     'fields': 'namedStyleType'}})
        output.append(text)
        index += size
    return ''.join(output), headings


def _document_text(document):
    return ''.join(element.get('textRun', {}).get('content', '')
                   for block in document.get('body', {}).get('content', [])
                   for element in block.get('paragraph', {}).get('elements', []))


class TeamPublishing:
    def __init__(self, store, secrets, markdown_builder, *, google_factory=None, smtp_factory=None):
        self.store, self.secrets, self.markdown_builder = store, secrets, markdown_builder
        self.google_factory = google_factory or self._google_clients
        self.smtp_factory = smtp_factory or smtplib.SMTP

    @contextmanager
    def _claim_operation(self, key):
        """Serialize initial operations and explicit retries across web workers."""
        now = datetime.now(timezone.utc)
        lock_key, owner = 'publishing_lock:' + key, uuid4().hex
        self.store.release_expired(lock_key, now.isoformat())
        acquired = self.store.create(lock_key, 'publishing_lock', {
            'owner': owner, 'expires_at': (now + timedelta(minutes=30)).isoformat()})
        try:
            yield acquired
        finally:
            if acquired:
                self.store.release_claim(lock_key, owner)

    def readiness(self):
        smtp = all(self.secrets(name) for name in ('SMTP_HOST', 'SMTP_USERNAME', 'SMTP_PASSWORD'))
        sender = self.secrets('FROM_EMAIL') or self.secrets('SMTP_FROM_EMAIL')
        return {'google_docs_configured': bool(self.secrets('GOOGLE_AUTHORIZED_USER_JSON')),
                'email_configured': bool(smtp and sender),
                'google_docs_tested': any(record.get('status') == 'published' for record in self.store.list('publication', 30)),
                'email_tested': any(record.get('status') == 'sent' for record in self.store.list('delivery', 30))}

    def _approved(self, case_id):
        case = self.store.get('case:' + case_id)
        if not case or case.get('status') != 'approved' or (case.get('quality') or {}).get('passed') is not True:
            raise PublishingError('Publication requires a quality-passed draft approved by a human.')
        history = case.get('review_history') or []
        if not history or history[-1].get('decision') != 'approved' or not history[-1].get('reviewer'):
            raise PublishingError('A recorded human approval with reviewer identity is required.')
        if not case.get('draft'):
            raise PublishingError('The approved draft is missing.')
        markdown = self.markdown_builder(case)
        if not isinstance(markdown, str) or not markdown.strip():
            raise PublishingError('The approved draft has no exportable content.')
        return case, markdown, sha256(markdown.encode()).hexdigest()

    def _google_clients(self):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from google_auth_httplib2 import AuthorizedHttp
        import httplib2
        try:
            value = json.loads(self.secrets('GOOGLE_AUTHORIZED_USER_JSON'))
            credentials = Credentials.from_authorized_user_info(value, scopes=[
                'https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive.file'])
            http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=60))
            return (build('docs', 'v1', http=http, cache_discovery=False),
                    build('drive', 'v3', http=http, cache_discovery=False))
        except Exception:
            raise PublishingError('Google document credentials could not be loaded. Check the private OAuth configuration.') from None

    def publish_document(self, case_id):
        case, markdown, content_hash = self._approved(case_id)
        if not self.readiness()['google_docs_configured']:
            raise PublishingError('Configure GOOGLE_AUTHORIZED_USER_JSON before creating a Google document.')
        operation_id = sha256((case_id + '|' + content_hash).encode()).hexdigest()
        key = 'publication:' + operation_id
        with self._claim_operation(key) as acquired:
            if not acquired:
                return self.store.get(key) or {'operation_id': operation_id, 'case_id': case_id, 'status': 'busy'}
            return self._publish_claimed(case_id, case, markdown, content_hash, operation_id, key)

    def _publish_claimed(self, case_id, case, markdown, content_hash, operation_id, key):
        # Re-read under the lease: a concurrent successful call may have
        # finished since the first button click began.
        existing = self.store.get(key)
        if existing and existing['status'] in ('published', 'preparing', 'unknown'):
            return existing
        if existing and existing.get('external_create_started') and not existing.get('document_id'):
            existing.update(status='unknown', updated_at=_now(), error='A document create may already have succeeded. Reconcile its operation tag in Drive before retrying.')
            self.store.put(key, 'publication', existing)
            return existing
        operation = existing or {'operation_id': operation_id, 'case_id': case_id,
                                 'content_hash': content_hash, 'status': 'preparing', 'created_at': _now(),
                                 'visibility': 'private', 'document_id': None}
        # A unique record serializes the initial create. If it survives a crash
        # in preparing state, do not issue another potentially duplicate create.
        if not existing and not self.store.create(key, 'publication', operation):
            return self.store.get(key)
        created_or_found = bool(operation.get('document_id'))
        create_started = False
        try:
            docs, drive = self.google_factory()
            _, _, latest_hash = self._approved(case_id)
            if latest_hash != content_hash:
                raise PublishingError('Approved content changed. Refresh the review before publishing.')
            if not operation.get('document_id'):
                found = drive.files().list(q=f"trashed = false and appProperties has {{ key='ad_doctor_operation' and value='{operation_id}' }}", spaces='drive', fields='files(id,name,webViewLink)', pageSize=2).execute(num_retries=0).get('files', [])
                if len(found) > 1:
                    raise PublishingError('Multiple documents match this operation. Reconcile them before retrying.')
                if found:
                    resource = found[0]
                else:
                    create_started = True
                    operation['external_create_started'] = True
                    self.store.put(key, 'publication', operation)
                    resource = drive.files().create(body={
                        'name': str(case['draft'].get('title') or case.get('topic') or 'Ad Doctor')[:200],
                        'mimeType': 'application/vnd.google-apps.document',
                        'appProperties': {'ad_doctor_operation': operation_id},
                    }, fields='id,webViewLink').execute(num_retries=0)
                operation['document_id'] = resource['id']
                created_or_found = True
                operation['url'] = resource.get('webViewLink') or f"https://docs.google.com/document/d/{resource['id']}/edit"
                self.store.put(key, 'publication', operation)
            document = docs.documents().get(documentId=operation['document_id']).execute(num_retries=0)
            expected, headings = _plain_document(markdown)
            current_text = _document_text(document)
            # An empty document includes its final newline. Matching content
            # needs no second insert after an uncertain prior update.
            if current_text.rstrip('\n') != expected.rstrip('\n'):
                _, _, latest_hash = self._approved(case_id)
                if latest_hash != content_hash:
                    raise PublishingError('Approved content changed. Refresh the review before publishing.')
                if current_text.strip():
                    raise PublishingError('The document contains different text. Review it before replacing approved content.')
                body = {'requests': [{'insertText': {'location': {'index': 1}, 'text': expected}}, *headings]}
                if document.get('revisionId'):
                    body['writeControl'] = {'requiredRevisionId': document['revisionId']}
                docs.documents().batchUpdate(documentId=operation['document_id'], body=body).execute(num_retries=0)
            operation.update(status='published', published_at=_now())
            self.store.put(key, 'publication', operation)
            return operation
        except StoreError:
            raise
        except Exception as exc:
            operation['status'] = 'recovery_required' if created_or_found or not create_started else 'unknown'
            operation['error'] = str(exc) if isinstance(exc, PublishingError) else 'Google publication failed. Review the saved operation before retrying.'
            operation['updated_at'] = _now()
            self.store.put(key, 'publication', operation)
            raise PublishingError(operation['error']) from None

    def send_email(self, case_id, recipient):
        case, markdown, content_hash = self._approved(case_id)
        recipient = _email(recipient)
        if not self.readiness()['email_configured']:
            raise PublishingError('Configure the SMTP host, username, password and sender before delivery.')
        sender = _email(self.secrets('FROM_EMAIL') or self.secrets('SMTP_FROM_EMAIL'))
        try:
            port = int(self.secrets('SMTP_PORT') or 587)
        except ValueError:
            raise PublishingError('SMTP_PORT must be 587 for STARTTLS.') from None
        if port != 587:
            raise PublishingError('This delivery connection requires SMTP port 587 with STARTTLS.')
        host = str(self.secrets('SMTP_HOST')).strip()
        if not host or re.search(r'[\s/\r\n]', host):
            raise PublishingError('Configure a valid SMTP hostname.')
        operation_id = sha256((case_id + '|' + content_hash + '|' + recipient.lower()).encode()).hexdigest()
        key = 'delivery:' + operation_id
        with self._claim_operation(key) as acquired:
            if not acquired:
                return self.store.get(key) or {'operation_id': operation_id, 'case_id': case_id, 'status': 'busy'}
            return self._send_claimed(case_id, case, markdown, content_hash, operation_id, key, recipient, sender, host, port)

    def delivery_status(self, case_id, recipient):
        """Read a recipient's saved outcome without opening an SMTP connection."""
        _, _, content_hash = self._approved(case_id)
        recipient = _email(recipient)
        operation_id = sha256((case_id + '|' + content_hash + '|' + recipient.lower()).encode()).hexdigest()
        return self.store.get('delivery:' + operation_id)

    def _send_claimed(self, case_id, case, markdown, content_hash, operation_id, key, recipient, sender, host, port):
        existing = self.store.get(key)
        # Only a recorded failure before any send attempt is retryable. An
        # uncertain or interrupted send remains held for reconciliation.
        if existing and not (existing.get('status') == 'failed' and existing.get('send_started') is False):
            return existing
        operation = {**(existing or {}), 'operation_id': operation_id, 'case_id': case_id, 'content_hash': content_hash,
                     'recipient': recipient, 'status': 'preparing', 'send_started': False,
                     'created_at': (existing or {}).get('created_at', _now()),
                     'attempts': int((existing or {}).get('attempts', 0)) + 1}
        operation.pop('error', None)
        if existing:
            self.store.put(key, 'delivery', operation)
        elif not self.store.create(key, 'delivery', operation):
            return self.store.get(key)
        message = EmailMessage()
        message['From'], message['To'] = sender, recipient
        message['Subject'] = str(case['draft'].get('title') or case.get('topic') or 'Ad Doctor').replace('\r', ' ').replace('\n', ' ')[:200]
        message['Message-ID'] = f'<ad-doctor-{operation_id}@{sender.rsplit("@", 1)[1]}>'
        message.set_content(markdown)
        send_started = False
        try:
            # Recheck the human approval immediately before any external send.
            _, _, latest_hash = self._approved(case_id)
            if latest_hash != content_hash:
                raise PublishingError('Approved content changed. Refresh the review before delivery.')
            with self.smtp_factory(host, port, timeout=30) as client:
                client.ehlo()
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
                client.login(self.secrets('SMTP_USERNAME'), self.secrets('SMTP_PASSWORD'))
                send_started = True
                operation['send_started'] = True
                self.store.put(key, 'delivery', operation)
                failures = client.send_message(message)
                if failures:
                    raise PublishingError('The mail server did not accept the recipient.')
            operation.update(status='sent', sent_at=_now())
            self.store.put(key, 'delivery', operation)
            return operation
        except StoreError:
            raise
        except Exception:
            operation.update(status='unknown' if send_started else 'failed', updated_at=_now(),
                             error='Delivery outcome is uncertain; reconcile with the mail server before retrying.' if send_started else 'Email connection failed before delivery. Check SMTP settings; no automatic retry was made.')
            self.store.put(key, 'delivery', operation)
            raise PublishingError(operation['error']) from None
