@echo off
setlocal
cd /d "%~dp0\.."
if not exist "data\output" mkdir "data\output"
".venv\Scripts\python.exe" -m src.daily_automation_runner >> "data\output\daily_automation.log" 2>&1
exit /b %ERRORLEVEL%
