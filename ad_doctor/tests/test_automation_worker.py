from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from unittest import TestCase
from unittest.mock import Mock, patch

from automation_worker import main


class AutomationWorkerTests(TestCase):
    def test_tick_and_immediate_delegate_without_logging_private_fields(self):
        service = Mock()
        service.run_due.return_value = {'status': 'completed', 'run_id': 'run', 'articles': ['PRIVATE_ARTICLE'], 'api_key': 'PRIVATE_KEY'}
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([], service_factory=lambda: service), 0)
        service.run_due.assert_called_once()
        self.assertNotIn('PRIVATE', output.getvalue())
        service.run_now.return_value = {'status': 'completed'}
        with redirect_stdout(StringIO()):
            main(['--force'], service_factory=lambda: service)
        service.run_now.assert_called_once()

    def test_provider_or_store_exception_logs_only_type(self):
        output = StringIO()
        with redirect_stderr(output):
            code = main([], service_factory=Mock(side_effect=RuntimeError('PRIVATE_KEY')))
        self.assertEqual(code, 1)
        self.assertNotIn('PRIVATE_KEY', output.getvalue())

    def test_local_ci_state_refused_by_default(self):
        with patch.dict('os.environ', {}, clear=True), redirect_stdout(StringIO()):
            self.assertEqual(main([]), 2)
