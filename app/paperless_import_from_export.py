import csv
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import requests

from paperless_importer import PaperlessClient


ALLOWED_BINARY_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


def _bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _safe_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_float(value: str, default: float = 0.0) -> float:
    text = str(value or "").strip().replace(",", ".")
    try:
        return float(text)
    except Exception:
        return default


def _parse_date(value: str) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    # Common formats seen in exports / mail receipts.
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except Exception:
            continue
    return None


def _mtime_date(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def _tax_year_folder_tag(tax_year: int) -> str:
    """
    We model "Steuerjahr YYYY" documents under the following ecoDMS-like hierarchy:
      Marcus/Steuererklärungen/{YYYY+1} für Steuerjahr {YYYY}
    """
    return f"ecodms/Marcus/Steuererklärungen/{tax_year + 1} für Steuerjahr {tax_year}"


def _tax_year_storage_path_name(tax_year: int) -> Tuple[str, str]:
    name = f"ecoDMS: Marcus/Steuererklärungen/{tax_year + 1} für Steuerjahr {tax_year}"
    path = f"ecoDMS/Marcus/Steuererklärungen/{tax_year + 1} für Steuerjahr {tax_year}"
    return name, path


def _iter_paperless_documents(base_url: str, token: str, page_size: int = 200) -> Iterable[Dict]:
    s = requests.Session()
    s.headers.update({"Authorization": f"Token {token}", "Accept": "application/json"})
    url = base_url.rstrip("/") + "/api/documents/"
    params = {"page_size": page_size}
    while True:
        r = s.get(url, params=params, timeout=60)
        r.raise_for_status()
        payload = r.json()
        for d in payload.get("results", []):
            yield d
        nxt = payload.get("next")
        if not nxt:
            break
        url = nxt
        params = None


@dataclass(frozen=True)
class ExportRow:
    filename: str
    tax_year: int
    created_hint: str
    merchant: str
    gross_amount: float


def _read_export_rows(path: Path, wanted_tax_year: int) -> List[ExportRow]:
    rows: List[ExportRow] = []
    with path.open("r", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            fn = str(r.get("datei", "")).strip()
            if not fn:
                continue
            ty = _safe_int(str(r.get("steuer_jahr", "")).strip(), default=0)
            if ty != wanted_tax_year:
                continue
            created_hint = str(r.get("belegdatum", "")).strip()
            merchant = str(r.get("haendler", "")).strip()
            gross = _safe_float(str(r.get("brutto_betrag", "")).strip(), default=0.0)
            rows.append(
                ExportRow(
                    filename=fn,
                    tax_year=ty,
                    created_hint=created_hint,
                    merchant=merchant,
                    gross_amount=gross,
                )
            )
    return rows


def _build_file_index(roots: List[Path]) -> Dict[str, Path]:
    index: Dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            key = p.name.strip().lower()
            if not key:
                continue
            # Prefer "real" docs over common noise files if duplicates exist.
            if key in index:
                existing = index[key]
                # Prefer PDFs over images, and larger files over tiny assets.
                if existing.suffix.lower() != ".pdf" and p.suffix.lower() == ".pdf":
                    index[key] = p
                elif existing.stat().st_size < 5000 <= p.stat().st_size:
                    index[key] = p
                continue
            index[key] = p
    return index


def main() -> None:
    export_csv = Path(os.getenv("EXPORT_CSV", "/data/exports/steuer_2025_export.csv"))
    tax_year = _safe_int(os.getenv("TAX_YEAR", "2025"), default=2025)
    dry_run = _bool(os.getenv("DRY_RUN", "false"))
    import_txt = _bool(os.getenv("IMPORT_TXT", "false"))
    max_uploads = _safe_int(os.getenv("MAX_UPLOADS", "0"), default=0)

    if not export_csv.exists():
        raise SystemExit(f"export csv not found: {export_csv}")

    client = None if dry_run else PaperlessClient()
    base_url = (os.getenv("PAPERLESS_URL", "http://127.0.0.1:8010") or "").strip().rstrip("/")
    token = (os.getenv("PAPERLESS_API_TOKEN", "") or "").strip()
    if not token and not dry_run:
        # PaperlessClient already resolved auth; re-use its session header.
        token = str(getattr(client, "session").headers.get("Authorization", "")).replace("Token ", "").strip()  # type: ignore[union-attr]
    if not token:
        raise SystemExit("PAPERLESS_API_TOKEN missing (or PAPERLESS_ADMIN_USER/PAPERLESS_ADMIN_PASSWORD)")

    # Build existing original_file_name index.
    existing: Set[str] = set()
    for d in _iter_paperless_documents(base_url, token, page_size=200):
        ofn = str(d.get("original_file_name") or "").strip().lower()
        if ofn:
            existing.add(ofn)

    roots: List[Path] = []
    patterns = [
        "/data/pipeline_inbox_imap_*",
        "/data/pipeline_inbox_imap_gmail_*",
        "/data/pipeline_inbox_imap_proton_*",
        "/data/pipeline_inbox_vodafone",
        "/data/processed",
        "/data/portal",
        "/data/inbox_hold",
        "/data/inbox",
        "/data/manual-drop",
    ]
    for pat in patterns:
        for p in sorted(Path("/").glob(pat.lstrip("/"))):
            roots.append(p)

    file_index = _build_file_index(roots)
    rows = _read_export_rows(export_csv, wanted_tax_year=tax_year)

    folder_tag = _tax_year_folder_tag(tax_year)
    storage_name, storage_path = _tax_year_storage_path_name(tax_year)

    # Keep uploads predictable and queryable.
    base_tag_names = [folder_tag, f"steuerjahr:{tax_year}"]
    # Avoid importing obvious noise assets.
    skip_name = re.compile(r"(fallback|_icon\.png$|(^|/)(icon|logo|image)\.png$)", re.IGNORECASE)
    skip_exact = {"icon.png"}
    invoice_hint = re.compile(
        r"(rechnung|invoice|beleg|receipt|quittung|kassenbon|bestell|order|zahlung|payment|abo|subscription|renew|verl(a|ä)nger|sipgate|strato)",
        re.IGNORECASE,
    )

    uploaded = 0
    skipped_exists = 0
    skipped_missing = 0
    skipped_suffix = 0
    skipped_noise = 0

    if not dry_run:
        assert client is not None
        doc_type_id = client.get_or_create_doc_type("Tax Import")
        folder_tag_id = client.get_or_create_tag(folder_tag)
        year_tag_id = client.get_or_create_tag(f"steuerjahr:{tax_year}")
        source_tag_id = client.get_or_create_tag("source:import")
        storage_path_id = client.get_or_create_storage_path(storage_name, storage_path)
        base_tag_ids = [folder_tag_id, year_tag_id, source_tag_id]
    else:
        doc_type_id = 0
        storage_path_id = None
        base_tag_ids = []

    for r in rows:
        fn = r.filename.strip()
        fn_l = fn.lower()
        if not fn or fn_l in skip_exact or skip_name.search(fn_l):
            skipped_noise += 1
            continue
        if fn_l in existing:
            skipped_exists += 1
            continue

        suffix = Path(fn).suffix.lower()
        if suffix == ".txt":
            if not import_txt:
                skipped_suffix += 1
                continue
            # Only import mail bodies when the filename strongly suggests a receipt/invoice.
            if not invoice_hint.search(fn):
                skipped_noise += 1
                continue
        if suffix != ".txt" and suffix not in ALLOWED_BINARY_SUFFIXES:
            skipped_suffix += 1
            continue

        found = file_index.get(fn_l)
        if not found or not found.exists():
            skipped_missing += 1
            continue

        created = _parse_date(r.created_hint) or _mtime_date(found)
        title = Path(fn).stem

        if dry_run:
            print(f"[DRY] upload {fn} from {found} created={created} tags={base_tag_names}")
            uploaded += 1
        else:
            assert client is not None
            try:
                client.upload_document(
                    file_path=found,
                    title=title,
                    created_date=created,
                    document_type_id=doc_type_id,
                    tag_ids=base_tag_ids,
                    storage_path_id=storage_path_id,
                    upload_filename=fn,
                )
                existing.add(fn_l)
                uploaded += 1
                print(f"[OK] {fn} ({found})")
            except Exception as exc:
                print(f"[ERR] {fn}: {exc}")

        if max_uploads > 0 and uploaded >= max_uploads:
            break

    print(
        "done "
        f"tax_year={tax_year} dry_run={dry_run} import_txt={import_txt} "
        f"uploaded={uploaded} skipped_exists={skipped_exists} skipped_missing={skipped_missing} "
        f"skipped_suffix={skipped_suffix} skipped_noise={skipped_noise}"
    )


if __name__ == "__main__":
    main()
