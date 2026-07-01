from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from typing import Any


SHEETS_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
)

DRAFT_HEADERS = [
    "external_id",
    "created_at",
    "run_id",
    "content_output_id",
    "locked_topic",
    "content_type",
    "approval_status",
    "doc_url",
    "draft_markdown",
]


@dataclass(frozen=True)
class GoogleWorkspaceConfig:
    spreadsheet_id: str
    worksheet_name: str
    folder_id: str | None


def get_draft_store_mode() -> str:
    return os.getenv("AD_DOCTOR_DRAFT_STORE", "sqlite").strip().lower() or "sqlite"


def is_google_draft_store_enabled() -> bool:
    return get_draft_store_mode() == "google"


def get_google_workspace_config() -> GoogleWorkspaceConfig | None:
    spreadsheet_id = os.getenv("GOOGLE_DRAFTS_SPREADSHEET_ID", "").strip()
    if not spreadsheet_id:
        return None
    return GoogleWorkspaceConfig(
        spreadsheet_id=spreadsheet_id,
        worksheet_name=os.getenv("GOOGLE_DRAFTS_WORKSHEET_NAME", "Drafts").strip() or "Drafts",
        folder_id=os.getenv("GOOGLE_DRAFTS_FOLDER_ID", "").strip() or None,
    )


def sync_content_outputs_to_google_workspace(outputs: list[dict[str, Any]]) -> int:
    if not outputs or not is_google_draft_store_enabled():
        return 0
    config = get_google_workspace_config()
    if config is None:
        return 0

    sheets_service, docs_service, drive_service = build_google_services()
    ensure_drafts_sheet(sheets_service, config)
    existing_ids = get_existing_external_ids(sheets_service, config)
    rows = []
    for output in outputs:
        external_id = build_external_id(output)
        if external_id in existing_ids:
            continue
        doc_url = create_google_doc_for_draft(docs_service, drive_service, config, output)
        rows.append(
            [
                external_id,
                str(output.get("created_at") or datetime.now(UTC).isoformat()),
                str(output.get("run_id") or ""),
                str(output.get("id") or ""),
                str(output.get("locked_topic") or "Untitled topic"),
                str(output.get("content_type") or ""),
                str(output.get("approval_status") or "pending"),
                doc_url,
                str(output.get("draft_markdown") or ""),
            ]
        )
    if not rows:
        return 0
    sheets_service.spreadsheets().values().append(
        spreadsheetId=config.spreadsheet_id,
        range=f"{quote_sheet_name(config.worksheet_name)}!A:I",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
    return len(rows)


def list_google_workspace_drafts() -> list[dict[str, Any]]:
    if not is_google_draft_store_enabled():
        return []
    config = get_google_workspace_config()
    if config is None:
        return []
    sheets_service, _docs_service, _drive_service = build_google_services()
    ensure_drafts_sheet(sheets_service, config)
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=config.spreadsheet_id,
        range=f"{quote_sheet_name(config.worksheet_name)}!A:I",
    ).execute()
    values = result.get("values", [])
    if len(values) <= 1:
        return []
    headers = values[0]
    drafts = []
    for index, row in enumerate(values[1:], start=2):
        padded = row + [""] * max(0, len(headers) - len(row))
        item = dict(zip(headers, padded))
        drafts.append(
            {
                "id": item.get("external_id") or f"google-row-{index}",
                "locked_topic_id": item.get("external_id") or f"google-row-{index}",
                "locked_topic": item.get("locked_topic") or "Untitled topic",
                "run_id": item.get("run_id") or "",
                "content_type": item.get("content_type") or "social",
                "template_version": "google-docs",
                "draft_markdown": item.get("draft_markdown") or "",
                "approval_status": item.get("approval_status") or "pending",
                "created_at": item.get("created_at") or "",
                "updated_at": item.get("created_at") or "",
                "doc_url": item.get("doc_url") or "",
                "storage": "google",
            }
        )
    return sorted(drafts, key=lambda draft: str(draft.get("created_at") or ""), reverse=True)


def build_google_services():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Google draft storage requires google-api-python-client and google-auth."
        ) from exc

    credentials_info = load_service_account_info()
    credentials = service_account.Credentials.from_service_account_info(
        credentials_info,
        scopes=list(SHEETS_SCOPES),
    )
    sheets_service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    docs_service = build("docs", "v1", credentials=credentials, cache_discovery=False)
    drive_service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    return sheets_service, docs_service, drive_service


def load_service_account_info() -> dict[str, Any]:
    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw_json:
        return json.loads(raw_json)
    json_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if json_path:
        return json.loads(Path(json_path).read_text(encoding="utf-8"))
    raise RuntimeError(
        "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE for Google draft storage."
    )


def ensure_drafts_sheet(sheets_service, config: GoogleWorkspaceConfig) -> None:
    spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=config.spreadsheet_id).execute()
    sheets = spreadsheet.get("sheets", [])
    existing = {
        sheet.get("properties", {}).get("title")
        for sheet in sheets
    }
    if config.worksheet_name not in existing:
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=config.spreadsheet_id,
            body={
                "requests": [
                    {"addSheet": {"properties": {"title": config.worksheet_name}}}
                ]
            },
        ).execute()
    header_result = sheets_service.spreadsheets().values().get(
        spreadsheetId=config.spreadsheet_id,
        range=f"{quote_sheet_name(config.worksheet_name)}!A1:I1",
    ).execute()
    if header_result.get("values"):
        return
    sheets_service.spreadsheets().values().update(
        spreadsheetId=config.spreadsheet_id,
        range=f"{quote_sheet_name(config.worksheet_name)}!A1:I1",
        valueInputOption="RAW",
        body={"values": [DRAFT_HEADERS]},
    ).execute()


def get_existing_external_ids(sheets_service, config: GoogleWorkspaceConfig) -> set[str]:
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=config.spreadsheet_id,
        range=f"{quote_sheet_name(config.worksheet_name)}!A2:A",
    ).execute()
    return {str(row[0]) for row in result.get("values", []) if row}


def create_google_doc_for_draft(docs_service, drive_service, config: GoogleWorkspaceConfig, output: dict[str, Any]) -> str:
    title = build_doc_title(output)
    document = docs_service.documents().create(body={"title": title}).execute()
    document_id = document["documentId"]
    draft_markdown = str(output.get("draft_markdown") or "")
    docs_service.documents().batchUpdate(
        documentId=document_id,
        body={
            "requests": [
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": draft_markdown,
                    }
                }
            ]
        },
    ).execute()
    if config.folder_id:
        drive_service.files().update(
            fileId=document_id,
            addParents=config.folder_id,
            fields="id, parents",
        ).execute()
    return f"https://docs.google.com/document/d/{document_id}/edit"


def build_external_id(output: dict[str, Any]) -> str:
    return f"{output.get('run_id') or 'run'}:{output.get('id') or ''}:{output.get('content_type') or ''}"


def build_doc_title(output: dict[str, Any]) -> str:
    topic = str(output.get("locked_topic") or "Untitled topic").strip()
    content_type = str(output.get("content_type") or "draft").title()
    created_at = str(output.get("created_at") or "")[:19]
    return f"Ad Doctor {content_type} Draft - {topic[:80]} - {created_at}".strip(" -")


def quote_sheet_name(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"
