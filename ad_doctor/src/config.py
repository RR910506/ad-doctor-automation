from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


if load_dotenv:
    load_dotenv()

APP_NAME = "Ad Doctor"
BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
DATABASE_DIR = BASE_DIR / "database"
_database_path = Path(
    os.getenv("AD_DOCTOR_DATABASE_PATH", str(DATABASE_DIR / "ad_doctor.db"))
).expanduser()
DATABASE_PATH = _database_path if _database_path.is_absolute() else BASE_DIR / _database_path
SCHEMA_PATH = DATABASE_DIR / "schema.sql"
TEMPLATE_DIR = BASE_DIR / "templates"

PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY", "").strip()
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar-pro").strip() or "sonar-pro"
PERPLEXITY_ENDPOINT = (
    os.getenv("PERPLEXITY_ENDPOINT", "https://api.perplexity.ai/v1/sonar").strip()
    or "https://api.perplexity.ai/v1/sonar"
)
