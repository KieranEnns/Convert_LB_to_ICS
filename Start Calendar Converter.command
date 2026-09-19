#!/bin/zsh

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="8000"
URL="http://127.0.0.1:${PORT}"

cd "$APP_DIR" || exit 1

close_command_window() {
  if [ "$TERM_PROGRAM" = "Apple_Terminal" ]; then
    (sleep 0.5; osascript -e 'tell application "Terminal" to close front window' >/dev/null 2>&1) &
  fi
}

if lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; then
  open "$URL"
  close_command_window
  exit 0
fi

PYTHON="python3"
if [ -x "$APP_DIR/.venv/bin/python3" ]; then
  if "$APP_DIR/.venv/bin/python3" -B -c "import pypdf" >/dev/null 2>&1; then
    PYTHON="$APP_DIR/.venv/bin/python3"
  fi
fi

nohup "$PYTHON" -B web_app.py --port "$PORT" >/tmp/calendar-converter.log 2>&1 &
disown
sleep 1
open "$URL"
close_command_window
exit 0
