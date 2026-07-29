@echo off
cd /d "%~dp0"

REM Always use this project's data folder (Firebase, admins, barcode list).
set "PICKER_CHECK_DATA=%~dp0data"
set "PICKER_CHECK_SOURCE_DATA=%~dp0data"

set "EXE=%~dp0dist\monitor\DEKSTopPickersMonitor\DEKSTopPickersMonitor.exe"

if exist "%EXE%" (
  start "" "%EXE%"
  exit /b 0
)

REM No packaged build yet - run from the Python virtualenv.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_monitor.ps1"
