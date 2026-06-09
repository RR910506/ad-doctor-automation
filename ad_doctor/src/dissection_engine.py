from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import json

from src.config import DATABASE_PATH, TEMPLATE_DIR
from src.db import (
    get_connection,
    get_latest_locked_run_id,
    insert_dissection_output,
    list_research_dossiers,
    log_run_event,
)


@dataclass(frozen=True)
class DissectionSummary:
    run_id: str
    dissections_created: int


def run_campaign_dissections(
    database_path=DATABASE_PATH,
    run_id: str | None = None,
    template_path=None,
) -> DissectionSummary:
    requested_run_id = run_id
    run_id = requested_run_id or datetime.now(UTC).strftime("run_%Y%m%d_%H%M%S")
    template_path = template_path or TEMPLATE_DIR / "dissection_roadmap.md"
    template_text = template_path.read_text(encoding="utf-8") if template_path.exists() else ""
    created = 0

    with closing(get_connection(database_path)) as connection:
        locked_run_id = requested_run_id or get_latest_locked_run_id(connection)
        dossiers = (
            list_research_dossiers(connection, run_id=locked_run_id)
            if locked_run_id
            else []
        )
        for dossier_row in dossiers:
            dossier = json.loads(dossier_row["dossier_json"])
            output = build_dissection_markdown(dossier, template_text)
            insert_dissection_output(
                connection,
                locked_topic_id=int(dossier_row["locked_topic_id"]),
                template_version=template_path.name,
                output_markdown=output,
            )
            created += 1

        summary = DissectionSummary(run_id=run_id, dissections_created=created)
        log_run_event(
            connection,
            run_id,
            "phase_8_dissection",
            "complete",
            f"Created {created} campaign dissection output(s).",
            json.dumps(summary.__dict__),
        )
        connection.commit()
        return summary


def build_dissection_markdown(dossier: dict[str, object], template_text: str) -> str:
    source_urls = list(dossier.get("source_urls", []))
    references = "\n".join(f"- {url}" for url in source_urls) or "- No source URLs available."
    platforms = ", ".join(dossier.get("media_platform_usage", [])) or "Not confirmed"
    roadmap_note = "Placeholder configurable roadmap. Replace templates/dissection_roadmap.md with the final framework."
    if template_text and "Placeholder" not in template_text:
        roadmap_note = "Configured roadmap loaded from templates/dissection_roadmap.md."

    return f"""# Campaign Dissection: {dossier.get("topic")}

## Roadmap Status
{roadmap_note}

## Brand
{dossier.get("brand_details")}

## Campaign Name
{dossier.get("campaign_name")}

## Agency Involved
{dossier.get("agency_involved")}

## Campaign Objective
{dossier.get("campaign_objective")}

## Strategic Insight
{dossier.get("strategic_insight")}

## Creative Idea
{dossier.get("creative_idea")}

## Execution
{dossier.get("execution")}

## Media / Platform Usage
{platforms}

## Audience
{dossier.get("audience")}

## Market Context
{dossier.get("market_context")}

## Performance Data
{dossier.get("performance_data")}

## References
{references}
"""


def dissect_campaign() -> None:
    raise NotImplementedError("Use run_campaign_dissections.")
