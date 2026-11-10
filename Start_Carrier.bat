@echo off
setlocal
title Carrier AC Selector
cd /d "%~dp0"

echo ==========================================
echo       Carrier AC Selector - Launcher
echo ==========================================
echo.

where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo PowerShell was not found. Please enable Windows PowerShell and try again.
    pause
    exit /b 1
)

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "SETUP_MARKER=%~dp0.venv\.carrier_setup_complete"
if not exist "%VENV_PY%" goto setup
if not exist "%SETUP_MARKER%" goto setup

"%VENV_PY%" -c "import PySide6, pandas, openpyxl, pytesseract, PIL, docx, pymupdf" >nul 2>&1
if errorlevel 1 goto setup
goto run

:setup
echo First-time setup or incomplete installation detected...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
    echo.
    echo Setup failed. Please check the error message above.
    pause
    exit /b 1
)

:run
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
