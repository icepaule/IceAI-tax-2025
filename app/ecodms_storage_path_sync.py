import argparse
from typing import Dict, List, Optional, Tuple

from ecodms_offline_importer import parse_systemordner_paths
from paperless_importer import PaperlessClient


def fetch_all(client: PaperlessClient, endpoint: str, params: Optional[Dict[str, str]] = None) -> List[dict]:
    base_url = f"{client.base_url}/api/{endpoint}/"
    rows: List[dict] = []
    query = dict(params or {})
    query.setdefault("page_size", "200")
    url = base_url
    while True:
        resp = client.session.get(url, params=query if url == base_url else None, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        rows.extend(payload.get("results", []))
        nxt = payload.get("next")
        if not nxt:
            break
        url = nxt
    return rows


def extract_oid(tag_name: str) -> str:
    prefix = "ecodms_oid:"
    if not tag_name.startswith(prefix):
        return ""
    return tag_name[len(prefix) :].strip()


def pick_storage_path_id(tag_ids: List[int], tag_to_sp: Dict[int, Tuple[int, str]]) -> Optional[int]:
    candidates: List[Tuple[int, str]] = []
    for tid in tag_ids:
        if tid in tag_to_sp:
            candidates.append(tag_to_sp[tid])
    if not candidates:
        return None
    candidates.sort(key=lambda x: len(x[1]), reverse=True)
    return candidates[0][0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Assign storage paths to already imported ecoDMS docs")
    parser.add_argument("--backup-sql", default="/data/ecodms-export/raw/backup.sql")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-docs", type=int, default=0)
    args = parser.parse_args()

    client = PaperlessClient()
    folder_paths = parse_systemordner_paths(__import__("pathlib").Path(args.backup_sql))

    tags = fetch_all(client, "tags", {"name__icontains": "ecodms_oid:"})
    tag_to_sp: Dict[int, Tuple[int, str]] = {}
    for tag in tags:
        tag_id = int(tag.get("id"))
        oid = extract_oid(str(tag.get("name", "")))
        folder_path = folder_paths.get(oid, "")
        if not folder_path:
            continue
        sp_name = f"ecoDMS: {folder_path}"
        sp_path = f"ecoDMS/{folder_path}"
        sp_id = client.get_or_create_storage_path(sp_name, sp_path)
        tag_to_sp[tag_id] = (sp_id, folder_path)

    docs = fetch_all(client, "documents", {"tags__name__iexact": "migration:ecodms"})
    checked = 0
    updated = 0
    skipped = 0

    for doc in docs:
        doc_id = int(doc.get("id"))
        storage_path = doc.get("storage_path")
        if storage_path:
            skipped += 1
            continue
        tag_ids = [int(t) for t in doc.get("tags", [])]
        target_sp = pick_storage_path_id(tag_ids, tag_to_sp)
        if target_sp is None:
            skipped += 1
            continue

        checked += 1
        if args.max_docs and checked > args.max_docs:
            break

        if args.dry_run:
            print(f"[DRY] doc={doc_id} -> storage_path={target_sp}")
            updated += 1
            continue

        resp = client.session.patch(
            f"{client.base_url}/api/documents/{doc_id}/",
            json={"storage_path": target_sp},
            timeout=20,
        )
        if resp.status_code >= 300:
            print(f"[ERROR] doc={doc_id} status={resp.status_code} body={resp.text[:300]}")
            continue
        updated += 1
        print(f"[OK] doc={doc_id} -> storage_path={target_sp}")

    print(f"done docs_total={len(docs)} updated={updated} skipped={skipped} dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
