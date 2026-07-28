# Build (and optionally install) the DEKS Top Pickers Monitor desktop app.
# Uses Flet pack / PyInstaller — works on Windows x64 and ARM64.
#
# Usage:
#   .\scripts\build_monitor.ps1              # build + install to LocalAppData
#   .\scripts\build_monitor.ps1 -BuildOnly   # build only (no shortcut install)
#   .\scripts\build_monitor.ps1 -InstallOnly # install last build + shortcuts
#
# Output folder: dist\monitor\DEKSTopPickersMonitor\
# Installed to:  %LOCALAPPDATA%\Programs\DEKSTopPickersMonitor\

param(
    [switch]$BuildOnly,
    [switch]$InstallOnly
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

$appName = "DEKSTopPickersMonitor"
$productName = "DEKS Top Pickers Monitor"
$company = "DEKS"
$distRoot = Join-Path $root "dist\monitor"
$builtDir = Join-Path $distRoot $appName
$installDir = Join-Path $env:LOCALAPPDATA "Programs\$appName"
$exeName = "$appName.exe"

function Get-AppVersion {
    $pyproject = Join-Path $root "pyproject.toml"
    if (Test-Path $pyproject) {
        $text = Get-Content $pyproject -Raw
        if ($text -match 'build_version\s*=\s*"([^"]+)"') {
            return $Matches[1]
        }
        if ($text -match 'version\s*=\s*"([^"]+)"') {
            return $Matches[1]
        }
    }
    return "1.0.0"
}

function Ensure-Venv {
    $python = Join-Path $root ".venv\Scripts\python.exe"
    $flet = Join-Path $root ".venv\Scripts\flet.exe"
    $pip = Join-Path $root ".venv\Scripts\pip.exe"

    if (-not (Test-Path $python)) {
        Write-Host "Creating virtual environment..." -ForegroundColor Cyan
        python -m venv .venv
        if (-not (Test-Path $python)) {
            throw "Could not create .venv. Install Python 3.10+ and retry."
        }
    }

    if (-not (Test-Path $flet)) {
        Write-Host "Installing dependencies..." -ForegroundColor Cyan
        & $pip install -r (Join-Path $root "requirements.txt")
        & $pip install "pymupdf>=1.24.0"
    }

    Write-Host "Ensuring PyInstaller is available..." -ForegroundColor Cyan
    & $pip install -q "pyinstaller>=6.0" "pillow>=10.0"
    return @{ Python = $python; Flet = $flet; Pip = $pip }
}

function Ensure-Icon {
    $ico = Join-Path $root "assets\icon.ico"
    if (Test-Path $ico) { return $ico }

    $png = Join-Path $root "assets\icon.png"
    if (-not (Test-Path $png)) { return $null }

    Write-Host "Creating assets\icon.ico from icon.png..." -ForegroundColor Cyan
    $py = Join-Path $root ".venv\Scripts\python.exe"
    $null = & $py -c @"
from pathlib import Path
from PIL import Image
src = Path(r'$png')
dst = Path(r'$ico')
img = Image.open(src).convert('RGBA')
img.save(dst, format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
"@
    if (Test-Path $ico) { return $ico }
    return $null
}

function Build-Monitor {
    $tools = Ensure-Venv
    $version = Get-AppVersion
    $fileVersion = if ($version -match '^\d+\.\d+\.\d+$') { "$version.0" } else { "1.0.0.0" }
    $icon = Ensure-Icon
    if ($icon -is [Array]) { $icon = $icon | Select-Object -Last 1 }

    New-Item -ItemType Directory -Force -Path $distRoot | Out-Null

    Write-Host ""
    Write-Host "Building $productName v$version ..." -ForegroundColor Cyan
    Write-Host "This may take a few minutes the first time." -ForegroundColor DarkGray

    # Use relative paths so PowerShell splatting stays clean.
    $packArgs = @(
        "pack", "monitor_main.py",
        "-n", $appName,
        "-D",
        "--distpath", "dist\monitor",
        "--product-name", $productName,
        "--file-description", $productName,
        "--product-version", $version,
        "--file-version", $fileVersion,
        "--company-name", $company,
        "--copyright", "Copyright (C) 2026 DEKS Industries Pty Ltd",
        "-y",
        "--add-data", "assets;assets",
        "--add-data", "data/deks_logo.png;data",
        "--add-data", "data/BarcodeMasterList.xlsx;data",
        "--hidden-import", "app",
        "--hidden-import", "app.monitor_app",
        "--hidden-import", "app.auth",
        "--hidden-import", "app.database",
        "--hidden-import", "app.firebase_presence",
        "--hidden-import", "app.paths",
        "--hidden-import", "app.theme",
        "--hidden-import", "app.components"
    )
    if ($icon -and (Test-Path $icon)) {
        $packArgs += @("-i", "assets\icon.ico")
    }

    $env:PYTHONIOENCODING = "utf-8"
    & $tools.Flet @packArgs
    if ($LASTEXITCODE -ne 0) {
        throw "flet pack failed with exit code $LASTEXITCODE"
    }

    $exe = Join-Path $builtDir $exeName
    if (-not (Test-Path $exe)) {
        throw "Build finished but EXE not found: $exe"
    }

    # Writable data folder beside the EXE (admins, firebase config, session, DB).
    $dataBeside = Join-Path $builtDir "data"
    New-Item -ItemType Directory -Force -Path $dataBeside | Out-Null
    foreach ($seed in @("deks_logo.png", "BarcodeMasterList.xlsx", "config.json.example", "firebase_config.json.example", "admins.json.example")) {
        $src = Join-Path $root "data\$seed"
        if (Test-Path $src) {
            Copy-Item $src (Join-Path $dataBeside (Split-Path $seed -Leaf)) -Force
        }
    }

    # Convenience launcher next to the build folder.
    $launcher = Join-Path $distRoot "Run Monitor.bat"
    @"
@echo off
cd /d "%~dp0$appName"
start "" "%~dp0$appName\$exeName"
"@ | Set-Content -Path $launcher -Encoding ASCII

    Write-Host ""
    Write-Host "Build complete:" -ForegroundColor Green
    Write-Host "  $exe"
    Write-Host "  $launcher"
}

function New-Shortcut {
    param(
        [string]$ShortcutPath,
        [string]$TargetPath,
        [string]$WorkingDirectory,
        [string]$Description,
        [string]$IconLocation
    )
    $wsh = New-Object -ComObject WScript.Shell
    $sc = $wsh.CreateShortcut($ShortcutPath)
    $sc.TargetPath = $TargetPath
    $sc.WorkingDirectory = $WorkingDirectory
    $sc.Description = $Description
    if ($IconLocation) { $sc.IconLocation = $IconLocation }
    $sc.Save()
}

function Install-Monitor {
    if (-not (Test-Path (Join-Path $builtDir $exeName))) {
        throw "No build found at $builtDir. Run without -InstallOnly first."
    }

    Write-Host ""
    Write-Host "Installing to $installDir ..." -ForegroundColor Cyan
    if (Test-Path $installDir) {
        Remove-Item $installDir -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $installDir | Out-Null
    Copy-Item -Path (Join-Path $builtDir "*") -Destination $installDir -Recurse -Force

    # Keep existing local config/DB if present from a previous install.
    $userData = Join-Path $installDir "data"
    New-Item -ItemType Directory -Force -Path $userData | Out-Null

    $exePath = Join-Path $installDir $exeName
    $desktop = [Environment]::GetFolderPath("Desktop")
    $startMenu = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"
    New-Item -ItemType Directory -Force -Path $startMenu | Out-Null

    $desktopLnk = Join-Path $desktop "$productName.lnk"
    $startLnk = Join-Path $startMenu "$productName.lnk"
    New-Shortcut -ShortcutPath $desktopLnk -TargetPath $exePath -WorkingDirectory $installDir -Description $productName -IconLocation $exePath
    New-Shortcut -ShortcutPath $startLnk -TargetPath $exePath -WorkingDirectory $installDir -Description $productName -IconLocation $exePath

    $uninstall = Join-Path $installDir "Uninstall Monitor.bat"
    @"
@echo off
echo Removing $productName...
del /q "%USERPROFILE%\Desktop\$productName.lnk" 2>nul
del /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\$productName.lnk" 2>nul
cd /d "%LOCALAPPDATA%\Programs"
rmdir /s /q "$appName"
echo Done.
pause
"@ | Set-Content -Path $uninstall -Encoding ASCII

    Write-Host "Installed." -ForegroundColor Green
    Write-Host "  App:      $exePath"
    Write-Host "  Desktop:  $desktopLnk"
    Write-Host "  Start:    $startLnk"
    Write-Host ""
    Write-Host "Sign in as Super Admin or Monitor Viewer." -ForegroundColor DarkGray
    Write-Host "Copy data\firebase_config.json into the install data folder if Who's online is used." -ForegroundColor DarkGray
}

if ($InstallOnly) {
    Install-Monitor
} else {
    Build-Monitor
    if (-not $BuildOnly) {
        Install-Monitor
    }
}

Write-Host ""
Write-Host "Next time, just run:  .\build-monitor.bat" -ForegroundColor Cyan
