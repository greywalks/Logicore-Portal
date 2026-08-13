@echo off
title Promethean Invoice Generator v9.0

REM ── Check Python ─────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install from https://www.python.org/downloads/
    pause & exit /b 1
)

REM ── Kill anything already on port 5000 ───────────────────────────────────────
echo Stopping any previous instance on port 5000...
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr /R ":5000 "') do (
    taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

REM ── Wipe ALL Python cache in this folder ──────────────────────────────────────
echo Clearing cache...
for /d /r "%~dp0" %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d" >nul 2>&1
)
del /s /q "%~dp0*.pyc" >nul 2>&1

REM ── Install / update dependencies ─────────────────────────────────────────────
echo Checking dependencies...
pip install -r "%~dp0requirements.txt" --quiet --disable-pip-version-check

REM ── Show version and folder ────────────────────────────────────────────────────
echo.
echo ============================================================
echo   Running: Promethean Invoice Generator v9.0
echo   Modules: Workshop Invoice + Storage Invoice
echo   Folder:  %~dp0
echo ============================================================
echo.
echo Your browser will open automatically.
echo Press Ctrl+C in this window to stop the server.
echo.

REM ── Launch ────────────────────────────────────────────────────────────────────
python "%~dp0app.py"
pause
