@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
title Build ClipRadar.exe
echo.
echo   Building ClipRadar.exe
echo   -----------------------
echo   This takes 5 to 10 minutes the first time. You only need to do it once.
echo.

set "MODE=folder"
if /i "%~1"=="onefile" set "MODE=onefile"

rem ---------- Python 3.10 to 3.13 ----------
set "PY="
for %%V in (3.12 3.11 3.13 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,13) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo   Python 3.12 is needed to build the exe. Installing it with winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
  if errorlevel 1 (
    echo   Couldn't install Python. Get Python 3.12 from https://www.python.org/downloads/
  ) else (
    echo   Python installed. Close this window and double-click build_exe.bat again.
  )
  pause
  exit /b 1
)

rem ---------- build environment ----------
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>&1 || rmdir /s /q .venv
)
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
  if errorlevel 1 ( echo   Couldn't create the Python environment. & pause & exit /b 1 )
)
set "VPY=.venv\Scripts\python.exe"
echo   [1/4] Installing build packages...
"%VPY%" -m pip install --upgrade pip --disable-pip-version-check -q
"%VPY%" -m pip install -U --disable-pip-version-check -q -r requirements.txt pywebview pyinstaller
if errorlevel 1 ( echo   Package install failed. Check your internet connection and try again. & pause & exit /b 1 )

rem ---------- helper tools (shipped next to the exe) ----------
echo   [2/4] Getting ffmpeg, TwitchDownloader and yt-dlp...
set "NEED="
if not exist "tools\ffmpeg.exe" set "NEED=%NEED% ffmpeg"
if not exist "tools\ffprobe.exe" if not defined NEED set "NEED=ffmpeg"
if not exist "tools\TwitchDownloaderCLI.exe" set "NEED=%NEED% twitchdownloader"
set "NEED=%NEED% ytdlp"
"%VPY%" src\tools_setup.py --dest tools %NEED%
if not exist "tools\ffmpeg.exe" (
  echo   Note: ffmpeg didn't download. The app will offer to download it on first launch.
)

rem ---------- laughter detector (bundled into the exe) ----------
"%VPY%" src\audio_events.py --download src\models
if not exist "src\models\audio_events.onnx" (
  echo   Note: the laughter detector didn't download. The app can download it later from Settings.
  mkdir src\models 2>nul
)

rem ---------- React UI (web\ builds into src\web; skipped until web\ exists) ----------
set "WEBDATA="
if exist "web\package.json" (
  where npm >nul 2>nul || ( echo   The React UI needs Node.js 22 ^(nodejs.org^). & pause & exit /b 1 )
  pushd web
  call npm ci || ( popd & echo   npm ci failed. & pause & exit /b 1 )
  call npm run build || ( popd & echo   The React UI didn't build. & pause & exit /b 1 )
  popd
)
if exist "src\web\index.html" set "WEBDATA=--add-data src\web;web"

rem ---------- PyInstaller ----------
echo   [3/4] Packaging (this is the slow part)...
set "PKG=--onedir"
if "%MODE%"=="onefile" set "PKG=--onefile"
rem src\stubs\av stands in for PyAV (Clip Radar decodes audio with FFmpeg; see that file)
set "PYTHONPATH=src\stubs"
"%VPY%" -m PyInstaller --noconfirm --clean --log-level WARN %PKG% --windowed ^
  --name ClipRadar --icon "src\ui\icon.ico" --add-data "src\ui;ui" --add-data "src\models;models" %WEBDATA% ^
  --paths src --paths src\stubs --exclude-module hf_xet ^
  --collect-all faster_whisper --collect-all ctranslate2 --collect-all onnxruntime ^
  --collect-all webview --collect-submodules yt_dlp --collect-data certifi ^
  --hidden-import av --hidden-import routes --hidden-import clr --hidden-import tkinter --hidden-import tkinter.filedialog ^
  src\app.py
if errorlevel 1 ( echo. & echo   Packaging failed. Scroll up for the error. & pause & exit /b 1 )
set "PYTHONPATH="

if "%MODE%"=="onefile" (
  set "OUT=dist"
  set "EXE=dist\ClipRadar.exe"
) else (
  set "OUT=dist\ClipRadar"
  set "EXE=dist\ClipRadar\ClipRadar.exe"
)
if not exist "%EXE%" ( echo   Build finished but ClipRadar.exe is missing. & pause & exit /b 1 )

echo   [4/4] Finishing up...
if exist tools xcopy /e /i /y /q tools "%OUT%\tools" >nul
copy /y docs\user-guide.txt "%OUT%\README.txt" >nul

rem desktop shortcut
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$exe=(Resolve-Path '%EXE%').Path; $ws=New-Object -ComObject WScript.Shell;" ^
  "$lnk=$ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Clip Radar.lnk'));" ^
  "$lnk.TargetPath=$exe; $lnk.WorkingDirectory=(Split-Path $exe); $lnk.IconLocation=$exe; $lnk.Save()" >nul 2>&1

echo.
echo   Done!  ClipRadar.exe is in:  %CD%\%OUT%
echo   A Clip Radar shortcut was added to your desktop.
echo.
echo   To use it on another PC, copy the whole "%OUT%" folder. No Python needed there.
echo.
start "" explorer "%OUT%"
choice /c YN /n /m "  Start Clip Radar now? [Y/N] "
if errorlevel 2 exit /b 0
start "" "%EXE%"
