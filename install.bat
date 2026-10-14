@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title VodClipper setup
echo.
echo   VodClipper setup
echo   ----------------
echo.

rem ---------- Python (3.10 to 3.13; the caption library has no 3.14 build yet) ----------
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
  echo   Python 3.12 is needed. Installing it with winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
  if errorlevel 1 (
    echo.
    echo   Couldn't install Python automatically.
    echo   Get Python 3.12 from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  ) else (
    echo.
    echo   Python installed. Close this window and double-click install.bat again.
  )
  echo.
  pause
  exit /b 1
)
for /f "delims=" %%v in ('%PY% -c "import platform; print(platform.python_version())"') do set "PYV=%%v"
echo   [1/5] Python %PYV% found.

rem ---------- virtual environment ----------
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>&1 || rmdir /s /q .venv
)
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
  if errorlevel 1 ( echo   Couldn't create the Python environment. & pause & exit /b 1 )
)
set "VPY=.venv\Scripts\python.exe"

echo   [2/5] Installing core packages...
"%VPY%" -m pip install --upgrade pip --disable-pip-version-check -q
"%VPY%" -m pip install -U --disable-pip-version-check -q numpy "yt-dlp[default]"
if errorlevel 1 ( echo   Package install failed. Check your internet connection and run install.bat again. & pause & exit /b 1 )

echo   [3/5] Installing captions (Whisper). First time takes a few minutes...
"%VPY%" -m pip install -U --disable-pip-version-check -q faster-whisper
if errorlevel 1 (
  echo   Captions couldn't be installed. VodClipper will still work, just without captions.
)

rem ---------- ffmpeg ----------
if not exist tools mkdir tools
if exist "tools\ffmpeg.exe" if exist "tools\ffprobe.exe" ( echo   [4/5] ffmpeg found. & goto :tdl )
where ffmpeg >nul 2>&1 && where ffprobe >nul 2>&1 && ( echo   [4/5] ffmpeg found. & goto :tdl )
echo   [4/5] Downloading ffmpeg (about 100 MB)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;" ^
  "$z=Join-Path $env:TEMP 'vc-ffmpeg.zip'; $d=Join-Path $env:TEMP 'vc-ffmpeg';" ^
  "Invoke-WebRequest 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $z -UseBasicParsing;" ^
  "if (Test-Path $d) { Remove-Item $d -Recurse -Force }; Expand-Archive $z $d -Force;" ^
  "Get-ChildItem $d -Recurse -Include ffmpeg.exe,ffprobe.exe | Copy-Item -Destination 'tools' -Force;" ^
  "Remove-Item $z,$d -Recurse -Force"
if not exist "tools\ffmpeg.exe" (
  echo   ffmpeg download failed. Download "ffmpeg-release-essentials.zip" from https://www.gyan.dev/ffmpeg/builds/
  echo   and copy ffmpeg.exe and ffprobe.exe from its bin folder into the tools folder here.
)

rem ---------- TwitchDownloaderCLI (chat replay) ----------
:tdl
if exist "tools\TwitchDownloaderCLI.exe" ( echo   [5/5] TwitchDownloaderCLI found. & goto :done )
echo   [5/5] Downloading TwitchDownloaderCLI (for chat replay)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;" ^
  "$r=Invoke-RestMethod 'https://api.github.com/repos/lay295/TwitchDownloader/releases/latest' -Headers @{'User-Agent'='VodClipper'};" ^
  "$a=$r.assets | Where-Object { $_.name -like 'TwitchDownloaderCLI-*Windows-x64.zip' } | Select-Object -First 1;" ^
  "$z=Join-Path $env:TEMP 'vc-tdl.zip'; $d=Join-Path $env:TEMP 'vc-tdl';" ^
  "Invoke-WebRequest $a.browser_download_url -OutFile $z -UseBasicParsing;" ^
  "if (Test-Path $d) { Remove-Item $d -Recurse -Force }; Expand-Archive $z $d -Force;" ^
  "Get-ChildItem $d -Recurse -Filter TwitchDownloaderCLI.exe | Copy-Item -Destination 'tools' -Force;" ^
  "Remove-Item $z,$d -Recurse -Force"
if not exist "tools\TwitchDownloaderCLI.exe" (
  echo   Couldn't get TwitchDownloaderCLI. Clips will be ranked by audio only.
  echo   You can download it from https://github.com/lay295/TwitchDownloader/releases and put
  echo   TwitchDownloaderCLI.exe in the tools folder.
)

:done
echo.
echo   Setup finished. Double-click run.bat to start VodClipper.
echo.
pause
