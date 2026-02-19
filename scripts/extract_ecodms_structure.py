#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path


def parse_systemordner(backup_sql: Path):
    rows = []
    in_copy = False
    with backup_sql.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not in_copy:
                if line.startswith("COPY classify01.systemordner "):
                    in_copy = True
                continue
            if line.strip() == r"\.":
                break
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 7:
                continue
            oid = parts[0].strip()
            name = parts[1].strip()
            flag = parts[3].strip()
            parentid = parts[5].strip()
            deleted = parts[6].strip()
            if not oid:
                continue
            rows.append(
                {
                    "oid": oid,
                    "name": name,
                    "flag": flag,
                    "parentid": parentid,
                    "deleted": deleted,
                }
            )
    return rows


def build_paths(rows):
    by_oid = {r["oid"]: r for r in rows}

    def resolve_path(oid: str, seen: set[str] | None = None) -> str:
        if seen is None:
            seen = set()
        if oid in seen:
            return by_oid[oid]["name"]
        seen.add(oid)
        row = by_oid[oid]
        parent = row["parentid"]
        if not parent or parent in {"-1", "0"} or parent not in by_oid:
            return row["name"]
        return f"{resolve_path(parent, seen)}/{row['name']}"

    out = []
    for r in rows:
        path = resolve_path(r["oid"])
        level = path.count("/")
        out.append({**r, "path": path, "level": str(level)})

    def oid_sort_key(oid: str):
        key = []
        for p in oid.split("."):
            if p.isdigit():
                key.append((0, int(p)))
            else:
                key.append((1, p))
        return key

    out.sort(key=lambda x: oid_sort_key(x["oid"]))
    return out


def main():
    parser = argparse.ArgumentParser(description="Extract ecoDMS folder hierarchy from backup.sql")
    parser.add_argument("--backup-sql", default="/root/tax-ai-stack/data/ecodms-export/raw/backup.sql")
    parser.add_argument("--out-csv", default="/root/tax-ai-stack/data/ecodms-export/systemordner_paths.csv")
    parser.add_argument("--out-tree", default="/root/tax-ai-stack/data/ecodms-export/systemordner_tree.txt")
    args = parser.parse_args()

    backup_sql = Path(args.backup_sql)
    rows = parse_systemordner(backup_sql)
    if not rows:
        raise SystemExit("No systemordner rows found in backup.sql")

    enriched = build_paths(rows)

    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["oid", "parentid", "name", "flag", "deleted", "level", "path"])
        writer.writeheader()
        writer.writerows(enriched)

    out_tree = Path(args.out_tree)
    lines = []
    for row in enriched:
        indent = "  " * int(row["level"])
        marker = " [M]" if "M" in row["flag"] else ""
        lines.append(f"{indent}- {row['name']} ({row['oid']}){marker}")
    out_tree.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"rows={len(enriched)}")
    print(f"csv={out_csv}")
    print(f"tree={out_tree}")


if __name__ == "__main__":
    main()
