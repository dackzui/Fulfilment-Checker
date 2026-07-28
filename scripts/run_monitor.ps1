# Start the Super Admin Top Pickers Monitor (desktop).
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$main = Join-Path $root "monitor_main.py"

if (-not (Test-Path $python)) {
    Write-Host "Virtual environment not found. Run: python -m venv .venv" -ForegroundColor Red
    exit 1
}

$runner = if (Test-Path $pythonw) { $pythonw } else { $python }
Start-Process -FilePath $runner -ArgumentList "`"$main`"" -WorkingDirectory $root
Write-Host "Top Pickers Monitor started. Sign in as Super Admin."
