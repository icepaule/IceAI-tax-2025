import argparse
import csv
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
import re
import subprocess
import tempfile

import pytesseract
from PIL import Image

from classifier import TaxClassifier
from config import SETTINGS
from ecodms_client import EcoDMSClient
from dropbox_collector import collect_from_dropbox
from imap_collector import collect_attachments
from ollama_client import OllamaClient
from reference_compare import compare_category_totals, load_reference_records
from steuerbox_uploader import SteuerboxUploader

DATA_ROOT = Path("/data")
# Default inbox is shared with Paperless' consumer. For the pipeline, support a separate
# staging dir to avoid Paperless moving files while we're hashing/processing them.
INBOX = Path(os.getenv("PIPELINE_INBOX_DIR", str(DATA_ROOT / "inbox")))
PROCESSED = DATA_ROOT / "processed"
EXPORTS = DATA_ROOT / "exports"
LOGS = DATA_ROOT / "logs"
HASH_INDEX = LOGS / "processed_hashes.json"
SEMANTIC_INDEX = LOGS / "processed_semantic.json"
LAST_NONEMPTY_EXPORT = LOGS / "last_nonempty_export.json"
PORTAL_INGEST_INDEX = LOGS / "portal_ingest_index.json"


def _replace_tax_year_tokens(value: str, tax_year: int) -> str:
    text = str(value or "")
    text = text.replace("{TAX_YEAR}", str(tax_year))
    text = text.replace("2025", str(tax_year))
    return text


def _resolve_runtime_tax_year(explicit_tax_year: int | None, current_year_mode: bool) -> int:
    current_year = datetime.now().year
    if explicit_tax_year is not None:
        return explicit_tax_year
    if current_year_mode:
        return current_year
    return current_year - 1


def _apply_runtime_tax_year(tax_year: int) -> None:
    SETTINGS.tax_year = tax_year
    SETTINGS.imap_search = f"SINCE 01-Jan-{tax_year} BEFORE 01-Jan-{tax_year + 1}"
    os.environ["IMAP_SEARCH"] = SETTINGS.imap_search

    SETTINGS.export_filename = _replace_tax_year_tokens(SETTINGS.export_filename, tax_year)
    SETTINGS.unsicher_filename = _replace_tax_year_tokens(SETTINGS.unsicher_filename, tax_year)
    SETTINGS.steuerbox_subject_prefix = _replace_tax_year_tokens(
        SETTINGS.steuerbox_subject_prefix, tax_year
    )
    SETTINGS.ecodms_classification_path = _replace_tax_year_tokens(
        SETTINGS.ecodms_classification_path, tax_year
    )


