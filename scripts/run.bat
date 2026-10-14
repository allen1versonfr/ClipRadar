@echo off
cd /d "%~dp0.."
title Clip Radar
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   Clip Radar isn't set up yet. Double-click install.bat first.
  echo.
  pause
  exit /b 1
)
set PYTHONUTF8=1
".venv\Scripts\python.exe" src\server.py
if errorlevel 1 (
  echo.
  echo   Clip Radar stopped with an error. The message above says why.
  pause
)
