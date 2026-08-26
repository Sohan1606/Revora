@echo off
title REVORA Backend
cd /d "%~dp0backend"

if not exist .venv (
  echo [one-time] Creating Python environment and installing packages...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)

rem One-command launcher: creates .env with a dev secret, sets up DB, seeds users, starts API
.venv\Scripts\python.exe -m app.infrastructure.scripts.dev_up

echo.
echo Backend stopped. Press any key to close this window.
pause >nul
