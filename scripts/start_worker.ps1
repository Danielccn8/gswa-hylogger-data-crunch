# Start the Explorer job worker on the Windows host (same pinned versions as the images).
# Usage: powershell -ExecutionPolicy Bypass -File scripts/start_worker.ps1 [-DataDir C:\HyLoggerData] [-Legacy <old data dir>]
param(
    [string]$DataDir = "C:\HyLoggerData",
    [string]$Legacy = "",
    [string]$Lanes = "processed:4,raw:2,analysis:1"
)
$root = Split-Path -Parent $PSScriptRoot
$env:HYLOGGER_DATA_DIR = $DataDir
if ($Legacy) { $env:HYLOGGER_LEGACY_DIR = $Legacy }
if (-not $env:DATABASE_URL) { $env:DATABASE_URL = "postgresql://hylogger:hylogger@127.0.0.1:5434/hylogger" }
$env:OMP_NUM_THREADS = "4"; $env:OPENBLAS_NUM_THREADS = "4"
New-Item -ItemType Directory -Force (Join-Path $DataDir "logs") | Out-Null
& (Join-Path $root ".venv\Scripts\python.exe") -m hylogger.jobs.worker --lanes $Lanes
