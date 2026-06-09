from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import csv
import json
import re
from pathlib import Path

from src.config import DATA_DIR, DATABASE_PATH
from src.db import get_connection, list_approved_content_outputs, log_run_event


EXPORT_DIR = DATA_DIR / "exports"


@dataclass(frozen=True)
class ExportSummary:
    run_id: str
    approved_outputs: int
    files: list[str]


def export_approved_outputs(
    database_path=DATABASE_PATH,
    export_dir: Path = EXPORT_DIR,
    run_id: str | None = None,
) -> ExportSummary:
    run_id = run_id or datetime.now(UTC).strftime("run_%Y%m%d_%H%M%S")
    export_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []

    with closing(get_connection(database_path)) as connection:
        approved_outputs = list_approved_content_outputs(connection)
        summary_rows = []
        for output in approved_outputs:
            filename = build_export_filename(output)
            path = export_dir / filename
            path.write_text(output["draft_markdown"], encoding="utf-8")
            files.append(str(path))
            summary_rows.append(
                {
                    "id": output["id"],
                    "locked_topic": output["locked_topic"],
                    "content_type": output["content_type"],
                    "path": str(path),
                }
            )

        summary_csv = export_dir / f"{run_id}_approved_exports.csv"
        with summary_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["id", "locked_topic", "content_type", "path"])
            writer.writeheader()
            writer.writerows(summary_rows)
        files.append(str(summary_csv))

        summary_json = export_dir / f"{run_id}_approved_exports.json"
        summary_json.write_text(json.dumps(summary_rows, indent=2), encoding="utf-8")
        files.append(str(summary_json))

        summary = ExportSummary(run_id=run_id, approved_outputs=len(approved_outputs), files=files)
        log_run_event(
            connection,
            run_id,
            "phase_10_export",
            "complete",
            f"Exported {len(approved_outputs)} approved content output(s).",
            json.dumps(summary.__dict__),
        )
        connection.commit()
        return summary


def build_export_filename(output: dict[str, object]) -> str:
    topic = slugify(str(output.get("locked_topic") or "untitled"))
    content_type = slugify(str(output.get("content_type") or "content"))
    return f"{int(output['id']):04d}_{topic}_{content_type}.md"


def slugify(value: str) -> str:
    lowered = value.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return cleaned[:80] or "untitled"


def export_outputs() -> None:
    raise NotImplementedError("Use export_approved_outputs.")
