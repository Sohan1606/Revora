@echo off
title REVORA Console
cd /d "%~dp0frontend"

if not exist node_modules (
  echo [one-time] Installing frontend packages...
  call npm install
)

echo Starting REVORA console... it will open at http://localhost:5173
call npm run dev

echo.
echo Frontend stopped. Press any key to close this window.
pause >nul
