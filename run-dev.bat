@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found.
    pause
    exit /b 1
)

call "%~dp0stop.bat" >nul 2>&1

echo Starting Picking Barcode Scanner ^(debug mode^)...
".venv\Scripts\python.exe" "%~dp0main.py"
if errorlevel 1 (
    echo.
    echo App exited with an error.
    pause
)
