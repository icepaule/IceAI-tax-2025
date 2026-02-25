"""
Assigns Paperless storage paths based on person tags (Babsi / Marcus).

Runs through all documents that have the 'ki-verarbeitet' tag but no storage path yet,
and assigns the correct archive path based on person tags.

Usage:
    docker compose run --rm tax-pipeline python paperless_storage_path_sync.py
    docker compose run --rm tax-pipeline python paperless_storage_path_sync.py --dry-run
"""

import os
import sys
from typing import Dict, List, Optional

import requests


class Paperless:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update(
            {"Authorization": f"Token {token}", "Accept": "application/json"}
        )

    def _get_tag_id(self, name: str) -> Optional[int]:
        r = self.s.get(
            f"{self.base_url}/api/tags/",
            params={"name__iexact": name, "page_size": 50},
            timeout=30,
        )
        r.raise_for_status()
        for row in r.json().get("results", []):
            if str(row.get("name", "")).strip().lower() == name.lower():
                return int(row["id"])
        return None

    def _get_storage_path_id(self, name: str) -> Optional[int]:
        r = self.s.get(
            f"{self.base_url}/api/storage_paths/",
            params={"name__iexact": name, "page_size": 50},
            timeout=30,
        )
        r.raise_for_status()
        for row in r.json().get("results", []):
            if str(row.get("name", "")).strip().lower() == name.lower():
                return int(row["id"])
        return None

    def iter_documents(self, page_size: int = 100):
        url = f"{self.base_url}/api/documents/"
        params: Optional[Dict] = {"page_size": page_size}
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

    def set_storage_path(self, doc_id: int, storage_path_id: int) -> None:
        r = self.s.patch(
            f"{self.base_url}/api/documents/{doc_id}/",
            json={"storage_path": storage_path_id},
            timeout=30,
        )
        r.raise_for_status()


def main() -> None:
    base_url = os.getenv("PAPERLESS_URL", "http://127.0.0.1:8010").strip()
    token = os.getenv("PAPERLESS_API_TOKEN", "").strip()
    dry_run = "--dry-run" in sys.argv

    if not token:
        print("ERROR: PAPERLESS_API_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    p = Paperless(base_url, token)

    # --- Customize: Map person-tag names to storage-path names ---
    # Each entry: (person_tag_name, storage_path_name)
    person_paths = [
        ("Person-A", "Archiv Person-A"),
        ("Person-B", "Archiv Person-B"),
    ]
    fallback_sp_name = "Archiv Allgemein"

    # Resolve tag IDs
    tag_ki = p._get_tag_id("ki-verarbeitet")

    if tag_ki is None:
        print("Tag 'ki-verarbeitet' not found -- nothing to do.")
        return

    # Resolve person tags and storage paths
    person_map: List[tuple] = []  # (tag_id, sp_id, label)
    for tag_name, sp_name in person_paths:
        tid = p._get_tag_id(tag_name)
        spid = p._get_storage_path_id(sp_name)
        if tid and spid:
            person_map.append((tid, spid, tag_name))

    sp_allgemein = p._get_storage_path_id(fallback_sp_name)
    if sp_allgemein is None:
        print(f"ERROR: Storage path '{fallback_sp_name}' not found. Run 'make setup-tags' first.",
              file=sys.stderr)
        sys.exit(1)

    assigned = 0
    skipped = 0

    for doc in p.iter_documents():
        doc_id = doc["id"]
        doc_tags = set(doc.get("tags", []))
        current_sp = doc.get("storage_path")

        # Only process documents tagged by AI
        if tag_ki not in doc_tags:
            continue

        # Skip if already has a storage path
        if current_sp is not None:
            skipped += 1
            continue

        # Determine storage path based on person tag
        target_sp = sp_allgemein
        target_label = "Allgemein"
        for tid, spid, label in person_map:
            if tid in doc_tags:
                target_sp = spid
                target_label = label
                break

        title = doc.get("title", f"id:{doc_id}")
        if dry_run:
            print(f"[DRY] {title} -> Archiv {target_label}")
        else:
            p.set_storage_path(doc_id, target_sp)
            print(f"[OK] {title} -> Archiv {target_label}")
        assigned += 1

    print(f"\nDone. assigned={assigned} skipped={skipped} dry_run={dry_run}")


if __name__ == "__main__":
    main()
