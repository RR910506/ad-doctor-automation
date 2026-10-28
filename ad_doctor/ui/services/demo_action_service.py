"""Prevent every mutation in the public sample workspace."""
from ui.services.ui_action_service import UIActionResult


class DemoActionService:
    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)

        def unavailable(*args, **kwargs):
            return UIActionResult(False, 'This is a read-only sample workspace. Changes and live integrations are disabled.', 'demo_read_only')
        return unavailable
