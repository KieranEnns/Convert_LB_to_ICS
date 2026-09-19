#!/bin/zsh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="LB to ICS Converter"
ZIP_NAME="LB-to-ICS-Converter-macOS.zip"

cd "$PROJECT_DIR"

PYTHON="python3"
if [ -x "$PROJECT_DIR/.venv/bin/python3" ]; then
  PYTHON="$PROJECT_DIR/.venv/bin/python3"
fi

"$PYTHON" -m pip install -r requirements.txt
"$PYTHON" -m pip install -r requirements-build.txt

rm -rf build dist

"$PYTHON" -m PyInstaller \
  --noconfirm \
  --windowed \
  --target-arch universal2 \
  --name "$APP_NAME" \
  desktop_launcher.py

rm -f "$ZIP_NAME"
ditto -c -k --keepParent "dist/${APP_NAME}.app" "$ZIP_NAME"

echo "Built ${PROJECT_DIR}/${ZIP_NAME}"
