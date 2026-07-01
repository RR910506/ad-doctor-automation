@echo off
setlocal
if not exist "%~dp0..\data\output" mkdir "%~dp0..\data\output"
echo [%date% %time%] Starting Ad Doctor local Streamlit keep-alive >> "%~dp0..\data\output\local-streamlit-task.log"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_streamlit.ps1" >> "%~dp0..\data\output\local-streamlit-task.log" 2>&1
echo [%date% %time%] Finished with exit code %ERRORLEVEL% >> "%~dp0..\data\output\local-streamlit-task.log"
exit /b %ERRORLEVEL%
