import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill export CSV rows for files skipped as hash duplicates by cloning the original row"
    )
    parser.add_argument("--export-csv", default="/data/exports/steuer_2025_export.csv")
    parser.add_argument("--hash-index", default="/data/logs/processed_hashes.json")
    parser.add_argument("--source-dir", default="/data/processed/duplicates")
    parser.add_argument(
        "--filter-regex",
        default=".*",
        help="Only consider files whose names match this regex (default: .*)",
    )
    args = parser.parse_args()

    export_csv = Path(args.export_csv)
    hash_index = Path(args.hash_index)
    source_dir = Path(args.source_dir)
    rx = re.compile(args.filter_regex)

    if not export_csv.exists():
        raise SystemExit(f"export csv not found: {export_csv}")
    if not hash_index.exists():
        raise SystemExit(f"hash index not found: {hash_index}")
    if not source_dir.exists():
        raise SystemExit(f"source dir not found: {source_dir}")

    idx = read_json(hash_index, {})
    known: Dict[str, Dict] = idx.get("hashes", {}) if isinstance(idx, dict) else {}

    with export_csv.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows: List[Dict[str, str]] = list(reader)

    if not fieldnames:
        raise SystemExit("export csv has no header")

    by_filename: Dict[str, Dict[str, str]] = {str(r.get("datei", "")).strip(): r for r in rows}

    added = 0
    missing_source_row = 0
    for file_path in sorted(source_dir.glob("*")):
        if not file_path.is_file():
            continue
        if not rx.search(file_path.name):
            continue
        if file_path.name in by_filename:
            continue
        digest = sha256_file(file_path)
        original = known.get(digest, {}).get("file", "")
        original = str(original or "").strip()
        if not original:
            continue
        src = by_filename.get(original)
        if not src:
            missing_source_row += 1
            continue
        cloned = dict(src)
        cloned["datei"] = file_path.name
        rows.append(cloned)
        by_filename[file_path.name] = cloned
        added += 1

    if added:
        with export_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)

    print(f"backfill_added={added}")
    print(f"missing_source_row={missing_source_row}")


if __name__ == "__main__":
    main()

