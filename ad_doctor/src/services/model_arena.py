"""Explicit, saved provider experiments isolated from production evidence.

No experiment can approve content or alter an operational case. Matching
brief_hash values identify comparisons made with exactly the same input/stage.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
from time import perf_counter
from uuid import uuid4

from src.services.ai_providers import ProviderError


ANSWER_SCHEMA = {'type': 'object', 'required': ['answer'], 'additionalProperties': False,
                 'properties': {'answer': {'type': 'string', 'minLength': 1, 'maxLength': 100000}}}
TASKS = {
    'research': 'Analyze only the supplied collected material and evidence. Separate source reports, interpretations, contradictions and unknowns. Cite supplied source URLs. Do not claim to have browsed URLs or independently verified facts.',
    'writing': 'Write an Ad Doctor diagnosis, Doctor’s Opinion, prescription and content outline using only the supplied evidence. Preserve citations. Distinguish source reports, interpretation and opinion. Never invent metrics or results.',
    'quality': 'Audit the supplied draft against only the supplied evidence. Identify unsupported claims, invented metrics, missing citations, weak diagnosis and generic prescriptions. Separate factual problems from subjective editorial preferences.',
}


def _now():
    return datetime.now(timezone.utc).isoformat()


class ModelArena:
    def __init__(self, store, registry):
        self.store, self.registry = store, registry

    def run(self, stage, material, route):
        if stage not in TASKS:
            raise ValueError('Choose research, writing or quality for this experiment.')
        if not isinstance(material, dict) or not material:
            raise ValueError('Supply saved source material or evidence for this experiment.')
        if not isinstance(route, dict) or not route.get('provider') or not route.get('model'):
            raise ValueError('Choose a provider and model for this experiment.')
        try:
            canonical = json.dumps(material, sort_keys=True, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError('Experiment material must be valid JSON data.') from None
        if len(canonical) > 90000:
            raise ValueError('Use experiment material under 90,000 characters.')
        brief_hash = sha256((stage + '\n' + canonical).encode()).hexdigest()
        identity = uuid4().hex
        record = {'experiment_id': identity, 'stage': stage, 'brief_hash': brief_hash,
                  'route': {'provider': route['provider'], 'model': route['model']},
                  'material': json.loads(canonical), 'status': 'running',
                  'created_at': _now(), 'output': None, 'usage': {}, 'rating': None,
                  'notes': '', 'error': None, 'verification': 'unverified_experiment_only'}
        # Do not spend API quota if the private record cannot first be saved.
        self.store.put('experiment:' + identity, 'experiment', record)
        started = perf_counter()
        try:
            response = self.registry.generate_json(provider=route['provider'], model=route['model'],
                system=TASKS[stage] + ' This is an unverified model comparison experiment, never approved production evidence. Return your response in the answer field.',
                payload=material, schema=ANSWER_SCHEMA)
            answer = response.get('data', {}).get('answer')
            if not isinstance(answer, str) or not answer.strip():
                raise ProviderError(route['provider'], 'invalid_structured_output')
            record.update(status='unverified', output=answer, usage=response.get('usage', {}))
        except ProviderError as exc:
            record.update(status='failed', error=str(exc))
        except Exception:
            record.update(status='failed', error='The experiment failed. No production evidence or approval was changed.')
        record.update(latency_seconds=round(perf_counter() - started, 3), finished_at=_now())
        self.store.put('experiment:' + identity, 'experiment', record)
        return record

    def rate(self, experiment_id, score, notes=''):
        if type(score) is not int or not 1 <= score <= 5:
            raise ValueError('Choose a subjective rating from one to five.')
        if not isinstance(notes, str) or len(notes) > 2000:
            raise ValueError('Keep experiment notes under 2,000 characters.')
        key = 'experiment:' + str(experiment_id)
        record = self.store.get(key)
        if not record or record['status'] != 'unverified':
            raise ValueError('Only a completed experiment can receive a subjective rating.')
        record.update(rating=score, notes=notes, rated_at=_now())
        self.store.put(key, 'experiment', record)
        return record