def read_document_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".json"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        try:
            with Image.open(path) as image:
                text = pytesseract.image_to_string(image, lang=SETTINGS.ocr_langs)
                return text.strip()
        except Exception:
            return ""
    if suffix == ".pdf":
        with tempfile.TemporaryDirectory(prefix="tax-ocr-") as temp_dir:
            txt_path = Path(temp_dir) / "pdf_text.txt"
            try:
                subprocess.run(
                    ["pdftotext", "-layout", str(path), str(txt_path)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if txt_path.exists():
                    text = txt_path.read_text(encoding="utf-8", errors="ignore").strip()
                    if len(text) > 200:
                        return text
            except Exception:
                pass

            # OCR fallback for scanned PDFs.
            try:
                out_prefix = str(Path(temp_dir) / "page")
                subprocess.run(
                    [
                        "pdftoppm",
                        "-f",
                        "1",
                        "-l",
                        str(max(1, SETTINGS.ocr_pdf_max_pages)),
                        "-png",
                        str(path),
                        out_prefix,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
            except Exception:
                return ""

            chunks: List[str] = []
            for img_path in sorted(Path(temp_dir).glob("page-*.png")):
                try:
                    with Image.open(img_path) as image:
                        chunk = pytesseract.image_to_string(image, lang=SETTINGS.ocr_langs).strip()
                        if chunk:
                            chunks.append(chunk)
                except Exception:
                    continue
            return "\n\n".join(chunks)

    return ""


def build_record(file_path: Path, extracted: Dict[str, Any], category: Dict[str, str]) -> Dict[str, Any]:
    product = (extracted.get("line_items_summary", "") or "").strip()
    if len(product) > 120:
        product = product[:117] + "..."
    ecodms_path = _replace_tax_year_tokens(category["ecodms_path"], SETTINGS.tax_year)
    return {
        "datei": file_path.name,
        "steuer_jahr": SETTINGS.tax_year,
        "belegdatum": extracted.get("invoice_date", ""),
        "haendler": extracted.get("merchant", ""),
        "produkt": product,
        "belegnummer": extracted.get("invoice_number", ""),
        "dokumenttyp": extracted.get("document_type", ""),
        "brutto_betrag": extracted.get("gross_amount", 0.0),
        "waehrung": extracted.get("currency", SETTINGS.default_currency),
        "steuer_kategorie": category["category"],
        "steuer_web_hinweis": category["steuer_web_hint"],
        "ecodms_pfad": ecodms_path,
        "ecodms_mainfolder_oid": category["ecodms_mainfolder_oid"],
        "ecodms_folder_oid": category["ecodms_folder_oid"],
        "tax_relevance_hint": extracted.get("tax_relevance_hint", ""),
        "confidence": extracted.get("confidence", 0.0),
        "review_required": extracted.get("review_required", True),
    }


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        # Keep previous export if this run produced no rows.
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        with path.open("r", newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def _row_identity(row: Dict[str, Any]) -> str:
    parts = [
        str(row.get("datei", "")).strip().lower(),
        str(row.get("belegnummer", "")).strip().lower(),
        str(row.get("belegdatum", "")).strip().lower(),
        str(row.get("haendler", "")).strip().lower(),
        str(row.get("brutto_betrag", "")).strip().lower(),
    ]
    return "|".join(parts)


def merge_rows(existing: List[Dict[str, Any]], new_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged = list(existing)
    known = {_row_identity(r) for r in existing}
    for row in new_rows:
        ident = _row_identity(row)
        if ident in known:
            continue
        known.add(ident)
        merged.append(row)
    return merged


def load_last_nonempty_export_rows() -> List[Dict[str, Any]]:
    if not LAST_NONEMPTY_EXPORT.exists():
        return []
    try:
        payload = json.loads(LAST_NONEMPTY_EXPORT.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return payload
        return []
    except Exception:
        return []


def save_last_nonempty_export_rows(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    LAST_NONEMPTY_EXPORT.parent.mkdir(parents=True, exist_ok=True)
    LAST_NONEMPTY_EXPORT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_file_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned or "unbekannt"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_hash_index() -> Dict[str, Any]:
    if not HASH_INDEX.exists():
        return {"hashes": {}}
    try:
        return json.loads(HASH_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {"hashes": {}}


def save_hash_index(index: Dict[str, Any]) -> None:
    HASH_INDEX.parent.mkdir(parents=True, exist_ok=True)
    HASH_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def load_semantic_index() -> Dict[str, Any]:
    if not SEMANTIC_INDEX.exists():
        return {"keys": {}}
    try:
        return json.loads(SEMANTIC_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {"keys": {}}


def save_semantic_index(index: Dict[str, Any]) -> None:
    SEMANTIC_INDEX.parent.mkdir(parents=True, exist_ok=True)
    SEMANTIC_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def load_portal_ingest_index() -> Dict[str, Any]:
    if not PORTAL_INGEST_INDEX.exists():
        return {"sources": {}}
    try:
        payload = json.loads(PORTAL_INGEST_INDEX.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload.setdefault("sources", {})
            return payload
    except Exception:
        pass
    return {"sources": {}}


def save_portal_ingest_index(index: Dict[str, Any]) -> None:
    PORTAL_INGEST_INDEX.parent.mkdir(parents=True, exist_ok=True)
    PORTAL_INGEST_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_from_portal_sources(tax_year: int, inbox_dir: Path) -> Dict[str, int]:
    inbox_dir.mkdir(parents=True, exist_ok=True)
    allowed_suffixes = {".pdf", ".png", ".jpg", ".jpeg"}
    sources = {
        "downloads": DATA_ROOT / "portal" / "downloads",
        "screenshots": DATA_ROOT / "portal" / "screenshots",
    }
    stats = {"portal_downloads_collected": 0, "portal_screenshots_collected": 0}
    index = load_portal_ingest_index()
    known_sources = index.get("sources", {})

    for source_type, root in sources.items():
        if not root.exists():
            continue
        for portal_dir in sorted([p for p in root.iterdir() if p.is_dir()]):
            year_dir = portal_dir / str(tax_year)
            if not year_dir.exists():
                continue
            for file_path in sorted([p for p in year_dir.iterdir() if p.is_file()]):
                if file_path.suffix.lower() not in allowed_suffixes:
                    continue
                marker = f"{file_path.resolve()}::{file_path.stat().st_size}::{file_path.stat().st_mtime_ns}"
                if marker in known_sources:
                    continue

                portal_name = safe_file_component(portal_dir.name)
                base_name = f"portal_{portal_name}_{file_path.name}"
                target = inbox_dir / base_name
                if target.exists():
                    stem = target.stem
                    suffix = target.suffix
                    counter = 1
                    while True:
                        candidate = inbox_dir / f"{stem}_{counter}{suffix}"
                        if not candidate.exists():
                            target = candidate
                            break
                        counter += 1

                shutil.copy2(file_path, target)
                known_sources[marker] = {
                    "source": str(file_path),
                    "target": str(target),
                    "copied_at": datetime.now(timezone.utc).isoformat(),
                }
                if source_type == "downloads":
                    stats["portal_downloads_collected"] += 1
                else:
                    stats["portal_screenshots_collected"] += 1

    index["sources"] = known_sources
    save_portal_ingest_index(index)
    return stats


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9._ -]", "", text)
    return text


def build_semantic_key(extracted: Dict[str, Any], row: Dict[str, Any]) -> str | None:
    merchant = _norm(extracted.get("merchant") or row.get("haendler"))
    invoice_date = _norm(extracted.get("invoice_date") or row.get("belegdatum"))
    invoice_number = _norm(extracted.get("invoice_number") or row.get("belegnummer"))
    product = _norm(extracted.get("line_items_summary") or row.get("produkt"))
    amount = _norm(extracted.get("gross_amount") or row.get("brutto_betrag"))

    if not merchant or not invoice_date:
        return None

    if invoice_number:
        return f"m={merchant}|d={invoice_date}|i={invoice_number}|p={product[:120]}"
    if product:
        return f"m={merchant}|d={invoice_date}|p={product[:120]}|a={amount}"
    return None


def process_documents(mode: str) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], int, int]:
    ollama = OllamaClient()
    classifier = TaxClassifier()
    ecodms = EcoDMSClient()
    steuerbox = SteuerboxUploader()

    rows: List[Dict[str, Any]] = []
    duplicates_skipped = 0
    duplicates_semantic_skipped = 0
    INBOX.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    files = sorted([p for p in INBOX.iterdir() if p.is_file()])
    total_files = len(files)
    print(f"Processing {total_files} file(s) from inbox")
    hash_index = load_hash_index()
    known_hashes: Dict[str, Any] = hash_index.get("hashes", {})
    semantic_index = load_semantic_index()
    known_semantic: Dict[str, Any] = semantic_index.get("keys", {})
    duplicate_dir = PROCESSED / "duplicates"
    duplicate_semantic_dir = PROCESSED / "duplicates_semantic"
    skipped_text_dir = PROCESSED / "skipped_text"
    duplicate_dir.mkdir(parents=True, exist_ok=True)
    duplicate_semantic_dir.mkdir(parents=True, exist_ok=True)
    skipped_text_dir.mkdir(parents=True, exist_ok=True)

    for idx, file_path in enumerate(files, start=1):
        file_started = datetime.now(timezone.utc)
        print(f"[{idx}/{total_files}] start: {file_path.name}")
        if file_path.suffix.lower() == ".txt" and not SETTINGS.process_text_docs:
            if mode == "run":
                target = skipped_text_dir / file_path.name
                if target.exists():
                    target = (
                        skipped_text_dir
                        / f"{file_path.stem}_{int(datetime.now().timestamp())}{file_path.suffix}"
                    )
                file_path.rename(target)
            continue

        try:
            current_hash = file_sha256(file_path)
        except FileNotFoundError:
            # Another process (e.g. Paperless consumer) may move the file while we're running.
            # Skip instead of crashing the whole batch.
            print(f"[{idx}/{total_files}] missing_file_skipped: {file_path}")
            continue
        if current_hash in known_hashes:
            duplicates_skipped += 1
            if mode == "run":
                target = duplicate_dir / file_path.name
                if target.exists():
                    target = duplicate_dir / f"{file_path.stem}_{int(datetime.now().timestamp())}{file_path.suffix}"
                file_path.rename(target)
            continue

        text = read_document_text(file_path)
        print(f"[{idx}/{total_files}] text_len={len(text)}")
        if not text:
            extracted = {
                "document_type": "unknown",
                "merchant": file_path.stem,
                "invoice_number": "",
                "invoice_date": "",
                "currency": SETTINGS.default_currency,
                "gross_amount": 0.0,
                "confidence": 0.0,
                "review_required": True,
                "tax_relevance_hint": "No OCR text available in pipeline",
            }
        else:
            try:
                extract_started = datetime.now(timezone.utc)
                extracted = ollama.extract(text)
                extract_elapsed = (datetime.now(timezone.utc) - extract_started).total_seconds()
                print(f"[{idx}/{total_files}] ollama_ok in {extract_elapsed:.1f}s")
            except Exception as exc:
                print(f"[{idx}/{total_files}] ollama_error: {exc}")
                extracted = {
                    "document_type": "unknown",
                    "merchant": file_path.stem,
                    "invoice_number": "",
                    "invoice_date": "",
                    "currency": SETTINGS.default_currency,
                    "gross_amount": 0.0,
                    "confidence": 0.0,
                    "review_required": True,
                    "tax_relevance_hint": f"Ollama error: {exc}",
                }

        category = classifier.classify(extracted, text)
        row = build_record(file_path, extracted, category)
        semantic_key = build_semantic_key(extracted, row)
        if semantic_key and semantic_key in known_semantic:
            duplicates_semantic_skipped += 1
            if mode == "run":
                target = duplicate_semantic_dir / file_path.name
                if target.exists():
                    target = (
                        duplicate_semantic_dir
                        / f"{file_path.stem}_{int(datetime.now().timestamp())}{file_path.suffix}"
                    )
                file_path.rename(target)
            continue
        rows.append(row)

        status = ecodms.upload_stub(
            file_path,
            {
                "ecodms_path": row["ecodms_pfad"],
                "ecodms_mainfolder_oid": row["ecodms_mainfolder_oid"],
                "ecodms_folder_oid": row["ecodms_folder_oid"],
                "subject": f"{row['haendler']} {row['belegdatum']} {row['datei']}",
                "remark": (
                    f"{row['haendler']} | "
                    f"{(row.get('produkt') or 'Produkt nicht erkannt')} | "
                    f"{row['brutto_betrag']} {row['waehrung']}"
                ),
                "upload_filename": (
                    f"{safe_file_component(str(row['haendler']))}_"
                    f"{safe_file_component(str(row.get('produkt') or 'produkt'))}_"
                    f"{safe_file_component(str(row['brutto_betrag']))}{file_path.suffix.lower()}"
                ),
                "invoice_date": row["belegdatum"],
            },
        )
        row["ecodms_upload_status"] = status
        row["steuerbox_upload_status"] = steuerbox.send_document(
            file_path,
            {
                "steuer_web_hint": row["steuer_web_hinweis"],
                "merchant": row["haendler"],
                "invoice_date": row["belegdatum"],
                "gross_amount": row["brutto_betrag"],
                "currency": row["waehrung"],
                "confidence": row["confidence"],
                "review_required": row["review_required"],
            },
        )
        known_hashes[current_hash] = {
            "file": file_path.name,
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }
        if semantic_key:
            known_semantic[semantic_key] = {
                "file": file_path.name,
                "stored_at": datetime.now(timezone.utc).isoformat(),
                "merchant": row.get("haendler", ""),
                "date": row.get("belegdatum", ""),
                "invoice_number": row.get("belegnummer", ""),
                "product": row.get("produkt", ""),
                "amount": row.get("brutto_betrag", ""),
            }

        if mode == "run":
            file_path.rename(PROCESSED / file_path.name)
        file_elapsed = (datetime.now(timezone.utc) - file_started).total_seconds()
        print(f"[{idx}/{total_files}] done in {file_elapsed:.1f}s: {file_path.name}")

    export_path = EXPORTS / SETTINGS.export_filename
    review_path = EXPORTS / SETTINGS.unsicher_filename

    existing_rows = read_csv_rows(export_path)
    if not existing_rows:
        existing_rows = load_last_nonempty_export_rows()
    if not existing_rows:
        # Fallback: use reviewed export as base if it is the only non-empty export left.
        existing_rows = read_csv_rows(EXPORTS / "steuer_2025_export_reviewed.csv")

    effective_rows = merge_rows(existing_rows, rows)
    if effective_rows:
        write_csv(export_path, effective_rows)
        review_rows = [r for r in effective_rows if str(r.get("review_required", "")).lower() in {"true", "1"}]
        write_csv(review_path, review_rows)
        save_last_nonempty_export_rows(effective_rows)

    (LOGS / "last_run.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    hash_index["hashes"] = known_hashes
    save_hash_index(hash_index)
    semantic_index["keys"] = known_semantic
    save_semantic_index(semantic_index)
    return rows, effective_rows, duplicates_skipped, duplicates_semantic_skipped


def write_status(
    mode: str,
    rows: List[Dict[str, Any]],
    collected_count: int,
    manual_drop_count: int,
    portal_download_count: int,
    portal_screenshot_count: int,
    duplicates_skipped: int,
    duplicates_semantic_skipped: int,
) -> None:
    recent = rows[-10:]
    open_docs = sorted([p.name for p in INBOX.iterdir() if p.is_file()]) if INBOX.exists() else []
    processed_docs = sorted([p.name for p in PROCESSED.iterdir() if p.is_file()]) if PROCESSED.exists() else []
    status = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "counts": {
            "documents_processed_this_run": len(rows),
            "documents_review_required": sum(
                1 for r in rows if str(r.get("review_required", "")).lower() in {"true", "1"}
            ),
            "ecodms_success": sum(
                1 for r in rows if str(r.get("ecodms_upload_status", "")).startswith("ok:")
            ),
            "ecodms_errors": sum(
                1 for r in rows if str(r.get("ecodms_upload_status", "")).startswith("error:")
            ),
            "steuerbox_sent": sum(
                1 for r in rows if str(r.get("steuerbox_upload_status", "")) == "sent"
            ),
            "steuerbox_errors": sum(
                1 for r in rows if str(r.get("steuerbox_upload_status", "")).startswith("error:")
            ),
            "imap_collected_attachments": collected_count,
            "manual_drop_collected_files": manual_drop_count,
            "portal_downloads_collected": portal_download_count,
            "portal_screenshots_collected": portal_screenshot_count,
            "duplicates_skipped_hash": duplicates_skipped,
            "duplicates_skipped_semantic": duplicates_semantic_skipped,
            "open_inbox_documents": len(open_docs),
            "processed_documents_total": len(processed_docs),
        },
        "last_10_documents": recent,
        "open_inbox_files": open_docs[:50],
        "notes": {
            "portal_invoice_fetch": "amazon/ebay/vodafone fetcher available via portal_fetcher.py",
            "mail_collection": "implemented via IMAP collector",
            "manual_drop": "implemented via MANUAL_DROP_DIR",
        },
    }
    (LOGS / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")


def run_compare(current_rows: List[Dict[str, Any]]) -> None:
    reference_dir = Path(SETTINGS.reference_2024_path)
    reference = load_reference_records(reference_dir) if reference_dir.exists() else []
    if not reference:
        (EXPORTS / "vergleich_2024_2025.csv").write_text("", encoding="utf-8")
        return
    compared = compare_category_totals(reference, current_rows)
    write_csv(EXPORTS / "vergleich_2024_2025.csv", compared)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tax document pipeline")
    parser.add_argument(
        "--mode", choices=["dry-run", "run", "process", "compare", "collect"], default="dry-run"
    )
    parser.add_argument("--tax-year", type=int, help="Explizites Steuerjahr, z. B. 2026")
    parser.add_argument(
        "--current-year",
        action="store_true",
        help="Aktuelles Kalenderjahr statt Vorjahr verarbeiten",
    )
    args = parser.parse_args()

    runtime_tax_year = _resolve_runtime_tax_year(args.tax_year, args.current_year)
    _apply_runtime_tax_year(runtime_tax_year)

    collected_count = 0
    manual_drop_count = 0
    portal_counts = {"portal_downloads_collected": 0, "portal_screenshots_collected": 0}
    if args.mode in {"collect", "run"}:
        dropped = collect_from_dropbox(Path(SETTINGS.manual_drop_dir), INBOX)
        manual_drop_count = len(dropped)
        if manual_drop_count:
            print(f"Collected {manual_drop_count} files from manual drop")

        if SETTINGS.portal_collect_enabled:
            portal_counts = collect_from_portal_sources(runtime_tax_year, INBOX)
            if portal_counts["portal_downloads_collected"] or portal_counts["portal_screenshots_collected"]:
                print(
                    "Collected portal files: "
                    f"downloads={portal_counts['portal_downloads_collected']} "
                    f"screenshots={portal_counts['portal_screenshots_collected']}"
                )

    if args.mode in {"collect", "run"}:
        saved = collect_attachments(INBOX)
        collected_count = len(saved)
        print(f"Collected {collected_count} attachments from IMAP")

    rows, effective_rows, duplicates_skipped, duplicates_semantic_skipped = process_documents(
        mode="run" if args.mode in {"run", "process"} else "dry-run"
    )
    if args.mode in {"dry-run", "compare", "run", "collect"}:
        run_compare(effective_rows if effective_rows else rows)
    write_status(
        args.mode,
        rows,
        collected_count,
        manual_drop_count,
        portal_counts["portal_downloads_collected"],
        portal_counts["portal_screenshots_collected"],
        duplicates_skipped,
        duplicates_semantic_skipped,
    )

    print(f"Processed {len(rows)} documents")
    print(f"Export: {EXPORTS / SETTINGS.export_filename}")
    print(f"Review: {EXPORTS / SETTINGS.unsicher_filename}")
    print(f"Compare: {EXPORTS / 'vergleich_2024_2025.csv'}")


if __name__ == "__main__":
    main()
