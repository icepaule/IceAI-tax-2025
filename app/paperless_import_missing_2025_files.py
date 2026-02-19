import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set

import requests
from PIL import Image


@dataclass(frozen=True)
class Target:
    filename: str
    paths: List[Path]


def _bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes", "y", "on"}


class Paperless:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Token {token}", "Accept": "application/json"})
        self._tag_cache: Dict[str, int] = {}
        self._doc_type_cache: Dict[str, int] = {}
        self._storage_cache: Dict[str, int] = {}

    def iter_documents(self, page_size: int = 200) -> Iterable[Dict]:
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

    def get_or_create_named(self, endpoint: str, cache: Dict[str, int], name: str, extra: Dict | None = None) -> int:
        name = name.strip()
        if not name:
            raise ValueError("empty name")
        if name in cache:
            return cache[name]

        r = self.s.get(f"{self.base_url}/api/{endpoint}/", params={"name__iexact": name, "page_size": 50}, timeout=30)
        r.raise_for_status()
        for row in r.json().get("results", []):
            if str(row.get("name", "")).strip().lower() == name.lower():
                cache[name] = int(row["id"])
                return cache[name]

        payload = {"name": name}
        if extra:
            payload.update(extra)
        c = self.s.post(f"{self.base_url}/api/{endpoint}/", json=payload, timeout=30)
        c.raise_for_status()
        cache[name] = int(c.json()["id"])
        return cache[name]

    def tag_id(self, name: str) -> int:
        return self.get_or_create_named("tags", self._tag_cache, name)

    def doc_type_id(self, name: str) -> int:
        return self.get_or_create_named("document_types", self._doc_type_cache, name)

    def storage_path_id(self, name: str, path: str) -> int:
        return self.get_or_create_named("storage_paths", self._storage_cache, name, extra={"path": path})

    def upload(self, file_path: Path, upload_filename: str, title: str, created: str, doc_type_id: int, tag_ids: List[int], storage_path_id: int) -> str:
        form = [("title", title), ("created", created), ("document_type", str(doc_type_id)), ("storage_path", str(storage_path_id))]
        for tid in tag_ids:
            form.append(("tags", str(tid)))
        with file_path.open("rb") as h:
            files = {"document": (upload_filename, h, "application/octet-stream")}
            r = self.s.post(f"{self.base_url}/api/documents/post_document/", data=form, files=files, timeout=180)
        if r.status_code >= 300:
            raise RuntimeError(f"upload failed status={r.status_code} body={r.text[:300]}")
        data = r.json()
        if isinstance(data, dict):
            return str(data.get("task_id", data))
        return str(data)


def main() -> None:
    base_url = os.getenv("PAPERLESS_URL", "http://127.0.0.1:8010").strip()
    token = os.getenv("PAPERLESS_API_TOKEN", "").strip()
    dry_run = _bool(os.getenv("DRY_RUN", "false"))

    if not token:
        raise SystemExit("PAPERLESS_API_TOKEN missing")

    targets = [
        "portal_vodafone_vodafone_2025_order_1002_fallback.png",
        "vodafone_2025_order_1001_fallback.png",
        "vodafone_2025_order_1098_fallback.png",
        "vodafone_2025_order_1101_fallback.png",
        "vodafone_2025_order_1105_fallback.png",
        "vodafone_2025_order_1118_fallback.png",
        "vodafone_2025_order_1130_fallback.png",
        "vodafone_2025_order_1131_fallback.png",
        "vodafone_2025_order_1144_fallback.png",
        "vodafone_2025_order_1163_fallback.png",
        "vodafone_2025_order_1165_fallback.png",
        "vodafone_2025_order_1166_fallback.png",
    ]

    roots = [Path("/data/portal"), Path("/data/processed"), Path("/data/inbox_hold")]

    p = Paperless(base_url, token)

    existing: Set[str] = set()
    for d in p.iter_documents(page_size=200):
        ofn = str(d.get("original_file_name") or "").strip().lower()
        if ofn:
            existing.add(ofn)

    folder_tag = p.tag_id("ecodms/Marcus/Steuererklärungen/2026 für Steuerjahr 2025")
    year_tag = p.tag_id("steuerjahr:2025")
    source_tag = p.tag_id("source:portal")
    doc_type = p.doc_type_id("Portal Import")
    sp_id = p.storage_path_id(
        "ecoDMS: Marcus/Steuererklärungen/2026 für Steuerjahr 2025",
        "ecoDMS/Marcus/Steuererklärungen/2026 für Steuerjahr 2025",
    )

    imported = 0
    skipped_existing = 0
    missing = 0
    converted_to_pdf = 0

    for fn in targets:
        fn_l = fn.lower()
        if fn_l in existing:
            skipped_existing += 1
            print("SKIP_EXISTS", fn)
            continue

        found_path: Path | None = None
        for r in roots:
            if not r.exists():
                continue
            for pth in r.rglob(fn):
                found_path = pth
                break
            if found_path:
                break

        if not found_path:
            missing += 1
            print("MISSING_FILE", fn)
            continue

        title = Path(fn).stem
        created = "2026-02-15"  # import date; can be refined later if needed
        tags = [year_tag, folder_tag, source_tag]

        if dry_run:
            print("DRY_UPLOAD", fn, "from", found_path)
            continue

        upload_path = found_path
        upload_name = fn
        # Some portal PNGs fail in Paperless OCR pipeline due to missing DPI metadata.
        # Convert those to PDF before uploading; tag sync will still match via title stem.
        if fn.lower().startswith("vodafone_2025_order_") and found_path.suffix.lower() == ".png":
            try:
                img = Image.open(found_path)
                rgb = img.convert("RGB")
                tmp_pdf = Path("/tmp") / (Path(fn).stem + ".pdf")
                rgb.save(tmp_pdf, "PDF")
                upload_path = tmp_pdf
                upload_name = tmp_pdf.name
                converted_to_pdf += 1
            except Exception as e:
                print("CONVERT_ERR", fn, e)

        try:
            task_id = p.upload(upload_path, upload_name, title, created, doc_type, tags, sp_id)
            imported += 1
            existing.add(upload_name.lower())
            print("OK", fn, "task", task_id)
        except Exception as e:
            print("ERR", fn, e)

    print(
        f"done dry_run={dry_run} imported={imported} skipped_existing={skipped_existing} "
        f"missing={missing} converted_to_pdf={converted_to_pdf}"
    )


if __name__ == "__main__":
    main()
