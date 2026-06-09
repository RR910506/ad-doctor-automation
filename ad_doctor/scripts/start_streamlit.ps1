$ErrorActionPreference = "Continue"

$projectDir = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $projectDir

$logPath = Join-Path $projectDir "streamlit.launch.log"
$pythonPath = Join-Path $projectDir ".venv\Scripts\python.exe"

& $pythonPath -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true *> $logPath
