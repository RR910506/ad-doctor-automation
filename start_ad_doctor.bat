@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 goto failed
)
.venv\Scripts\python.exe -m pip install -r ad_doctor\requirements.txt
if errorlevel 1 goto failed
set "AD_DOCTOR_DATABASE_PATH=%CD%\.venv\development.db"
set "AD_DOCTOR_DRAFT_STORE=sqlite"
set "AD_DOCTOR_APP_SCHEDULER_ENABLED=0"
set "AD_DOCTOR_SCHEDULER_MODE=external"
set "AD_DOCTOR_CLEAR_SQLITE_GENERATED_DATA_FOR_GOOGLE=0"
cd ad_doctor
..\.venv\Scripts\python.exe -c "from src.db import initialize_database; initialize_database()"
if errorlevel 1 goto failed
..\.venv\Scripts\python.exe -m streamlit run app.py --server.address=127.0.0.1 --server.port=8501 --browser.gatherUsageStats=false
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Check the message above. Python 3.12 with pip is required.
pause
exit /b 1
