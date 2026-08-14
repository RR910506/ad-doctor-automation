from __future__ import annotations

from dataclasses import replace
import os
import re

from src.config import DATABASE_PATH
from src.security.redaction import redact
from ui.components.pagination import paginate
from ui.fixture_data import demo_payload
from ui.services.production_query_service import ProductionUIDataSource
from ui.status_vocabulary import status_display


def demo_mode_enabled(environ=None):
    return str((environ or os.environ).get("AD_DOCTOR_UI_DEMO_MODE", "false")).lower() in {"1", "true", "yes", "on"}


class UIQueryService:
    def __init__(self, demo=None, state="populated", database_path=DATABASE_PATH, production_source=None):
        self.demo = demo_mode_enabled() if demo is None else bool(demo)
        self.state = state if self.demo and state in {"populated", "empty", "loading", "warning"} else "populated"
        self._production = None if self.demo else (production_source or ProductionUIDataSource(database_path))
        populated = demo_payload() if self.demo else None
        if populated is not None and self.state == "warning":
            populated["automation"] = replace(populated["automation"], enabled=False, next_run="Paused")
        self._payload = populated if populated is not None and self.state in {"populated", "warning"} else {
            "dashboard": None, "sources": (), "articles": (), "qualification": (), "cases": (),
            "content_studio": None, "publishing": None, "subscribers": (), "automation": None,
            "logs": (), "settings": (),
        }

    @property
    def demo_label(self):
        return "Demo data — no production services" if self.demo else "Live workspace"

    @property
    def capabilities(self):
        return {} if self.demo else self._production.capabilities.as_dict()

    def get(self, page):
        return self._payload.get(page, ()) if self.demo else self._production.get(page)

    def list(self, page, *, search="", status="All", sort="Newest first", page_number=1, page_size=10, filters=None):
        if not self.demo:
            return self._production.list(page, search=search, status=status, sort=sort, page_number=page_number, page_size=page_size, filters=filters)
        items = list(self.get(page) or ())
        term = search.strip().lower()
        filters = filters or {}
        if page == "logs":
            items = [replace(item, title=str(redact(item.title)), subtitle=re.sub(r"([A-Za-z0-9._%+-])[^@\s]*(@[^\s]+)", r"\1***\2", str(redact(item.subtitle)))) for item in items]
        if term:
            items = [item for item in items if term in (item.title + " " + item.subtitle).lower()]
        if status != "All":
            items = [item for item in items if status.lower() in {item.status.lower(), item.meta.lower(), status_display(item.status).label.lower()}]
        for key, value in filters.items():
            if value not in (None, "All", ""):
                items = [item for item in items if str(item.details.get(key, "")).lower() == str(value).lower()]
        if sort == "A–Z":
            items.sort(key=lambda item: item.title.lower())
        elif sort == "Oldest first":
            items.reverse()
        return paginate(items, page_number, page_size)
