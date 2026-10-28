"""Explicit consent and suppression for private-team subscriber delivery."""
from datetime import datetime, timezone
from hashlib import sha256

from src.services.automation_store import StoreError
from src.services.team_publishing import _email


def _now():
    return datetime.now(timezone.utc).isoformat()


class SubscriberError(ValueError):
    pass


class TeamSubscribers:
    def __init__(self, store):
        self.store = store

    def add(self, email, consent_note, confirmed=False):
        if confirmed is not True:
            raise SubscriberError('Confirm that this person explicitly agreed to receive Ad Doctor emails.')
        email = _email(email).lower()
        note = str(consent_note or '').strip()
        if not note or len(note) > 2000:
            raise SubscriberError('Record how consent was obtained, using at most 2,000 characters.')
        subscriber_id = sha256(email.encode()).hexdigest()
        key = 'subscriber:' + subscriber_id
        current = self.store.get(key)
        if current and current.get('status') == 'active' and current.get('consent_note') == note:
            return current
        at = _now()
        record = {**(current or {}), 'subscriber_id': subscriber_id, 'email': email,
                  'status': 'active', 'consent_confirmed': True, 'consent_note': note,
                  'consented_at': at, 'updated_at': at, 'created_at': (current or {}).get('created_at', at),
                  'events': list((current or {}).get('events', [])) + [
                      {'event': 'consent_recorded', 'at': at, 'note': note}],
                  'review_revision': int((current or {}).get('review_revision', 0)) + 1}
        if current:
            if not self.store.compare_and_set(key, current.get('review_revision', 0), record, kind='subscriber'):
                raise SubscriberError('This subscription changed in another session. Refresh and confirm the current consent again.')
        elif not self.store.create(key, 'subscriber', record):
            # Do not overwrite a simultaneous unsubscribe or add operation.
            raise SubscriberError('This subscription changed in another session. Refresh before trying again.')
        return record

    def list_subscribers(self, active_only=False):
        rows = self.store.list('subscriber', 1000)
        return [row for row in rows if not active_only or self._active(row)]

    @staticmethod
    def _active(record):
        return bool(record and record.get('status') == 'active' and record.get('consent_confirmed') is True
                    and record.get('consent_note') and record.get('consented_at'))

    def unsubscribe(self, subscriber_id):
        key = 'subscriber:' + str(subscriber_id)
        current = self.store.get(key)
        if not current:
            raise SubscriberError('The subscriber record was not found.')
        if current.get('status') == 'unsubscribed':
            return current
        at = _now()
        record = {**current, 'status': 'unsubscribed', 'unsubscribed_at': at, 'updated_at': at,
                  'review_revision': int(current.get('review_revision', 0)) + 1,
                  'events': list(current.get('events', [])) + [{'event': 'unsubscribed', 'at': at}]}
        if not self.store.compare_and_set(key, current.get('review_revision', 0), record, kind='subscriber'):
            raise SubscriberError('This subscription changed in another session. Refresh before unsubscribing again.')
        return record

    def deliver(self, case_id, publisher, confirmed=False):
        if confirmed is not True:
            raise SubscriberError('Confirm delivery of this approved content to the consented subscriber list.')
        eligible = self.list_subscribers(active_only=True)
        started_at = datetime.now(timezone.utc)
        result = {'eligible': len(eligible), 'attempted': 0, 'sent': 0, 'already_sent': 0,
                  'suppressed': 0, 'held': 0, 'failed': 0,
                  'remaining': 0, 'outcomes': []}
        status_reader = getattr(type(publisher), 'delivery_status', None)
        # The publisher independently enforces human-approved content and each
        # content/recipient claim. A bounded batch never circumvents that gate.
        for candidate in eligible:
            subscriber = self.store.get('subscriber:' + candidate['subscriber_id'])
            if not self._active(subscriber):
                result['suppressed'] += 1
                result['outcomes'].append({'subscriber_id': candidate['subscriber_id'], 'status': 'suppressed'})
                continue
            # Skip terminal/uncertain prior outcomes without consuming this
            # batch's new-send allowance. Otherwise repeatedly clicking a
            # 105-person list would keep revisiting its first 100 recipients.
            if callable(status_reader):
                existing = publisher.delivery_status(case_id, subscriber['email'])
                if existing and existing.get('status') == 'sent':
                    result['already_sent'] += 1
                    result['outcomes'].append({'subscriber_id': subscriber['subscriber_id'], 'status': 'already_sent'})
                    continue
                if existing and existing.get('status') in ('unknown', 'preparing', 'busy'):
                    result['held'] += 1
                    result['outcomes'].append({'subscriber_id': subscriber['subscriber_id'], 'status': existing['status']})
                    continue
            if result['attempted'] >= 100:
                result['remaining'] += 1
                continue
            result['attempted'] += 1
            try:
                delivery = publisher.send_email(case_id, subscriber['email'])
                status = str(delivery.get('status') or 'unknown')
                if status == 'sent':
                    sent_at = delivery.get('sent_at')
                    try:
                        previous = sent_at and datetime.fromisoformat(sent_at) < started_at
                    except (TypeError, ValueError):
                        previous = False
                    result['already_sent' if previous else 'sent'] += 1
                elif status == 'failed':
                    result['failed'] += 1
                else:
                    result['held'] += 1
                result['outcomes'].append({'subscriber_id': subscriber['subscriber_id'], 'status': status})
            except StoreError:
                # Stop the batch when durable operation logging is unavailable;
                # continuing could spend/send without safe duplicate records.
                raise
            except Exception:
                # SMTP/network exceptions may contain addresses, passwords or
                # provider responses; details stay in the publisher's private
                # operation record rather than being echoed in a batch error.
                result['failed'] += 1
                result['outcomes'].append({'subscriber_id': subscriber['subscriber_id'], 'status': 'failed',
                                           'reason': 'Delivery was blocked or failed. Review its private operation record.'})
        return result
