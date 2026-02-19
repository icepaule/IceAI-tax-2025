import argparse
import csv
import re
from pathlib import Path


UUID_PDF_RX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.pdf$",
    re.IGNORECASE,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune UUID-named PDF rows from an export CSV (avoid double counting)")
    parser.add_argument("--csv", default="/data/exports/steuer_2025_export.csv")
    args = parser.parse_args()

    path = Path(args.csv)
    if not path.exists():
        raise SystemExit(f"csv not found: {path}")

    with path.open("r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        fieldnames = list(r.fieldnames or [])
        rows = list(r)

    kept = []
    removed = 0
    for row in rows:
        name = str(row.get("datei", "")).strip()
        if UUID_PDF_RX.match(name):
            removed += 1
            continue
        kept.append(row)

    if removed:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(kept)

    print(f"rows_before={len(rows)} rows_after={len(kept)} removed={removed}")


if __name__ == "__main__":
    main()

