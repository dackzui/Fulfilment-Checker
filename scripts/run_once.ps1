# Start exactly one desktop instance of Picking Barcode Scanner.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$main = Join-Path $root "main.py"

if (-not (Test-Path $pythonw)) {
    Write-Host "Virtual environment not found. Run: python -m venv .venv" -ForegroundColor Red
    exit 1
}

& (Join-Path $PSScriptRoot "stop_app.ps1") | Out-Null
Start-Sleep -Seconds 1

$running = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -match 'picker-check[\\/]main\.py'
}
if ($running) {
    Write-Host "App is already running."
    exit 0
}

Start-Process -FilePath $pythonw -ArgumentList "`"$main`"" -WorkingDirectory $root
Write-Host "Picking Barcode Scanner started."
