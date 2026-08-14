from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Iterable, Iterator, Sequence


@dataclass(frozen=True, slots=True)
class DatabaseCapabilities:
    legacy_pipeline_available: bool
    case_files_available: bool
    evidence_available: bool
    structured_content_available: bool
    quality_reviews_available: bool
    unified_approval_available: bool
    publishing_available: bool
    subscribers_available: bool
    deliveries_available: bool

    def as_dict(self) -> dict[str, bool]:
        return {name: bool(getattr(self, name)) for name in self.__dataclass_fields__}


class UIReadRepository:
    """Small, read-only SQLite gateway used only by the modern UI."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).expanduser().resolve()
        self._tables: set[str] | None = None

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            f"{self.database_path.as_uri()}?mode=ro",
            uri=True,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        try:
            yield connection
        finally:
            connection.close()

    @property
    def tables(self) -> set[str]:
        if self._tables is None:
            if not self.database_path.exists():
                self._tables = set()
            else:
                with self.connection() as connection:
                    self._tables = {
                        str(row[0])
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
        return set(self._tables)

    def has_table(self, table: str) -> bool:
        return table in self.tables

    def columns(self, table: str) -> set[str]:
        if not self.has_table(table):
            return set()
        safe_name = table.replace('"', '""')
        with self.connection() as connection:
            return {
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{safe_name}")')
            }

    def capabilities(self) -> DatabaseCapabilities:
        tables = self.tables
        return DatabaseCapabilities(
            legacy_pipeline_available={"sources", "articles", "topics"}.issubset(tables),
            case_files_available="cases" in tables,
            evidence_available="evidence_packs" in tables,
            structured_content_available="content_packages" in tables,
            quality_reviews_available="quality_reviews" in tables,
            unified_approval_available="approval_events" in tables,
            publishing_available={"publications", "external_operations"}.issubset(tables),
            subscribers_available="subscribers" in tables,
            deliveries_available={"deliveries", "distribution_runs"}.issubset(tables),
        )

    def scalar(self, sql: str, parameters: Sequence[object] = (), default=0):
        with self.connection() as connection:
            row = connection.execute(sql, tuple(parameters)).fetchone()
        return row[0] if row and row[0] is not None else default

    def rows(self, sql: str, parameters: Sequence[object] = ()) -> list[dict[str, object]]:
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(sql, tuple(parameters)).fetchall()]

    def row(self, sql: str, parameters: Sequence[object] = ()) -> dict[str, object] | None:
        with self.connection() as connection:
            result = connection.execute(sql, tuple(parameters)).fetchone()
        return dict(result) if result else None

    def page(
        self,
        select_sql: str,
        count_sql: str,
        parameters: Iterable[object],
        *,
        page_number: int,
        page_size: int,
    ) -> tuple[list[dict[str, object]], dict[str, int]]:
        page_size = max(1, min(int(page_size), 100))
        parameters = tuple(parameters)
        total = int(self.scalar(count_sql, parameters, 0))
        pages = max(1, (total + page_size - 1) // page_size)
        page_number = max(1, min(int(page_number), pages))
        offset = (page_number - 1) * page_size
        rows = self.rows(select_sql + " LIMIT ? OFFSET ?", parameters + (page_size, offset))
        return rows, {
            "page": page_number,
            "page_size": page_size,
            "total": total,
            "pages": pages,
        }
