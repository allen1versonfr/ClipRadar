@echo off
rem Speed audit, start to finish (docs/plans/efficiency-audit.md). Double-click it, or run
rem   scripts\speed_audit.bat /nopause [--quick] [other scripts\audit_speed.py options]
rem It sets up Python and the packages if needed, then scripts\audit_speed.py --auto does the rest:
rem FFmpeg and the laughter detector, picking videos from your Clip Radar projects, the measuring, and the
rem app's own log. Results: audit_runs\<date>\SUMMARY.md. Nothing in your projects or settings is changed.
setlocal EnableExtensions
cd /d "%~dp0.."
title Clip Radar speed audit

set "NOPAUSE="
set "ARGS="
:args
if "%~1"=="" goto argsdone
if /i "%~1"=="/nopause" (
  set "NOPAUSE=1"
) else (
  set ARGS=%ARGS% %1
)
shift
goto args
:argsdone

echo.
echo   Clip Radar speed audit
echo   ----------------------
echo.

rem ---------- Python 3.10 to 3.13 (the caption library has no 3.14 build yet) ----------
set "PY="
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,13) else 1)" >nul 2>&1 || rmdir /s /q .venv
)
if exist ".venv\Scripts\python.exe" goto :venv
for %%V in (3.12 3.11 3.13 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,13) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo   Installing Python 3.12 with winget...
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-source-agreements --accept-package-agreements
  rem this window's PATH doesn't have it yet: use it where winget puts it
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
)
if not defined PY (
  echo   Couldn't find or install Python 3.12. Install it from https://www.python.org/downloads/ and run this again.
  goto :fail
)
echo   Making the Python environment (.venv)...
%PY% -m venv .venv
if errorlevel 1 ( echo   Couldn't create the Python environment. & goto :fail )

:venv
set "VPY=.venv\Scripts\python.exe"
echo   Installing what the audit needs (the first time takes a few minutes)...
"%VPY%" -m pip install --upgrade pip --disable-pip-version-check -q
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt psutil py-spy onnxruntime
if errorlevel 1 ( echo   Package install failed. Check the internet connection and run this again. & goto :fail )

set PYTHONUTF8=1
"%VPY%" scripts\audit_speed.py --auto %ARGS%
if errorlevel 1 goto :fail
if defined NOPAUSE exit /b 0
rem open the newest results folder (outside a ( ) block, where %LAST% would be read before it's set)
set "LAST="
for /d %%D in (audit_runs\*) do set "LAST=%%D"
if defined LAST start "" "%CD%\%LAST%"
echo.
pause
exit /b 0

:fail
echo.
echo   The speed audit stopped. The message above says why.
if not defined NOPAUSE pause
exit /b 1
