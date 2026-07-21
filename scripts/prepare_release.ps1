# Pre-release checks before pushing to GitHub and building the tablet APK.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "Picking Barcode Scanner - release prep" -ForegroundColor Cyan
Write-Host ""

$required = @(
    "pyproject.toml",
    "main.py",
    "requirements.txt",
    ".github/workflows/build-apk.yml",
    "assets/icon.png",
    "assets/deks_logo.png",
    "app/main_app.py",
    "app/barcode_catalog.py",
    "app/item_grouping.py",
    "app/history_export.py",
    "data/admins.json.example"
)

$missing = @()
foreach ($path in $required) {
    if (-not (Test-Path $path)) {
        $missing += $path
        Write-Host "  MISSING  $path" -ForegroundColor Red
    } else {
        Write-Host "  OK       $path" -ForegroundColor Green
    }
}

Write-Host ""
if ($missing.Count -gt 0) {
    Write-Host "Fix missing files before release." -ForegroundColor Red
    exit 1
}

$versionLine = Select-String -Path "pyproject.toml" -Pattern 'build_version = "' | Select-Object -First 1
$buildLine = Select-String -Path "pyproject.toml" -Pattern 'build_number = ' | Select-Object -First 1
$version = if ($versionLine) { $versionLine.Line.Split('"')[1] } else { "?" }
$build = if ($buildLine) { ($buildLine.Line -replace '\D', '').Trim() } else { "?" }

Write-Host "Release version: v$version build $build" -ForegroundColor Cyan
Write-Host ""
Write-Host "Do NOT push to GitHub:" -ForegroundColor Yellow
Write-Host "  - .venv"
Write-Host "  - build"
Write-Host "  - data/scanner.db"
Write-Host "  - data/admins.json"
Write-Host "  - data/config.json"
Write-Host "  - *.jks"
Write-Host ""
Write-Host "GitHub Actions secrets required:" -ForegroundColor Yellow
Write-Host "  ANDROID_KEYSTORE_BASE64"
Write-Host "  ANDROID_KEYSTORE_PASSWORD"
Write-Host "  ANDROID_KEY_ALIAS"
Write-Host "  ANDROID_KEY_PASSWORD"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. git add, commit, push"
Write-Host "  2. GitHub Actions - Build Android APK - Run workflow"
Write-Host "  3. Download picker-check-apk artifact"
Write-Host "  4. Install on tablet over existing app"
Write-Host ""

try {
    $env:PYTHONPATH = (Get-Location).Path
    & .\.venv\Scripts\python.exe scripts\check_release_version.py
} catch {
    Write-Host "Could not verify Python imports." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Release prep complete." -ForegroundColor Green
