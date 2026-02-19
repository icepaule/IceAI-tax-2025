import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests

from config import SETTINGS

ALLOWED_SUFFIXES = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".bmp",
    ".txt",
}


class PaperlessClient:
    def __init__(self) -> None:
        self.base_url = SETTINGS.paperless_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        token = SETTINGS.paperless_api_token.strip()
        if not token:
            token = self._login_and_get_token()
        if not token:
            raise RuntimeError(
                "Paperless token missing. Set PAPERLESS_API_TOKEN or PAPERLESS_ADMIN_USER/PAPERLESS_ADMIN_PASSWORD."
            )
        self.session.headers.update({"Authorization": f"Token {token}"})
        self._tags_cache: Dict[str, int] = {}
        self._doc_types_cache: Dict[str, int] = {}
        self._storage_paths_cache: Dict[str, int] = {}

    def _login_and_get_token(self) -> str:
        if not SETTINGS.paperless_admin_user or not SETTINGS.paperless_admin_password:
            return ""
        try:
            response = requests.post(
                f"{self.base_url}/api/token/",
                data={
                    "username": SETTINGS.paperless_admin_user,
                    "password": SETTINGS.paperless_admin_password,
                },
                timeout=20,
            )
            response.raise_for_status()
            return str(response.json().get("token", "")).strip()
        except Exception:
            return ""

    def _get_or_create_named_entity(self, endpoint: str, cache: Dict[str, int], name: str) -> int:
        key = name.strip()
        if not key:
            raise RuntimeError("Empty entity name")
        if key in cache:
            return cache[key]

        response = self.session.get(
            f"{self.base_url}/api/{endpoint}/",
            params={"name__iexact": key},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        for row in data.get("results", []):
            if str(row.get("name", "")).strip().lower() == key.lower():
                entity_id = int(row["id"])
                cache[key] = entity_id
                return entity_id

        create = self.session.post(
            f"{self.base_url}/api/{endpoint}/",
            json={"name": key},
            timeout=20,
        )
        create.raise_for_status()
        entity_id = int(create.json()["id"])
        cache[key] = entity_id
        return entity_id

    def get_or_create_tag(self, name: str) -> int:
        return self._get_or_create_named_entity("tags", self._tags_cache, name)

    def get_or_create_doc_type(self, name: str) -> int:
        return self._get_or_create_named_entity("document_types", self._doc_types_cache, name)

    def get_or_create_storage_path(self, name: str, path: str) -> int:
        key = name.strip()
        if not key:
            raise RuntimeError("Empty storage path name")
        if key in self._storage_paths_cache:
            return self._storage_paths_cache[key]

        response = self.session.get(
            f"{self.base_url}/api/storage_paths/",
            params={"name__iexact": key},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        for row in data.get("results", []):
            if str(row.get("name", "")).strip().lower() == key.lower():
                entity_id = int(row["id"])
                self._storage_paths_cache[key] = entity_id
                return entity_id

        create = self.session.post(
            f"{self.base_url}/api/storage_paths/",
            json={"name": key, "path": path},
            timeout=20,
        )
        create.raise_for_status()
        entity_id = int(create.json()["id"])
        self._storage_paths_cache[key] = entity_id
        return entity_id

    def upload_document(
        self,
        file_path: Path,
        title: str,
        created_date: str,
        document_type_id: int,
        tag_ids: List[int],
        storage_path_id: int | None = None,
        upload_filename: str | None = None,
    ) -> str:
        form = [
            ("title", title),
            ("created", created_date),
            ("document_type", str(document_type_id)),
        ]
        for tag_id in tag_ids:
            form.append(("tags", str(tag_id)))
        if storage_path_id is not None:
            form.append(("storage_path", str(storage_path_id)))

        effective_filename = (upload_filename or file_path.name).strip() or file_path.name
        with file_path.open("rb") as handle:
            files = {"document": (effective_filename, handle, "application/octet-stream")}
            response = self.session.post(
                f"{self.base_url}/api/documents/post_document/",
                data=form,
                files=files,
                timeout=180,
            )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return str(payload.get("task_id", payload))
        return str(payload)


def build_hierarchy_tags(relative_parent: Path) -> List[str]:
    parts = [p for p in relative_parent.parts if p and p not in {".", "/"}]
    tags = ["source:ecodms", "migration:ecodms"]
    prefix: List[str] = []
    for part in parts:
        prefix.append(part)
        tags.append("ecodms/" + "/".join(prefix))
    return tags


def created_date_for(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def collect_files(source_dir: Path, max_files: int) -> List[Path]:
    files = [p for p in source_dir.rglob("*") if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIXES]
    files.sort()
    if max_files > 0:
        return files[:max_files]
    return files


def run_import(source_dir: Path, dry_run: bool, max_files: int) -> None:
    if not source_dir.exists():
        raise RuntimeError(f"Source directory not found: {source_dir}")

    files = collect_files(source_dir, max_files=max_files)
    print(f"Found {len(files)} files in {source_dir}")
    if not files:
        return

    client: Optional[PaperlessClient] = None if dry_run else PaperlessClient()
    doc_type_name = "ecoDMS Import"
    document_type_id = 0 if dry_run else client.get_or_create_doc_type(doc_type_name)  # type: ignore[arg-type]

    success = 0
    failed = 0
    for file_path in files:
        relative = file_path.relative_to(source_dir)
        relative_parent = relative.parent
        title = file_path.stem
        created = created_date_for(file_path)
        tag_names = build_hierarchy_tags(relative_parent)

        try:
            if dry_run:
                print(
                    f"[DRY] {relative} -> title='{title}', doc_type='{doc_type_name}', tags={len(tag_names)}"
                )
            else:
                tag_ids = [client.get_or_create_tag(name) for name in tag_names]  # type: ignore[union-attr]
                task_id = client.upload_document(
                    file_path=file_path,
                    title=title,
                    created_date=created,
                    document_type_id=document_type_id,
                    tag_ids=tag_ids,
                )
                print(f"[OK] {relative} -> task={task_id}")
            success += 1
        except Exception as exc:
            failed += 1
            print(f"[ERROR] {relative}: {exc}")

    print(f"Import done. success={success}, failed={failed}, dry_run={dry_run}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Import ecoDMS export tree into Paperless-ngx")
    parser.add_argument(
        "--source-dir",
        default=SETTINGS.ecodms_export_dir,
        help="Root directory of ecoDMS export (default: ECODMS_EXPORT_DIR)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not upload, only show planned actions")
    parser.add_argument("--max-files", type=int, default=0, help="Limit number of files (0 = all)")
    args = parser.parse_args()

    run_import(Path(args.source_dir), args.dry_run, args.max_files)


if __name__ == "__main__":
    main()
