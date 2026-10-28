"""Run the private automation service independently from Streamlit."""
import argparse
import json
import os
from pathlib import Path
import sys


def main(argv=None, *, service_factory=None):
    parser = argparse.ArgumentParser(description='Run Ad Doctor automation without a browser or Codex session.')
    parser.add_argument('--force', action='store_true', help='Run immediately instead of checking the saved schedule.')
    parser.add_argument('--allow-local', action='store_true', help='Permit local SQLite for development only.')
    args = parser.parse_args(argv)
    if service_factory is None:
        if not args.allow_local and not (os.environ.get('SUPABASE_URL') and os.environ.get('SUPABASE_SERVICE_ROLE_KEY')):
            print(json.dumps({'status': 'configuration_required', 'reason': 'shared_store_required'}))
            return 2
        from src.services.ai_providers import ProviderRegistry
        from src.services.automation_store import build_store
        from src.services.automation_service import AutomationService
        def service_factory():
            return AutomationService(build_store(), ProviderRegistry(lambda name: os.environ.get(name, '')),
                                     database_path=Path(os.environ.get('AD_DOCTOR_COLLECTION_DATABASE', '/tmp/ad-doctor-collection.db')))
    try:
        service = service_factory()
        result = service.run_now() if args.force else service.run_due()
        if result is None:
            result = {'status': 'skipped', 'reason': 'no_due_slot'}
        safe_keys = ('run_id', 'status', 'reason', 'drafts_ready', 'eligible_topics', 'finished_at')
        print(json.dumps({key: result[key] for key in safe_keys if key in result and isinstance(result[key], (str, int, float, bool, type(None)))}))
        return 1 if result.get('status') in ('failed', 'error') else 0
    except Exception as exc:
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
