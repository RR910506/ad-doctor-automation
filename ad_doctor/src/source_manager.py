from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from urllib.parse import parse_qs, urlencode, urlparse

import pandas as pd

from src.db import get_connection, upsert_source
from src.config import DATABASE_PATH


REQUIRED_COLUMNS = ("source_name", "website_url")
OPTIONAL_COLUMNS = (
    "rss_url",
    "category",
    "country",
    "credibility_tier",
    "active_status",
)
SOURCE_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS

COLUMN_ALIASES = {
    "name": "source_name",
    "source": "source_name",
    "publication": "source_name",
    "publisher": "source_name",
    "url": "website_url",
    "link": "website_url",
    "website": "website_url",
    "website_link": "website_url",
    "source_url": "website_url",
    "feed_url": "rss_url",
    "rss": "rss_url",
    "tier": "credibility_tier",
    "credibility": "credibility_tier",
    "status": "active_status",
    "active": "active_status",
}

TRUE_VALUES = {"1", "active", "true", "yes", "y"}
FALSE_VALUES = {"0", "inactive", "false", "no", "n"}


@dataclass(frozen=True)
class SourceImportResult:
    total_rows: int
    valid_rows: int
    inserted: int
    updated: int
    skipped_rows: int
    warnings: list[str]
    sources: list[dict[str, object]]


def import_sources() -> None:
    raise NotImplementedError("Use import_sources_from_file, import_sources_from_google_sheet, or import_sources_from_dataframe.")


def set_source_active(source_id: int, active: bool, database_path: Path = DATABASE_PATH) -> bool:
    """Change one source status; called by an explicit, confirmed UI action."""
    with closing(get_connection(database_path)) as connection:
        changed = connection.execute(
            "UPDATE sources SET active_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (1 if active else 0, int(source_id)),
        ).rowcount
        connection.commit()
    return bool(changed)


def import_sources_from_file(
    file: str | Path | BinaryIO,
    filename: str | None = None,
    database_path: Path = DATABASE_PATH,
) -> SourceImportResult:
    dataframe = read_source_file(file, filename=filename)
    return import_sources_from_dataframe(dataframe, database_path=database_path)


def import_sources_from_google_sheet(
    sheet_url: str,
    database_path: Path = DATABASE_PATH,
) -> SourceImportResult:
    csv_url = google_sheet_url_to_csv_url(sheet_url)
    dataframe = pd.read_csv(csv_url)
    return import_sources_from_dataframe(dataframe, database_path=database_path)


def import_sources_from_dataframe(
    dataframe: pd.DataFrame,
    database_path: Path = DATABASE_PATH,
) -> SourceImportResult:
    normalized = normalize_sources_dataframe(dataframe)
    warnings = list(normalized.attrs.get("warnings", []))

    inserted = 0
    updated = 0
    source_records = normalized.to_dict(orient="records")

    with closing(get_connection(database_path)) as connection:
        for source in source_records:
            outcome = upsert_source(connection, source)
            if outcome == "inserted":
                inserted += 1
            else:
                updated += 1
        connection.commit()

    return SourceImportResult(
        total_rows=int(dataframe.shape[0]),
        valid_rows=len(source_records),
        inserted=inserted,
        updated=updated,
        skipped_rows=int(dataframe.shape[0]) - len(source_records),
        warnings=warnings,
        sources=source_records,
    )


def read_source_file(file: str | Path | BinaryIO, filename: str | None = None) -> pd.DataFrame:
    detected_name = filename or getattr(file, "name", "")
    suffix = Path(detected_name).suffix.lower()

    if isinstance(file, (str, Path)):
        suffix = Path(file).suffix.lower()

    if suffix == ".csv":
        return pd.read_csv(file)
    if suffix in {".xls", ".xlsx"}:
        return pd.read_excel(file)

    if hasattr(file, "getvalue"):
        raw = file.getvalue()
        return pd.read_excel(BytesIO(raw))

    raise ValueError("Unsupported source file type. Upload .xls, .xlsx, or .csv.")


