from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest import TestCase
from unittest.mock import Mock

from automation_schedule_probe import main, due_run_key, retry_due


class AutomationScheduleProbeTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 28, 5, 0, tzinfo=timezone.utc)
        self.env = {'SUPABASE_URL': 'https://project.supabase.co', 'SUPABASE_SERVICE_ROLE_KEY': 'private'}

    def test_disabled_schedule_never_fetches_run(self):
        fetch = Mock(return_value={'enabled': False})
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(main(env=self.env, fetch=fetch, now=self.now), 0)
        self.assertIn('ready=false', output.getvalue())
        fetch.assert_called_once()

    def test_due_slot_matches_india_time_and_previous_day(self):
        settings = {'enabled': True, 'schedule_time': '09:00', 'timezone': 'Asia/Kolkata'}
        first = due_run_key(settings, self.now)
        self.assertEqual(first, due_run_key(settings, self.now + timedelta(hours=4)))
        self.assertNotEqual(first, due_run_key(settings, self.now - timedelta(hours=2)))

    def test_existing_success_skips_and_transient_waits_for_retry(self):
        self.assertFalse(retry_due({'status': 'completed'}, self.now))
        pending = {'status': 'completed_with_gaps', 'retryable': True, 'attempts': 1, 'next_retry_at': (self.now + timedelta(minutes=30)).isoformat()}
        self.assertFalse(retry_due(pending, self.now))
        self.assertTrue(retry_due(pending, self.now + timedelta(hours=1)))
        pending['attempts'] = 2
        self.assertFalse(retry_due(pending, self.now + timedelta(hours=1)))

    def test_stale_worker_can_be_recovered(self):
        self.assertTrue(retry_due({'status': 'running', 'attempts': 1, 'heartbeat_at': (self.now - timedelta(hours=3)).isoformat()}, self.now))
        self.assertFalse(retry_due({'status': 'running', 'attempts': 1, 'heartbeat_at': self.now.isoformat()}, self.now))

    def test_manual_force_needs_shared_config_but_no_specific_ai_key(self):
        fetch = Mock()
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(main(env={**self.env, 'REQUEST_RUN_NOW': 'true'}, fetch=fetch), 0)
        self.assertIn('ready=true', output.getvalue())
        fetch.assert_not_called()
