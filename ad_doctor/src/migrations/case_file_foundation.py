from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


MIGRATION_PATH = Path(__file__).resolve().parents[2] / "database" / "migrations" / "002_case_file_foundation.sql"
APPROVAL_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "database" / "migrations" / "004_unified_approval.sql"
CONTENT_QUALITY_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "database" / "migrations" / "005_content_quality.sql"
PUBLISHING_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "database" / "migrations" / "006_publishing_delivery.sql"


def apply_case_file_foundation(connection: sqlite3.Connection) -> None:
    """Apply the additive Phase 2A schema safely; repeated calls are no-ops."""
    connection.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
    columns = {row[1] for row in connection.execute("PRAGMA table_info(case_article_links)")}
    additions = {
        "source_id": "INTEGER",
        "contributed_to_qualification": "INTEGER NOT NULL DEFAULT 0",
        "relevance_score": "REAL",
        "originating_run_id": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE case_article_links ADD COLUMN {name} {definition}")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_case_article_links_source_id ON case_article_links (source_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_case_article_links_run_id ON case_article_links (originating_run_id)")
    connection.executescript(APPROVAL_MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.executescript(CONTENT_QUALITY_MIGRATION_PATH.read_text(encoding="utf-8"))
    connection.executescript(PUBLISHING_MIGRATION_PATH.read_text(encoding="utf-8"))


def migrate_database(database_path: Path) -> None:
    """Apply Phase 2A to an explicitly supplied database path."""
    from src.db import get_connection
    with closing(get_connection(Path(database_path))) as connection:
        apply_case_file_foundation(connection)
        connection.commit()
