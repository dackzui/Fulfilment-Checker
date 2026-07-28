@echo off
cd /d "%~dp0"
echo Building DEKS Top Pickers Monitor desktop app...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_monitor.ps1" %*
if errorlevel 1 (
  echo.
  echo Build failed.
  pause
  exit /b 1
)
echo.
pause
