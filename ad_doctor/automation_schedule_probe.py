"""Read-only CI schedule check using the standard library; claims stay in the service."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import sys
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def due_run_key(settings, now):
    if not settings.get('enabled'):
        return None
    local = now.astimezone(ZoneInfo(settings.get('timezone', 'Asia/Kolkata')))
    hour, minute = map(int, settings.get('schedule_time', '09:00').split(':'))
    due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local < due:
        due -= timedelta(days=1)
    return 'run:' + sha256(('daily:' + due.isoformat()).encode()).hexdigest()[:24]


def retry_due(run, now):
    if not run:
        return True
    if int(run.get('attempts', 1)) >= 2:
        return False
    if run.get('status') == 'running':
        value = run.get('heartbeat_at') or run.get('started_at')
        if not value:
            return False
        last = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return last.tzinfo is not None and now - last >= timedelta(hours=2)
    if run.get('status') not in ('failed', 'interrupted', 'completed_with_gaps') or run.get('retryable') is not True:
        return False
    value = run.get('next_retry_at')
    if not value:
        return False
    due = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return due.tzinfo is not None and due <= now


def read_record(key, url, token):
    query = urlencode({'key': 'eq.' + key, 'select': 'data', 'limit': '1'})
    request = Request(url.rstrip('/') + '/rest/v1/ad_doctor_records?' + query,
                      headers={'apikey': token, 'Authorization': 'Bearer ' + token})
    with build_opener(NoRedirect()).open(request, timeout=20) as response:
        rows = json.load(response)
    return rows[0]['data'] if rows else None


def main(*, env=None, fetch=read_record, now=None):
    env = os.environ if env is None else env
    url, token = env.get('SUPABASE_URL', ''), env.get('SUPABASE_SERVICE_ROLE_KEY', '')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/') or not token:
        print('ready=false')
        print('Shared storage is not configured correctly.', file=sys.stderr)
        return 2
    if env.get('REQUEST_RUN_NOW') == 'true':
        print('ready=true')
        return 0
    try:
        settings = fetch('settings', url, token) or {}
        check_time = now or datetime.now(timezone.utc)
        key = due_run_key(settings, check_time)
        ready = key is not None and retry_due(fetch(key, url, token), check_time)
    except Exception as exc:
        print('ready=false')
        print('Schedule check failed: ' + type(exc).__name__, file=sys.stderr)
        return 1
    print('ready=' + str(ready).lower())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
