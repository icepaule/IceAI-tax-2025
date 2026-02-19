import argparse
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from paperless_importer import PaperlessClient


_VF_RX = re.compile(r"vodafone|kabel deutschland|www\.vodafone\.de", re.IGNORECASE)


def _pdftotext_head(path: Path, max_chars: int = 8000) -> str:
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        text = (out.stdout or "")[:max_chars]
        return text
    except Exception:
        return ""


def _parse_german_date(text: str) -> Optional[str]:
    # Typical: "Datum 12. Februar 2025"
    # Also accept: "Datum           12. Februar 2025"
    m = re.search(
        r"\bDatum\s+(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+)\s+(20\d{2})\b",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    day = int(m.group(1))
    month_name = m.group(2).strip().lower()
    year = int(m.group(3))
    months = {
        "januar": 1,
        "februar": 2,
        "märz": 3,
        "maerz": 3,
        "april": 4,
        "mai": 5,
        "juni": 6,
        "juli": 7,
        "august": 8,
        "september": 9,
        "oktober": 10,
        "november": 11,
        "dezember": 12,
    }
    month = months.get(month_name)
    if not month:
        return None
    try:
        return datetime(year, month, day, tzinfo=timezone.utc).date().isoformat()
    except Exception:
        return None


def _parse_invoice_number(text: str) -> str:
    m = re.search(r"\bRechnungsnummer\s+(\d{6,})\b", text, re.IGNORECASE)
    if not m:
        return ""
    return m.group(1).strip()


def _build_title(text: str, fallback: str) -> str:
    inv = _parse_invoice_number(text)
    date = _parse_german_date(text)
    if inv and date:
        return f"Vodafone Rechnung {date} #{inv}"
    if inv:
        return f"Vodafone Rechnung #{inv}"
    if date:
        return f"Vodafone Rechnung {date}"
    return fallback


def main() -> None:
    parser = argparse.ArgumentParser(description="Import manually downloaded Vodafone invoices into Paperless")
    parser.add_argument(
        "--source-dir",
        default="/manual-drop",
        help="Directory containing PDFs (default: /manual-drop)",
    )
    parser.add_argument("--tax-year", type=int, default=2025)
    parser.add_argument("--all-pdfs", action="store_true", help="Import all PDFs in source dir (skip content check)")
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    if not source_dir.exists():
        raise SystemExit(f"source dir not found: {source_dir}")

    pdfs = sorted([p for p in source_dir.glob("*.pdf") if p.is_file()])
    if not pdfs:
        print(f"no pdfs in {source_dir}")
        return

    candidates: list[tuple[Path, str]] = []
    for path in pdfs:
        if path.name.startswith("Hetzner_"):
            continue
        head = _pdftotext_head(path)
        if args.all_pdfs or _VF_RX.search(head or ""):
            candidates.append((path, head))

    if not candidates:
        print("no vodafone-like pdfs detected")
        return

    client = PaperlessClient()
    doc_type_id = client.get_or_create_doc_type("Portal Import")
    storage_path_id = client.get_or_create_storage_path(
        name="ecoDMS: Marcus/Steuererklärungen/2026 für Steuerjahr 2025",
        path="ecoDMS/Marcus/Steuererklärungen/2026 für Steuerjahr 2025",
    )
    tag_ids = [
        client.get_or_create_tag("source:portal"),
        client.get_or_create_tag("source:vodafone"),
        client.get_or_create_tag(f"steuerjahr:{args.tax_year}"),
        client.get_or_create_tag("ecodms/Marcus/Steuererklärungen/2026 für Steuerjahr 2025"),
    ]

    uploaded = 0
    skipped_duplicates = 0
    failed = 0
    for path, head in candidates:
        created = _parse_german_date(head)
        if not created:
            created = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()
        title = _build_title(head, path.stem)
        try:
            task = client.upload_document(
                file_path=path,
                title=title,
                created_date=created,
                document_type_id=doc_type_id,
                tag_ids=tag_ids,
                storage_path_id=storage_path_id,
                upload_filename=path.name,
            )
            print(f"[OK] {path.name} -> task={task}")
            uploaded += 1
        except requests.HTTPError as exc:
            body = ""
            if getattr(exc, "response", None) is not None:
                try:
                    body = exc.response.text or ""
                except Exception:
                    body = ""
            if "duplicate" in body.lower():
                print(f"[SKIP_DUP] {path.name}")
                skipped_duplicates += 1
            else:
                print(f"[ERROR] {path.name}: {exc}")
                failed += 1
        except Exception as exc:
            print(f"[ERROR] {path.name}: {exc}")
            failed += 1

    print(
        f"done pdfs={len(pdfs)} candidates={len(candidates)} uploaded={uploaded} skipped_duplicates={skipped_duplicates} failed={failed}"
    )


if __name__ == "__main__":
    main()
