#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "Build this app on an Apple Silicon Mac." >&2
  exit 2
fi
for tool in ffmpeg ffprobe; do
  if [[ ! -x "tools/$tool" ]]; then
    echo "Missing tools/$tool. Run Clip Radar's Tools setup first." >&2
    exit 2
  fi
done
MODEL_DIR="$ROOT/build/macos-models"
mkdir -p "$MODEL_DIR"
cp -R src/models/. "$MODEL_DIR/"
MODEL="$MODEL_DIR/audio_events.onnx"
MODEL_SHA256=197920fa51524d0a8078a6a999d97ccbeff76fe61a86034a07d31d236b24803e
if [[ ! -f "$MODEL" ]] || [[ "$(shasum -a 256 "$MODEL" | awk '{print $1}')" != "$MODEL_SHA256" ]]; then
  curl --fail --location --silent --show-error \
    "https://github.com/allen1versonfr/vodclipper/releases/download/models-v1/audio_events.onnx" \
    --output "$MODEL.part"
  if [[ "$(shasum -a 256 "$MODEL.part" | awk '{print $1}')" != "$MODEL_SHA256" ]]; then
    rm -f "$MODEL.part"
    echo "The laughter detector model failed its SHA-256 check." >&2
    exit 2
  fi
  mv "$MODEL.part" "$MODEL"
fi

PYTHON="${PYTHON:-.venv/bin/python}"
export PYINSTALLER_CONFIG_DIR="$ROOT/build/pyinstaller-config"
"$PYTHON" -m PyInstaller --noconfirm --clean --windowed --onedir \
  --name ClipRadar --osx-bundle-identifier com.clipradar.app \
  --add-data "src/ui:ui" --add-data "$MODEL_DIR:models" \
  --add-data "tools/ffmpeg:tools" --add-data "tools/ffprobe:tools" \
  --hidden-import keyring.backends.macOS \
  --hidden-import webview.platforms.cocoa \
  --collect-all pynput \
  --hidden-import Quartz \
  src/app.py

chmod 755 dist/ClipRadar.app/Contents/MacOS/ClipRadar
chmod 755 dist/ClipRadar.app/Contents/Resources/tools/ffmpeg dist/ClipRadar.app/Contents/Resources/tools/ffprobe
codesign --force --deep --sign - dist/ClipRadar.app
ditto -c -k --sequesterRsrc --keepParent dist/ClipRadar.app dist/ClipRadar-macOS-arm64.zip
unzip -tq dist/ClipRadar-macOS-arm64.zip
if hdiutil create -volname "Clip Radar" -srcfolder dist/ClipRadar.app -ov -format UDZO dist/ClipRadar-macOS-arm64.dmg; then
  hdiutil imageinfo dist/ClipRadar-macOS-arm64.dmg >/dev/null
  echo "Built dist/ClipRadar.app, the updater ZIP, and the installer DMG (ad-hoc signed; not notarized)."
else
  echo "Built dist/ClipRadar.app and the updater ZIP; DMG creation failed (ad-hoc signed; not notarized)." >&2
fi
