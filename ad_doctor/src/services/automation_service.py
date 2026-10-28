"""Provider-independent application workflow used by Streamlit and workers."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import tempfile
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.services.ai_providers import ProviderError
from src.services.automation_store import StoreError
from src.services.editorial_pipeline import candidate_fingerprint, process_topic
from src.services.publication_collection import collect_publications


DEFAULT_ROUTES = {stage: {'provider': 'gemini', 'model': 'gemini-3.5-flash-lite'} for stage in ('research', 'writing', 'quality')}
DEFAULTS = {'enabled': False, 'schedule_time': '09:00', 'timezone': 'Asia/Kolkata', 'max_topics': 1, 'routes': DEFAULT_ROUTES}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class AutomationService:
    def __init__(self, store, registry, database_path=None):
        self.store, self.registry = store, registry
        self.database_path = Path(database_path or Path(tempfile.gettempdir()) / 'ad-doctor-collector' / 'collection.db')

    def settings(self):
        return deepcopy({**DEFAULTS, **(self.store.get('settings') or {})})

    def save_settings(self, config):
        settings = {**self.settings(), **{key: value for key, value in config.items() if key in DEFAULTS}}
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', str(settings['schedule_time'])):
            raise ValueError('Choose a daily time in HH:MM format.')
        try:
            ZoneInfo(settings['timezone'])
        except (TypeError, ZoneInfoNotFoundError):
            raise ValueError('Choose a valid timezone.') from None
        if type(settings['max_topics']) is not int or not 1 <= settings['max_topics'] <= 10:
            raise ValueError('Choose one to ten topics per run.')
        if type(settings['enabled']) is not bool:
            raise ValueError('Schedule enabled must be true or false.')
        if settings['enabled'] and not self.store.shared:
            raise ValueError('Connect shared storage before enabling daily cloud runs.')
        catalog = {item['id']: item for item in self.registry.catalog()}
        routes = settings['routes']
        if not isinstance(routes, dict):
            raise ValueError('Choose research, writing and quality models.')
        for stage in ('research', 'writing', 'quality'):
            route = routes.get(stage, {})
            if not isinstance(route, dict) or route.get('provider') not in catalog or stage not in catalog[route['provider']]['stages']:
                raise ValueError(f'Choose a supported provider for {stage}.')
            if not isinstance(route.get('model'), str) or not re.fullmatch(r'[A-Za-z0-9._:/-]{1,160}', route['model']):
                raise ValueError(f'Enter the provider’s model ID for {stage}.')
        settings['routes'] = {stage: {key: routes[stage][key] for key in ('provider', 'model')} for stage in ('research', 'writing', 'quality')}
        settings['updated_at'] = utc_now()
        self.store.put('settings', 'settings', settings)
        return settings

    def list_runs(self, limit=20):
        runs = self.store.list('run', limit)
        for run in runs:
            if self._stale(run):
                run.update(status='interrupted', retryable=True, next_retry_at=utc_now(), message='Worker interrupted; saved stages are retained.')
                # Display recovery availability without mutating a snapshot:
                # a worker may have resumed since list() read it. Only the
                # lock-holding run_now may persist recovery transitions.
        return runs

    @staticmethod
    def _stale(run):
        if not run or run['status'] != 'running':
            return False
        last = datetime.fromisoformat(run.get('heartbeat_at') or run['started_at'])
        return datetime.now(timezone.utc) - last >= timedelta(hours=2)

    def run_due(self, now=None):
        settings = self.settings()
        if not settings['enabled']:
            return None
        if not self.store.shared:
            raise StoreError('Scheduled cloud operation requires shared storage.')
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError('Scheduler time requires a timezone.')
        local = now.astimezone(ZoneInfo(settings['timezone']))
        hour, minute = map(int, settings['schedule_time'].split(':'))
        due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if local < due:
            due -= timedelta(days=1)
        return self.run_now('daily:' + due.isoformat())

    @staticmethod
    def _can_retry(run):
        return (run.get('retryable') is True and run.get('attempts', 1) < 2
                and run.get('next_retry_at', '') <= utc_now())

    def _load_frontier(self):
        manifest = self.store.get('crawl_checkpoint')
        if manifest and manifest.get('storage_version') == 3:
            sources = []
            for part in manifest['source_parts']:
                rows = []
                for key in part['row_keys']:
                    chunk = self.store.get(key)
                    if chunk is None:
                        raise StoreError('A saved crawl queue chunk is missing. Restore storage before collecting.')
                    rows.extend(chunk['rows'])
                sources.append({**{k: v for k, v in part.items() if k != 'row_keys'}, 'rows': rows})
            return {**{k: v for k, v in manifest.items() if k not in ('source_parts', 'storage_version')}, 'sources': sources}
        if not manifest or manifest.get('storage_version') != 2:
            return manifest
        sources = []
        for key in manifest['source_keys']:
            snapshot = self.store.get(key)
            if snapshot is None:
                raise StoreError('A saved crawl queue is missing. Restore shared storage before collecting.')
            sources.append(snapshot)
        return {**{key: value for key, value in manifest.items() if key not in ('source_keys', 'storage_version')}, 'sources': sources}

    def _save_frontier(self, checkpoint):
        previous = self.store.get('crawl_checkpoint') or {}
        old_keys = set(previous.get('source_keys', []))
        for part in previous.get('source_parts', []):
            old_keys.update(part['row_keys'])
        keys, parts = set(), []
        for source in checkpoint.get('sources', []):
            row_keys = []
            for start in range(0, len(source['rows']), 1000):
                chunk = {'website_url': source['website_url'], 'rows': source['rows'][start:start + 1000]}
                key = 'crawl_chunk:' + sha256(json.dumps(chunk, sort_keys=True).encode()).hexdigest()
                self.store.put(key, 'crawl_chunk', chunk)
                row_keys.append(key)
                keys.add(key)
            parts.append({**{k: v for k, v in source.items() if k != 'rows'}, 'row_keys': row_keys})
        # Immutable chunks first, manifest last. A storage failure before this
        # point preserves the previous complete checkpoint instead of replacing
        # it with a partially uploaded source queue.
        self.store.put('crawl_checkpoint', 'crawl_checkpoint', {**{k: v for k, v in checkpoint.items() if k != 'sources'}, 'source_parts': parts, 'storage_version': 3})
        for obsolete in old_keys - keys:
            self.store.delete(obsolete)

    def run_now(self, request_key=None):
        request_key = request_key or 'manual:' + uuid4().hex
        run_id = sha256(request_key.encode()).hexdigest()[:24]
        run_key = 'run:' + run_id
        existing = self.store.get(run_key)
        if existing and not self._stale(existing) and not self._can_retry(existing):
            return existing
        owner = uuid4().hex
        self.store.release_expired('automation_lock', utc_now())
        if not self.store.create('automation_lock', 'lock', {'owner': owner, 'expires_at': (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()}):
            return {'status': 'busy', 'message': 'Another run is in progress. Refresh to see its saved progress.'}
        # Re-read after the distributed lock; a different worker may just have
        # finished. Never overwrite that worker with our stale pre-lock read.
        existing = self.store.get(run_key)
        if existing and not self._stale(existing) and not self._can_retry(existing):
            self.store.release_claim('automation_lock', owner)
            return existing
        if existing and existing.get('attempts', 1) >= 2:
            self.store.release_claim('automation_lock', owner)
            return existing
        settings = self.settings()
        run = {'run_id': run_id, 'request_key': request_key, 'started_at': utc_now(), 'heartbeat_at': utc_now(),
               'status': 'running', 'stage': 'collecting', 'case_ids': [], 'errors': [], 'settings': settings,
               'attempts': (existing or {}).get('attempts', 0) + 1, 'previous_status': (existing or {}).get('status')}
        claimed = False
        try:
            if existing:
                self.store.put(run_key, 'run', run)
                claimed = True
            else:
                claimed = self.store.create(run_key, 'run', run)
                if not claimed:
                    return self.store.get(run_key)
            def progress(event):
                run.update(stage=event.get('stage', event.get('event_type', 'collecting')), heartbeat_at=utc_now())
                if event.get('source_name'):
                    run['current_source'] = event['source_name']
                self.store.put(run_key, 'run', run)
            inputs = self.store.get('inputs:' + run_id) if existing else None
            if inputs:
                collection = {**inputs.get('collection', existing.get('collection', {})), 'topics': inputs['topics']}
            else:
                collection = collect_publications(self.database_path, frontier_checkpoint=self._load_frontier(), progress_callback=progress)
                frontier = collection.pop('frontier_checkpoint', None)
                if frontier:
                    self._save_frontier(frontier)
                # Persist immutable collected input before any AI calls, so a
                # provider retry does not depend on changing/cached web pages.
                self.store.put('inputs:' + run_id, 'run_inputs', {'topics': collection['topics'][:settings['max_topics']], 'collection': {k: v for k, v in collection.items() if k != 'topics'}})
            run['collection'] = {k: v for k, v in collection.items() if k != 'topics'}
            run['eligible_topics'] = len(collection['topics'])
            progress({'stage': 'checking AI configuration'})
            missing = [stage for stage, route in settings['routes'].items() if not self.registry.configured(route['provider'])]
            if missing and collection['topics']:
                run.update(status='configuration_required', message='Collection saved. Configure API access for: ' + ', '.join(missing))
                return run
            for candidate in collection['topics'][:settings['max_topics']]:
                identity = sha256((candidate_fingerprint(candidate) + json.dumps(settings['routes'], sort_keys=True)).encode()).hexdigest()[:32]
                key = 'case:' + identity
                old = self.store.get(key)
                if old and old['status'] in ('pending_review', 'approved', 'rejected'):
                    run['case_ids'].append(identity)
                    continue
                checkpoint = old
                if checkpoint is None:
                    # A model change creates a new reviewable case. Reuse
                    # compatible successful stages from the same evidence;
                    # process_topic independently checks each stage's route.
                    fingerprint = candidate_fingerprint(candidate)
                    checkpoint = next((record for record in self.store.list('case', 1000) if record.get('fingerprint') == fingerprint), None)
                metadata = {'case_id': identity, 'run_id': run_id, 'created_at': (old or {}).get('created_at', utc_now()), 'routes': settings['routes']}
                def save_checkpoint(result):
                    self.store.put(key, 'case', {**result, **metadata})
                result = process_topic(candidate, self.registry, settings['routes'], checkpoint=checkpoint, checkpoint_callback=save_checkpoint, progress_callback=progress)
                if result['status'] == 'needs_revision':
                    result = process_topic(candidate, self.registry, settings['routes'], checkpoint=result, checkpoint_callback=save_checkpoint, progress_callback=progress)
                    result['automatic_revisions'] = 1
                self.store.put(key, 'case', {**result, **metadata})
                run['case_ids'].append(identity)
            cases = [self.store.get('case:' + case_id) for case_id in run['case_ids']]
            run['drafts_ready'] = sum(case['status'] in ('pending_review', 'approved') for case in cases)
            run['retryable'] = any('provider_unavailable' in error or 'timeout' in error for case in cases for error in case.get('errors', []))
            if run['retryable']:
                run['next_retry_at'] = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
            run['status'] = 'completed_with_gaps' if collection.get('coverage_status') != 'complete' or any(case['status'] in ('blocked', 'needs_revision') for case in cases) else 'completed'
            run['message'] = f"{run['drafts_ready']} draft(s) ready for your review." if cases else 'No topic met the recent three-publisher evidence requirement in this scan. See the source coverage report.'
            run['stage'] = 'finished'
        except StoreError:
            run.update(status='failed', message='Storage became unavailable. Further AI calls were stopped.')
            raise
        except (ProviderError, ValueError) as exc:
            run.update(status='failed', message=str(exc))
        except Exception:
            run.update(status='failed', message='The run stopped. Saved evidence and reports have been retained.')
        finally:
            if run['status'] == 'running':
                run.update(status='interrupted', retryable=True, next_retry_at=(datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat())
            run['finished_at'] = utc_now()
            try:
                if claimed:
                    self.store.put(run_key, 'run', run)
            finally:
                self.store.release_claim('automation_lock', owner)
        return run

    def review_case(self, case_id, approve, reviewer):
        case = self.store.get('case:' + case_id)
        if not reviewer.strip() or len(reviewer) > 100:
            raise ValueError('Enter your name for the editorial review record.')
        if not case or case['status'] not in ('pending_review', 'approved', 'rejected') or not (case.get('quality') or {}).get('passed'):
            raise ValueError('Only a draft that passed quality checks can be approved or rejected.')
        revision = case.get('review_revision', 0)
        case['status'] = 'approved' if approve else 'rejected'
        case['review_revision'] = revision + 1
        case.setdefault('review_history', []).append({'decision': case['status'], 'reviewer': reviewer, 'at': utc_now()})
        if not self.store.compare_and_set('case:' + case_id, revision, case, kind='case'):
            raise ValueError('Another reviewer changed this item. Refresh before reviewing again.')
        return case

    @staticmethod
    def markdown(case):
        draft = case.get('draft') or {}
        evidence = {item['evidence_id']: item for item in (case.get('evidence') or {}).get('source_reports', [])}
        def block(item):
            if not isinstance(item, dict):
                return ''
            references = item.get('evidence_ids')
            references = references if isinstance(references, list) else []
            citations = ' '.join(f"[{key}]({evidence[key]['source_url']})" for key in references if isinstance(key, str) and key in evidence)
            return f"{item.get('text', '')}\n\n{citations}"
        lines = ['# ' + draft.get('title', case['topic']), 'Status: ' + case['status']]
        for name, label in [('diagnosis', 'Diagnosis'), ('doctors_opinion', 'Doctor’s Opinion'), ('prescription', 'Prescription')]:
            lines.extend(['## ' + label, block(draft.get(name))])
        lines.append('## Article')
        article = draft.get('article')
        lines.extend(block(item) for item in article if isinstance(item, dict)) if isinstance(article, list) else None
        lines.append('## Carousel')
        carousel = draft.get('carousel')
        for item in carousel if isinstance(carousel, list) else []:
            if not isinstance(item, dict):
                continue
            lines.extend([f"### {item.get('slide')}. {item.get('title')}", block(item)])
        lines.extend(['## Caption', block(draft.get('caption'))])
        return '\n\n'.join(lines)
