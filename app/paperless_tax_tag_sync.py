import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set

import requests


@dataclass(frozen=True)
class ExportRow:
    filename: str
    tax_year: str
    category_key: str
    review_required: bool


def _bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def _category_to_tags(category_key: str) -> List[str]:
    """
    Maps taxonomy category keys like 'werbungskosten_arbeitsmittel' to user-friendly tag names.
    Keep tags stable and queryable: 'Steuer/Werbungskosten' + 'Steuer/Werbungskosten/Arbeitsmittel'.
    """
    key = (category_key or "").strip()
    if not key:
        return ["Steuer/Pruefung"]

    if key == "sonstiges_pruefung":
        return ["Steuer/Pruefung"]

    parts = key.split("_", 1)
    group_key = parts[0]
    sub_key = parts[1] if len(parts) == 2 else ""

    group_map = {
        "werbungskosten": "Werbungskosten",
        "sonderausgaben": "Sonderausgaben",
        "haushaltsnahe": "Haushaltsnahe Leistungen",
        "hausverwaltung": "Hausverwaltung",
        "versicherungen": "Versicherungen",
        "gesundheit": "Gesundheit",
        "bank": "Bank",
    }
    group = group_map.get(group_key, group_key.title())
    tags = [f"Steuer/{group}"]

    if sub_key:
        # sub_key may contain multiple underscores (e.g. sonderausgaben_spenden already split)
        sub = " ".join([p for p in sub_key.split("_") if p]).title()
        tags.append(f"Steuer/{group}/{sub}")

    tags.append(f"Steuer/Kategorie/{key}")
    return tags


class Paperless:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Token {token}", "Accept": "application/json"})
        self._tag_cache: Dict[str, int] = {}

    def get_or_create_tag_id(self, name: str) -> int:
        name = name.strip()
        if not name:
            raise ValueError("empty tag")
        if name in self._tag_cache:
            return self._tag_cache[name]

        r = self.s.get(f"{self.base_url}/api/tags/", params={"name__iexact": name, "page_size": 50}, timeout=30)
        r.raise_for_status()
        for row in r.json().get("results", []):
            if str(row.get("name", "")).strip().lower() == name.lower():
                tid = int(row["id"])
                self._tag_cache[name] = tid
                return tid

        c = self.s.post(f"{self.base_url}/api/tags/", json={"name": name}, timeout=30)
        c.raise_for_status()
        tid = int(c.json()["id"])
        self._tag_cache[name] = tid
        return tid

    def iter_documents(self, page_size: int = 200) -> Iterable[Dict]:
        """
        Paperless does not necessarily expose filter backends for every field.
        Build a local index from the full document list (id/title/original_file_name/tags/...).
        """
        url = f"{self.base_url}/api/documents/"
        params = {"page_size": page_size}
        while True:
            r = self.s.get(url, params=params, timeout=60)
            r.raise_for_status()
            payload = r.json()
            for d in payload.get("results", []):
                yield d
            nxt = payload.get("next")
            if not nxt:
                break
            url = nxt
            params = None

    def patch_doc_tags(self, doc_id: int, existing_tag_ids: Iterable[int], add_tag_ids: Iterable[int]) -> None:
        tags = set(int(t) for t in (existing_tag_ids or []))
        tags.update(int(t) for t in add_tag_ids)
        payload = {"tags": sorted(tags)}
        r = self.s.patch(
            f"{self.base_url}/api/documents/{doc_id}/",
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        r.raise_for_status()


def read_export_rows(csv_path: Path) -> List[ExportRow]:
    rows: List[ExportRow] = []
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(
                ExportRow(
                    filename=str(row.get("datei", "")).strip(),
                    tax_year=str(row.get("steuer_jahr", "")).strip(),
                    category_key=str(row.get("steuer_kategorie", "")).strip(),
                    review_required=_bool(str(row.get("review_required", ""))),
                )
            )
    return rows


def main() -> None:
    base_url = os.getenv("PAPERLESS_URL", "http://127.0.0.1:8010").strip()
    token = os.getenv("PAPERLESS_API_TOKEN", "").strip()
    export_path = Path(os.getenv("EXPORT_CSV", "/data/exports/steuer_2025_export.csv"))
    dry_run = _bool(os.getenv("DRY_RUN", "false"))

    if not token:
        raise SystemExit("PAPERLESS_API_TOKEN missing")
    if not export_path.exists():
        raise SystemExit(f"Export CSV not found: {export_path}")

    p = Paperless(base_url=base_url, token=token)
    rows = read_export_rows(export_path)

    # Build local index: original_file_name -> docs, title -> docs
    by_file: Dict[str, List[Dict]] = {}
    by_title: Dict[str, List[Dict]] = {}
    docs_total = 0
    for d in p.iter_documents(page_size=200):
        docs_total += 1
        ofn = str(d.get("original_file_name") or "").strip().lower()
        title = str(d.get("title") or "").strip().lower()
        if ofn:
            by_file.setdefault(ofn, []).append(d)
        if title:
            by_title.setdefault(title, []).append(d)

    updated_docs: Set[int] = set()
    missing_files: List[str] = []
    matched_rows = 0

    for r in rows:
        if not r.filename or not r.tax_year:
            continue

        tag_names = [f"steuerjahr:{r.tax_year}"]
        tag_names.extend(_category_to_tags(r.category_key))
        if r.review_required:
            tag_names.append("Steuer/Review")

        tag_ids = [p.get_or_create_tag_id(n) for n in tag_names]

        filename_l = r.filename.strip().lower()
        docs = by_file.get(filename_l, [])
        if not docs:
            # Fallback: match by title = stem
            stem = Path(r.filename).stem.strip().lower()
            docs = by_title.get(stem, [])

        if not docs:
            missing_files.append(r.filename)
            continue

        matched_rows += 1
        for d in docs:
            doc_id = int(d["id"])
            if dry_run:
                continue
            p.patch_doc_tags(doc_id, d.get("tags") or [], tag_ids)
            updated_docs.add(doc_id)

    print(f"docs_total={docs_total} export_rows={len(rows)} matched_rows={matched_rows} dry_run={dry_run}")
    print(f"updated_docs={len(updated_docs)}")
    print(f"missing_files={len(missing_files)}")
    if missing_files:
        print("missing_files_sample=" + ", ".join(missing_files[:10]))


if __name__ == "__main__":
    main()
