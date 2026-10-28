#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r ad_doctor/requirements.txt
export AD_DOCTOR_DATABASE_PATH="$(pwd)/.venv/development.db"
export AD_DOCTOR_DRAFT_STORE=sqlite
export AD_DOCTOR_APP_SCHEDULER_ENABLED=0
export AD_DOCTOR_SCHEDULER_MODE=external
export AD_DOCTOR_CLEAR_SQLITE_GENERATED_DATA_FOR_GOOGLE=0
cd ad_doctor
../.venv/bin/python -c 'from src.db import initialize_database; initialize_database()'
exec ../.venv/bin/python -m streamlit run app.py --server.address=127.0.0.1 --server.port=8501 --browser.gatherUsageStats=false
