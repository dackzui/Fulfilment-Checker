@echo off
cd /d "%~dp0"
REM Use the same data folder as run-monitor.bat (project\data) so Firebase works.
set "PICKER_CHECK_DATA=%~dp0data"
set "PICKER_CHECK_SOURCE_DATA=%~dp0data"

set "EXE=%~dp0dist\monitor\DEKSTopPickersMonitor\DEKSTopPickersMonitor.exe"
if not exist "%EXE%" (
  echo.
  echo Monitor not built yet, or dist folder is empty.
  echo.
  echo 1. Run:  build-monitor.bat
  echo 2. Then run this file again.
  echo.
  echo Do NOT open:  build\DEKSTopPickersMonitor\...
  echo That folder is incomplete and will show a Python DLL error.
  echo.
  pause
  exit /b 1
)

if not exist "%PICKER_CHECK_DATA%\firebase_config.json" (
  echo.
  echo WARNING: data\firebase_config.json not found.
  echo Firebase Who's online / rankings will be empty until you add it.
  echo ^(Same file used by run-monitor.bat^)
  echo.
)

start "" "%EXE%"
