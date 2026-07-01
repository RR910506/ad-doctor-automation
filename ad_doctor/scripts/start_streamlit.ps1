param(
    [switch]$Foreground
)
$ErrorActionPreference = "Stop"

$projectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$port = 8501
$healthUrl = "http://127.0.0.1:$port/_stcore/health"
$outputDir = Join-Path $projectDir "data\output"
$outLog = Join-Path $outputDir "local-streamlit.out.log"
$errLog = Join-Path $outputDir "local-streamlit.err.log"
$launchLog = Join-Path $outputDir "local-streamlit.launch.log"
$pythonPath = Join-Path $projectDir ".venv\Scripts\python.exe"

function Test-AdDoctorHealth {
    param(
        [int]$Attempts = 1,
        [int]$DelaySeconds = 2
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200 -and $response.Content.Trim() -eq "ok") {
                return $true
            }
        } catch {
            if ($attempt -lt $Attempts) {
                Start-Sleep -Seconds $DelaySeconds
            }
        }
    }
    return $false
}

if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir | Out-Null
}

if (Test-AdDoctorHealth -Attempts 2) {
    "Ad Doctor Streamlit is healthy on port $port." | Out-File -FilePath $launchLog -Append -Encoding utf8
    exit 0
}

$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    "Unhealthy process detected on port $port. Restarting PID $($listener.OwningProcess)." | Out-File -FilePath $launchLog -Append -Encoding utf8
    Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

if (-not (Test-Path $pythonPath)) {
    $pythonPath = "python"
}

if (-not $env:AD_DOCTOR_APP_SCHEDULER_ENABLED) {
    $env:AD_DOCTOR_APP_SCHEDULER_ENABLED = "1"
}
if (-not $env:AD_DOCTOR_SCHEDULER_CATCHUP_HOURS) {
    $env:AD_DOCTOR_SCHEDULER_CATCHUP_HOURS = "24"
}

$pathValue = [Environment]::GetEnvironmentVariable("Path", "Process")
if (-not $pathValue) {
    $pathValue = [Environment]::GetEnvironmentVariable("PATH", "Process")
}
if ($pathValue) {
    [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
}

$arguments = @(
    "-m", "streamlit", "run", "app.py",
    "--server.address", "0.0.0.0",
    "--server.port", "$port",
    "--server.headless", "true",
    "--browser.gatherUsageStats", "false"
)

if ($Foreground) {
    Set-Location $projectDir
    "Starting Ad Doctor Streamlit in foreground on port $port." | Out-File -FilePath $launchLog -Append -Encoding utf8
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $pythonPath @arguments 1>> $outLog 2>> $errLog
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    exit $exitCode
}

$process = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList $arguments `
    -WorkingDirectory $projectDir `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru

"Started Ad Doctor Streamlit on port $port. PID: $($process.Id)" | Out-File -FilePath $launchLog -Append -Encoding utf8

for ($attempt = 1; $attempt -le 10; $attempt++) {
    Start-Sleep -Seconds 2
    if (Test-AdDoctorHealth) {
        "Health check passed: $healthUrl" | Out-File -FilePath $launchLog -Append -Encoding utf8
        exit 0
    }
    if ($process.HasExited) {
        "Streamlit exited before becoming healthy. Exit code: $($process.ExitCode)." | Out-File -FilePath $launchLog -Append -Encoding utf8
        exit $process.ExitCode
    }
}

"Streamlit did not become healthy within 20 seconds." | Out-File -FilePath $launchLog -Append -Encoding utf8
exit 1
