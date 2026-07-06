import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.db import initialize_database
from src.openai_content_writer import (
    is_openai_writer_enabled,
    write_final_draft_with_chatgpt,
)
from src.settings_engine import load_content_writer_settings, save_content_writer_settings


class OpenAIContentWriterTest(unittest.TestCase):
    def test_missing_key_falls_back_to_local_framework_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
                result = write_final_draft_with_chatgpt(
                    content_type="social",
                    local_draft="Local carousel draft",
                    dossier={"topic": "Test topic"},
                    dissection_markdown="Dissection",
                    framework_settings={"framework_name": "Ad Doctor"},
                    database_path=database_path,
                )

        self.assertEqual(result.status, "unavailable")
        self.assertIn("Local carousel draft", result.draft_markdown)
        self.assertIn("Content Writing Mode: Local framework generator", result.draft_markdown)

    def test_content_writer_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )
            saved = save_content_writer_settings(
                provider_key="openai",
                enabled=True,
                api_key="test-key",
                model="gpt-test",
                endpoint="https://api.openai.com/v1",
                database_path=database_path,
            )
            loaded = load_content_writer_settings(database_path=database_path)

        self.assertEqual(saved["model"], "gpt-test")
        self.assertEqual(loaded["api_key"], "test-key")
        self.assertEqual(loaded["enabled"], "1")

    def test_writer_enabled_uses_saved_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "ad_doctor.db"
            initialize_database(
                database_path=database_path,
                schema_path=PROJECT_DIR / "database" / "schema.sql",
            )
            save_content_writer_settings(
                provider_key="openai",
                enabled=True,
                api_key="test-key",
                model="gpt-test",
                endpoint="https://api.openai.com/v1",
                database_path=database_path,
            )

            enabled = is_openai_writer_enabled(database_path=database_path)

        self.assertTrue(enabled)


if __name__ == "__main__":
    unittest.main()
