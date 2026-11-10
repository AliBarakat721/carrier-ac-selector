@echo off
setlocal
title Carrier AC Selector
cd /d "%~dp0"

echo ==========================================
echo       Carrier AC Selector - Launcher
echo ==========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo First-time setup: installing required components...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
    if errorlevel 1 (
        echo.
        echo Setup failed. Please check the error message above.
        pause
        exit /b 1
    )
) else (
    echo Existing installation found. Skipping dependency installation.
)

echo.
echo Starting Carrier AC Selector...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
if errorlevel 1 (
    echo.
    echo The application could not be started. Please check the error message above.
    pause
    exit /b 1
)

endlocal