def google_sheet_url_to_csv_url(sheet_url: str) -> str:
    parsed = urlparse(sheet_url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Enter a valid Google Sheet URL or CSV export URL.")

    query = parse_qs(parsed.query)
    fragment_query = parse_qs(parsed.fragment)
    if query.get("output") == ["csv"] or query.get("format") == ["csv"]:
        return sheet_url.strip()

    path_parts = [part for part in parsed.path.split("/") if part]
    if "spreadsheets" not in path_parts or "d" not in path_parts:
        return sheet_url.strip()

    try:
        sheet_id = path_parts[path_parts.index("d") + 1]
    except IndexError as exc:
        raise ValueError("Could not find the Google Sheet ID in the URL.") from exc

    gid = query.get("gid", fragment_query.get("gid", ["0"]))[0]
    export_query = urlencode({"format": "csv", "gid": gid})
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?{export_query}"


def normalize_sources_dataframe(dataframe: pd.DataFrame) -> pd.DataFrame:
    if dataframe.empty:
        raise ValueError("The source list is empty.")

    normalized = dataframe.copy()
    normalized.columns = [normalize_column_name(column) for column in normalized.columns]
    normalized = apply_column_aliases(normalized)

    missing_columns = [column for column in REQUIRED_COLUMNS if column not in normalized.columns]
    if missing_columns:
        missing = ", ".join(missing_columns)
        raise ValueError(f"Missing required source column(s): {missing}.")

    for column in OPTIONAL_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = None

    normalized = normalized.loc[:, list(SOURCE_COLUMNS)]
    normalized = normalized.where(pd.notna(normalized), None)
    normalized["source_name"] = normalized["source_name"].map(clean_text)
    normalized["website_url"] = normalized["website_url"].map(clean_url)
    normalized["rss_url"] = normalized["rss_url"].map(clean_optional_url)
    normalized["category"] = normalized["category"].map(clean_optional_text)
    normalized["country"] = normalized["country"].map(clean_optional_text)
    normalized["credibility_tier"] = normalized["credibility_tier"].map(normalize_credibility_tier)
    normalized["active_status"] = normalized["active_status"].map(normalize_active_status)

    row_count_before = len(normalized)
    normalized = normalized[
        normalized["source_name"].notna()
        & normalized["website_url"].notna()
        & normalized["website_url"].map(is_http_url)
    ]

    duplicate_count = int(normalized.duplicated(subset=["website_url"]).sum())
    normalized = normalized.drop_duplicates(subset=["website_url"], keep="last")

    warnings: list[str] = []
    skipped_count = row_count_before - len(normalized) - duplicate_count
    if skipped_count > 0:
        warnings.append(f"Skipped {skipped_count} row(s) with missing or invalid source name/website URL.")
    if duplicate_count > 0:
        warnings.append(f"Collapsed {duplicate_count} duplicate website URL row(s), keeping the last version.")

    normalized.attrs["warnings"] = warnings
    return normalized.reset_index(drop=True)


def normalize_column_name(column: object) -> str:
    cleaned = str(column).strip().lower()
    for character in (" ", "-", "."):
        cleaned = cleaned.replace(character, "_")
    return cleaned


def apply_column_aliases(dataframe: pd.DataFrame) -> pd.DataFrame:
    renamed_columns: dict[str, str] = {}
    existing_columns = set(dataframe.columns)
    for column in dataframe.columns:
        canonical_column = COLUMN_ALIASES.get(column)
        if canonical_column and canonical_column not in existing_columns:
            renamed_columns[column] = canonical_column
    return dataframe.rename(columns=renamed_columns)


def clean_text(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def clean_optional_text(value: object) -> str | None:
    return clean_text(value)


def clean_url(value: object) -> str | None:
    cleaned = clean_text(value)
    if not cleaned:
        return None
    return cleaned.rstrip("/")


def clean_optional_url(value: object) -> str | None:
    cleaned = clean_url(value)
    if not cleaned:
        return None
    return cleaned if is_http_url(cleaned) else None


def is_http_url(value: object) -> bool:
    parsed = urlparse(str(value))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def normalize_credibility_tier(value: object) -> int:
    if value is None or clean_text(value) is None:
        return 1
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return 1
    return max(1, int(numeric))


def normalize_active_status(value: object) -> int:
    if value is None or clean_text(value) is None:
        return 1
    numeric = pd.to_numeric(value, errors="coerce")
    if not pd.isna(numeric):
        return 0 if int(numeric) == 0 else 1
    cleaned = str(value).strip().lower()
    if cleaned in TRUE_VALUES:
        return 1
    if cleaned in FALSE_VALUES:
        return 0
    return 1
