# Rebuild Android APK from source. Edit app code as usual, then run this script again.
# Output: build\apk\app-release.apk (or build\apk\<artifact>.apk)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

if (-not (Test-Path ".venv\Scripts\flet.exe")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
    .\.venv\Scripts\pip install -r requirements.txt
}

if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") {
    Write-Host "ERROR: flet build apk is not supported on Windows ARM64." -ForegroundColor Red
    Write-Host "Use an x64 Windows PC, or run the GitHub Actions workflow (.github/workflows/build-apk.yml)."
    exit 1
}

Write-Host "Building APK (first run may download Flutter and take several minutes)..."
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\flet build apk --yes --no-rich-output

$apkDir = Join-Path (Get-Location) "build\apk"
if (Test-Path $apkDir) {
    Write-Host ""
    Write-Host "Build complete. APK files:"
    Get-ChildItem $apkDir -Filter *.apk | ForEach-Object { Write-Host "  $($_.FullName)" }
}
