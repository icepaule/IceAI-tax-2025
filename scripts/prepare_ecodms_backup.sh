#!/usr/bin/env bash
set -euo pipefail

BACKUP_ZIP="${1:-/srv/ecodms-export/dmsbackup_2026-02-15_12_33_48.zip}"
OUT_DIR="${2:-/root/tax-ai-stack/data/ecodms-export/raw}"

if [[ ! -f "$BACKUP_ZIP" ]]; then
  echo "backup_not_found: $BACKUP_ZIP" >&2
  exit 1
fi

size_1="$(stat -c '%s' "$BACKUP_ZIP")"
sleep 5
size_2="$(stat -c '%s' "$BACKUP_ZIP")"
if [[ "$size_1" != "$size_2" ]]; then
  echo "backup_still_growing: $BACKUP_ZIP ($size_1 -> $size_2)" >&2
  exit 2
fi

echo "zip_test_start: $BACKUP_ZIP"
if command -v zip >/dev/null 2>&1; then
  zip -T "$BACKUP_ZIP" >/tmp/ecodms_zip_test.log 2>&1 || {
    echo "zip_test_failed (see /tmp/ecodms_zip_test.log)" >&2
    exit 3
  }
else
  unzip -t "$BACKUP_ZIP" >/tmp/ecodms_zip_test.log 2>&1 || {
    echo "zip_test_failed (see /tmp/ecodms_zip_test.log)" >&2
    exit 3
  }
fi
echo "zip_test_ok"

mkdir -p "$OUT_DIR"
echo "extract_start: $OUT_DIR"
unzip -o "$BACKUP_ZIP" -d "$OUT_DIR" >/tmp/ecodms_unzip.log 2>&1
echo "extract_ok"

echo "top_level_entries:"
find "$OUT_DIR" -mindepth 1 -maxdepth 1 | sed -n '1,120p'
