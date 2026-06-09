import unittest
from contextlib import closing
from pathlib import Path
import sys
import tempfile


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

try:
    import pandas as pd

    from src.db import initialize_database, get_connection, list_sources
    from src.source_manager import (
        google_sheet_url_to_csv_url,
        import_sources_from_dataframe,
        normalize_sources_dataframe,
    )

    IMPORT_ERROR = None
except ModuleNotFoundError as exc:
    pd = None
    IMPORT_ERROR = exc


@unittest.skipIf(IMPORT_ERROR is not None, "Install requirements.txt to run source import tests.")
class SourceImportTest(unittest.TestCase):
    def test_google_sheet_url_is_converted_to_csv_export(self) -> None:
        url = "https://docs.google.com/spreadsheets/d/sheet123/edit#gid=987"

        csv_url = google_sheet_url_to_csv_url(url)

        self.assertEqual(
            csv_url,
            "https://docs.google.com/spreadsheets/d/sheet123/export?format=csv&gid=987",
        )

    def test_normalize_sources_dataframe_cleans_and_deduplicates_rows(self) -> None:
        dataframe = pd.DataFrame(
            [
                {
                    "Source Name": "Ad News Daily",
                    "Website URL": "https://example.com/ad-news/",
                    "RSS URL": "https://example.com/ad-news/rss",
                    "Category": "Advertising",
                    "Country": "US",
                    "Credibility Tier": 3,
                    "Active Status": "active",
                },
                {
                    "Source Name": "Invalid Source",
                    "Website URL": "not-a-url",
                },
                {
                    "Source Name": "Campaign Watch",
                    "Website URL": "https://example.com/campaign-watch",
                    "Active Status": "inactive",
                },
                {
                    "Source Name": "Campaign Watch Updated",
                    "Website URL": "https://example.com/campaign-watch",
                    "Active Status": "yes",
                },
            ]
        )

        normalized = normalize_sources_dataframe(dataframe)

        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized.loc[0, "website_url"], "https://example.com/ad-news")
        self.assertEqual(normalized.loc[1, "source_name"], "Campaign Watch Updated")
        self.assertEqual(normalized.loc[1, "active_status"], 1)
        self.assertEqual(len(normalized.attrs["warnings"]), 2)

    def test_normalize_sources_dataframe_accepts_url_alias(self) -> None:
        dataframe = pd.DataFrame(
            [
                {
                    "url": "https://example.com/ad-age",
                    "source_name": "Ad Age",
                }
            ]
        )

        normalized = normalize_sources_dataframe(dataframe)

        self.assertEqual(normalized.loc[0, "website_url"], "https://example.com/ad-age")

    def test_import_sources_from_dataframe_upserts_into_sqlite(self) -> None:
        dataframe = pd.DataFrame(
            [
                {
                    "source_name": "Ad News Daily",
                    "website_url": "https://example.com/ad-news",
                    "rss_url": "https://example.com/ad-news/rss",
                    "category": "Advertising",
                    "country": "US",
                    "credibility_tier": 3,
                    "active_status": "active",
                },
                {
                    "source_name": "Campaign Watch",
                    "website_url": "https://example.com/campaign-watch",
                    "credibility_tier": 2,
                    "active_status": "inactive",
                },
            ]
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )

            first_result = import_sources_from_dataframe(dataframe, database_path=database_path)
            second_result = import_sources_from_dataframe(dataframe, database_path=database_path)

            with closing(get_connection(database_path)) as connection:
                sources = list_sources(connection)

        self.assertEqual(first_result.inserted, 2)
        self.assertEqual(first_result.updated, 0)
        self.assertEqual(second_result.inserted, 0)
        self.assertEqual(second_result.updated, 2)
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]["source_name"], "Ad News Daily")
