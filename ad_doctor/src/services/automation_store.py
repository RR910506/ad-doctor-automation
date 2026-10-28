"""Private operational records shared by the web app and independent worker.

Secrets are resolved at runtime; records contain only workflow data. Local
SQLite is suitable for development or a host with a persistent disk. Supabase
uses a server-only key and a table protected by row-level security.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

import requests


class StoreError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class SQLiteStore:
    shared = False

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE IF NOT EXISTS ad_doctor_records (key TEXT PRIMARY KEY,kind TEXT NOT NULL,data TEXT NOT NULL,updated_at TEXT NOT NULL)')
            connection.commit()

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def get(self, key):
        with closing(self._connect()) as connection:
            row = connection.execute('SELECT data FROM ad_doctor_records WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, kind, data):
        with closing(self._connect()) as connection:
            connection.execute('INSERT INTO ad_doctor_records VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET kind=excluded.kind,data=excluded.data,updated_at=excluded.updated_at', (key, kind, json.dumps(data), utc_now()))
            connection.commit()

    def create(self, key, kind, data):
        with closing(self._connect()) as connection:
            cursor = connection.execute('INSERT OR IGNORE INTO ad_doctor_records VALUES (?,?,?,?)', (key, kind, json.dumps(data), utc_now()))
            connection.commit()
            return cursor.rowcount == 1

    def list(self, kind, limit=30):
        with closing(self._connect()) as connection:
            rows = connection.execute('SELECT data FROM ad_doctor_records WHERE kind=? ORDER BY updated_at DESC LIMIT ?', (kind, min(max(int(limit), 1), 1000))).fetchall()
        return [json.loads(row[0]) for row in rows]

    def delete(self, key):
        with closing(self._connect()) as connection:
            connection.execute('DELETE FROM ad_doctor_records WHERE key=?', (key,))
            connection.commit()

    def release_expired(self, key, now):
        with closing(self._connect()) as connection:
            connection.execute("DELETE FROM ad_doctor_records WHERE key=? AND json_extract(data,'$.expires_at')<=?", (key, now))
            connection.commit()

    def release_claim(self, key, owner):
        with closing(self._connect()) as connection:
            connection.execute("DELETE FROM ad_doctor_records WHERE key=? AND json_extract(data,'$.owner')=?", (key, owner))
            connection.commit()

    def compare_and_set(self, key, expected_revision, data, kind=None):
        if data.get('review_revision') != int(expected_revision) + 1:
            raise ValueError('The new review revision must increase by one.')
        with closing(self._connect()) as connection:
            if kind is None:
                cursor = connection.execute("UPDATE ad_doctor_records SET data=?,updated_at=? WHERE key=? AND COALESCE(json_extract(data,'$.review_revision'),0)=?", (json.dumps(data), utc_now(), key, expected_revision))
            else:
                cursor = connection.execute("UPDATE ad_doctor_records SET data=?,kind=?,updated_at=? WHERE key=? AND COALESCE(json_extract(data,'$.review_revision'),0)=?", (json.dumps(data), kind, utc_now(), key, expected_revision))
            connection.commit()
            return cursor.rowcount == 1


class SupabaseStore:
    shared = True

    def __init__(self, url, key, session=None):
        parsed = urlsplit(str(url))
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise StoreError('SUPABASE_URL must be your HTTPS project root URL.')
        self.endpoint = str(url).rstrip('/') + '/rest/v1/ad_doctor_records'
        self.session = session or requests.Session()
        self.headers = {'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}

    def _request(self, method, *, params=None, data=None, prefer=None, conflict_ok=False):
        headers = dict(self.headers)
        if prefer:
            headers['Prefer'] = prefer
        try:
            response = self.session.request(method, self.endpoint, params=params, json=data, headers=headers, timeout=30, allow_redirects=False)
        except requests.RequestException:
            raise StoreError('Shared storage is unreachable. Check the project URL and network access.') from None
        if response.status_code == 409 and conflict_ok:
            return False
        if not 200 <= response.status_code < 300:
            raise StoreError(f'Shared storage request failed (HTTP {response.status_code}). Check the table, project status and server secret.')
        try:
            return response.json() if response.content else []
        except ValueError:
            raise StoreError('Shared storage returned an invalid response.') from None

    def get(self, key):
        rows = self._request('GET', params={'key': 'eq.' + key, 'select': 'data', 'limit': '1'})
        return rows[0]['data'] if rows else None

    def put(self, key, kind, data):
        self._request('POST', params={'on_conflict': 'key'}, data={'key': key, 'kind': kind, 'data': data, 'updated_at': utc_now()}, prefer='resolution=merge-duplicates,return=minimal')

    def create(self, key, kind, data):
        return self._request('POST', data={'key': key, 'kind': kind, 'data': data, 'updated_at': utc_now()}, prefer='return=minimal', conflict_ok=True) is not False

    def list(self, kind, limit=30):
        rows = self._request('GET', params={'kind': 'eq.' + kind, 'select': 'data', 'order': 'updated_at.desc', 'limit': str(min(max(int(limit), 1), 1000))})
        return [row['data'] for row in rows]

    def delete(self, key):
        self._request('DELETE', params={'key': 'eq.' + key})

    def release_expired(self, key, now):
        self._request('DELETE', params={'key': 'eq.' + key, 'data->>expires_at': 'lte.' + now})

    def release_claim(self, key, owner):
        self._request('DELETE', params={'key': 'eq.' + key, 'data->>owner': 'eq.' + owner})

    def compare_and_set(self, key, expected_revision, data, kind=None):
        if data.get('review_revision') != int(expected_revision) + 1:
            raise ValueError('The new review revision must increase by one.')
        params = {'key': 'eq.' + key, 'select': 'key'}
        if expected_revision == 0:
            params['or'] = '(data->>review_revision.is.null,data->>review_revision.eq.0)'
        else:
            params['data->>review_revision'] = 'eq.' + str(expected_revision)
        values = {'data': data, 'updated_at': utc_now()}
        if kind is not None:
            values['kind'] = kind
        rows = self._request('PATCH', params=params, data=values, prefer='return=representation')
        return bool(rows)


def build_store(config=None):
    config = config or {}
    def value(name):
        return os.environ.get(name) or config.get(name, '')
    url, key = value('SUPABASE_URL'), value('SUPABASE_SERVICE_ROLE_KEY')
    if bool(url) != bool(key):
        raise StoreError('Configure both SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY for shared storage.')
    if url and key:
        return SupabaseStore(str(url), str(key))
    return SQLiteStore(value('AD_DOCTOR_STATE_DATABASE') or Path(__file__).resolve().parents[2] / 'data/runtime/automation-state.db')
