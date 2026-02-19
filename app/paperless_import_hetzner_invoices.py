import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from paperless_importer import PaperlessClient


def _created_date_from_filename(filename: str) -> Optional[str]:
    # Example: Hetzner_2025-01-15_087000143883.pdf
    m = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", filename)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc).date().isoformat()
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Hetzner invoices into Paperless with Steuerjahr tags")
    parser.add_argument(
        "--source-dir",
        default="/manual-drop",
        help="Directory containing Hetzner invoice PDFs (default: /manual-drop)",
    )
    parser.add_argument("--tax-year", type=int, default=2025)
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    if not source_dir.exists():
        raise SystemExit(f"source dir not found: {source_dir}")

    pattern = f"Hetzner_{args.tax_year}-*.pdf"
    files = sorted(source_dir.glob(pattern))
    if not files:
        print(f"no files matched {pattern} in {source_dir}")
        return

    client = PaperlessClient()

    doc_type_id = client.get_or_create_doc_type("Portal Import")
    storage_path_id = client.get_or_create_storage_path(
        name="ecoDMS: Marcus/Steuererklärungen/2026 für Steuerjahr 2025",
        path="ecoDMS/Marcus/Steuererklärungen/2026 für Steuerjahr 2025",
    )
    tag_ids = [
        client.get_or_create_tag("source:portal"),
        client.get_or_create_tag("source:hetzner"),
        client.get_or_create_tag(f"steuerjahr:{args.tax_year}"),
        client.get_or_create_tag("ecodms/Marcus/Steuererklärungen/2026 für Steuerjahr 2025"),
    ]

    uploaded = 0
    skipped_duplicates = 0
    failed = 0
    for file_path in files:
        title = file_path.stem
        created = _created_date_from_filename(file_path.name)
        if not created:
            # fall back to mtime
            created = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc).date().isoformat()
        try:
            task = client.upload_document(
                file_path=file_path,
                title=title,
                created_date=created,
                document_type_id=doc_type_id,
                tag_ids=tag_ids,
                storage_path_id=storage_path_id,
                upload_filename=file_path.name,
            )
            print(f"[OK] {file_path.name} -> task={task}")
            uploaded += 1
        except requests.HTTPError as exc:
            body = ""
            if getattr(exc, "response", None) is not None:
                try:
                    body = exc.response.text or ""
                except Exception:
                    body = ""
            if "duplicate" in body.lower():
                print(f"[SKIP_DUP] {file_path.name}")
                skipped_duplicates += 1
            else:
                print(f"[ERROR] {file_path.name}: {exc}")
                failed += 1
        except Exception as exc:
            print(f"[ERROR] {file_path.name}: {exc}")
            failed += 1

    print(
        f"done files={len(files)} uploaded={uploaded} skipped_duplicates={skipped_duplicates} failed={failed}"
    )


if __name__ == "__main__":
    main()

