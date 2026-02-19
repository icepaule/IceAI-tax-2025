#!/usr/bin/env bash
set -euo pipefail

URL="${1:-http://127.0.0.1:8787}"
OUT="${2:-docs/assets/screenshots/01-status-overview.png}"

if command -v chromium >/dev/null 2>&1; then
  chromium --headless --disable-gpu --screenshot="$OUT" --window-size=1600,1200 "$URL"
  echo "Screenshot geschrieben: $OUT"
  exit 0
fi

if command -v google-chrome >/dev/null 2>&1; then
  google-chrome --headless --disable-gpu --screenshot="$OUT" --window-size=1600,1200 "$URL"
  echo "Screenshot geschrieben: $OUT"
  exit 0
fi

echo "Kein Headless-Browser gefunden (chromium/google-chrome)." >&2
exit 1
