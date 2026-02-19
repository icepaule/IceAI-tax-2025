import argparse
import csv
import json
import re
import html
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from zipfile import ZipFile

@dataclass
class KlassRecord:
    docid: str
    mainfolder_oid: str
    folder_oid: str
    revision: str
    cdate: str
    docart: str


@dataclass
class ExportRecord:
    docid: str
    archive_rel_path: str
    original_name: str
    xml_date: str
    xml_doc_type: str


def _parse_revision(rev: str) -> Tuple[int, ...]:
    parts: List[int] = []
    for p in str(rev or "").split("."):
        if p.isdigit():
            parts.append(int(p))
        else:
            if not parts:
                parts.append(0)
            break
    return tuple(parts) if parts else (0,)


def parse_systemordner_paths(backup_sql: Path) -> Dict[str, str]:
    rows: List[Dict[str, str]] = []
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
            parentid = parts[5].strip()
            if oid and name:
                rows.append({"oid": oid, "name": name, "parentid": parentid})

    by_oid = {r["oid"]: r for r in rows}

    def resolve_path(oid: str, seen: Optional[set] = None) -> str:
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

    out: Dict[str, str] = {}
    for oid in by_oid:
        out[oid] = resolve_path(oid)
    return out


def parse_klassifizierung_latest(backup_sql: Path) -> Dict[str, KlassRecord]:
    out: Dict[str, KlassRecord] = {}
    in_copy = False
    with backup_sql.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not in_copy:
                if line.startswith("COPY classify01.klassifizierung "):
                    in_copy = True
                continue
            if line.strip() == r"\.":
                break
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 14:
                continue

            docid = parts[1].strip()
            if not docid or docid == r"\N":
                continue

            rec = KlassRecord(
                docid=docid,
                mainfolder_oid="" if parts[2].strip() == r"\N" else parts[2].strip(),
                folder_oid="" if parts[3].strip() == r"\N" else parts[3].strip(),
                revision="" if parts[6].strip() == r"\N" else parts[6].strip(),
                docart="" if parts[7].strip() == r"\N" else parts[7].strip(),
                cdate="" if parts[9].strip() == r"\N" else parts[9].strip(),
            )

            old = out.get(docid)
            if old is None or _parse_revision(rec.revision) >= _parse_revision(old.revision):
                out[docid] = rec
    return out


def _safe_text(node: Optional[ET.Element], xpath: str) -> str:
    if node is None:
        return ""
    child = node.find(xpath)
    return (child.text or "").strip() if child is not None and child.text is not None else ""


def parse_export_xml_from_zip(export_zip: Path) -> Dict[str, ExportRecord]:
    out: Dict[str, ExportRecord] = {}
    with ZipFile(export_zip, "r") as zf:
        xml_member = "offline_export/archive/export.xml"
        raw = zf.read(xml_member).decode("utf-8", errors="ignore")
        # sanitize common invalid chars seen in ecoDMS export
        raw = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", raw)

        doc_blocks = re.finditer(r"<document\s+docid='(\d+)'>(.*?)</document>", raw, flags=re.S)
        for m in doc_blocks:
            docid = m.group(1).strip()
            body = m.group(2)

            files_match = re.search(r"<files\s+([^>]*?)>", body, flags=re.S)
            if not files_match:
                continue
            files_attrs = files_match.group(1)

            rel_match = re.search(r"filePath='([^']+)'", files_attrs)
            orig_match = re.search(r"origname='([^']*)'", files_attrs)
            rel = rel_match.group(1).strip() if rel_match else ""
            orig = html.unescape(orig_match.group(1).strip()) if orig_match else ""
            if not rel:
                continue

            ver_match = re.search(r"<Version>(.*?)</Version>", body, flags=re.S)
            ver_body = ver_match.group(1) if ver_match else ""
            date_match = re.search(r"<datum>(.*?)</datum>", ver_body, flags=re.S)
            dtype_match = re.search(r"<dokumentenart>(.*?)</dokumentenart>", ver_body, flags=re.S)
            xml_date = html.unescape(date_match.group(1).strip()) if date_match else ""
            xml_doc_type = html.unescape(dtype_match.group(1).strip()) if dtype_match else ""

            out[docid] = ExportRecord(
                docid=docid,
                archive_rel_path=f"offline_export/archive/{rel}",
                original_name=orig,
                xml_date=xml_date,
                xml_doc_type=xml_doc_type,
            )
    return out


def normalize_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return datetime.utcnow().date().isoformat()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return datetime.utcnow().date().isoformat()


def build_tags(folder_path: str, folder_oid: str, docid: str) -> List[str]:
    tags = ["source:ecodms", "migration:ecodms"]
    if folder_oid:
        tags.append(f"ecodms_oid:{folder_oid}")
    if docid:
        tags.append(f"ecodms_docid:{docid}")

    parts = [p.strip() for p in folder_path.split("/") if p.strip()]
    prefix: List[str] = []
    for part in parts:
        prefix.append(part)
        tags.append("ecodms/" + "/".join(prefix))
    return tags


def build_manifest_rows(
    export_records: Dict[str, ExportRecord],
    klass_latest: Dict[str, KlassRecord],
    folder_paths: Dict[str, str],
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for docid, exp in export_records.items():
        klass = klass_latest.get(docid)
        folder_oid = klass.folder_oid if klass else ""
        mainfolder_oid = klass.mainfolder_oid if klass else ""
        folder_path = folder_paths.get(folder_oid, "")
        created = normalize_date(klass.cdate if klass else exp.xml_date)
        doc_type = (exp.xml_doc_type or "ecoDMS Import").strip() or "ecoDMS Import"
        title = Path(exp.original_name or Path(exp.archive_rel_path).name).stem

        rows.append(
            {
                "docid": docid,
                "archive_rel_path": exp.archive_rel_path,
                "original_name": exp.original_name,
                "title": title,
                "created": created,
                "doc_type": doc_type,
                "mainfolder_oid": mainfolder_oid,
                "folder_oid": folder_oid,
                "folder_path": folder_path,
                "tags_json": json.dumps(build_tags(folder_path, folder_oid, docid), ensure_ascii=False),
            }
        )

    rows.sort(key=lambda r: int(r["docid"]))
    return rows


def write_manifest(rows: List[Dict[str, str]], manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "docid",
        "archive_rel_path",
        "original_name",
        "title",
        "created",
        "doc_type",
        "mainfolder_oid",
        "folder_oid",
        "folder_path",
        "tags_json",
    ]
    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def import_from_manifest(
    export_zip: Path,
    rows: Iterable[Dict[str, str]],
    dry_run: bool,
    max_files: int,
) -> None:
    selected = list(rows)
    if max_files > 0:
        selected = selected[:max_files]

    print(f"Import candidates: {len(selected)}")
    if not selected:
        return

    # Import lazily so manifest-only mode can run without Paperless dependencies.
    client = None
    if not dry_run:
        from paperless_importer import PaperlessClient

        client = PaperlessClient()
    doc_type_cache: Dict[str, int] = {}
    storage_path_cache: Dict[str, int] = {}

    success = 0
    failed = 0
    skipped_duplicates = 0
    skipped_existing = 0
    skipped_unsupported = 0
    unsupported_rows: List[Dict[str, str]] = []

    existing_docids: set[str] = set()
    existing_keys: set[tuple[str, str, str]] = set()
    if not dry_run and client is not None:
        # Build a lightweight index of already imported ecoDMS docs to keep reruns idempotent.
        tag_name_by_id: Dict[int, str] = {}
        next_url = f"{client.base_url}/api/tags/?page_size=500"
        while next_url:
            resp = client.session.get(next_url, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
            for tag in payload.get("results", []):
                tid = int(tag.get("id"))
                tag_name_by_id[tid] = str(tag.get("name", ""))
            next_url = payload.get("next")

        next_url = f"{client.base_url}/api/documents/?tags__name__iexact=migration:ecodms&page_size=200"
        while next_url:
            resp = client.session.get(next_url, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
            for doc in payload.get("results", []):
                title = str(doc.get("title", "")).strip().lower()
                created = str(doc.get("created", "")).strip()[:10]
                oid = ""
                docid = ""
                for tid in doc.get("tags", []):
                    tname = tag_name_by_id.get(int(tid), "")
                    if tname.startswith("ecodms_docid:"):
                        docid = tname.split(":", 1)[1].strip()
                        continue
                    if tname.startswith("ecodms_oid:"):
                        oid = tname.split(":", 1)[1].strip()
                if docid:
                    existing_docids.add(docid)
                elif title and created and oid:
                    existing_keys.add((title, created, oid))
            next_url = payload.get("next")
    with ZipFile(export_zip, "r") as zf:
        names = set(zf.namelist())
        for idx, row in enumerate(selected, start=1):
            rel = row["archive_rel_path"]
            if rel not in names:
                failed += 1
                print(f"[{idx}] [MISSING] {rel}")
                continue

            try:
                # Skip known unsupported formats early to avoid Paperless consumer noise.
                name_for_suffix = str(row.get("original_name") or rel)
                suffix = Path(name_for_suffix).suffix.lower()
                if suffix in {".msg", ".doc", ".xls", ".ppt"} or not suffix:
                    skipped_unsupported += 1
                    unsupported_rows.append(
                        {
                            "docid": str(row.get("docid", "")),
                            "original_name": str(row.get("original_name", "")),
                            "archive_rel_path": rel,
                            "folder_path": str(row.get("folder_path", "")),
                            "reason": f"unsupported_suffix:{suffix or '(none)'}",
                        }
                    )
                    print(
                        f"[{idx}] [SKIP_UNSUPPORTED] docid={row.get('docid')} file={name_for_suffix} reason=suffix"
                    )
                    continue

                tags = json.loads(row.get("tags_json", "[]"))
                created = str(row.get("created", "")).strip()[:10]
                folder_oid = str(row.get("folder_oid", "")).strip()
                docid = str(row.get("docid", "")).strip()
                key = (str(row["title"]).strip().lower(), created, folder_oid)
                if (docid and docid in existing_docids) or key in existing_keys:
                    skipped_existing += 1
                    print(f"[{idx}] [SKIP_EXISTING] docid={row['docid']} title='{row['title']}'")
                    continue
                if dry_run:
                    print(
                        f"[{idx}] [DRY] docid={row['docid']} file={rel} path='{row['folder_path']}' doc_type='{row['doc_type']}' tags={len(tags)}"
                    )
                    success += 1
                    continue

                dtype = row["doc_type"]
                if dtype not in doc_type_cache:
                    doc_type_cache[dtype] = client.get_or_create_doc_type(dtype)  # type: ignore[union-attr]
                doc_type_id = doc_type_cache[dtype]
                tag_ids = [client.get_or_create_tag(t) for t in tags]  # type: ignore[union-attr]
                folder_path = (row.get("folder_path") or "").strip()
                storage_path_id: Optional[int] = None
                if folder_path:
                    sp_name = f"ecoDMS: {folder_path}"
                    sp_path = f"ecoDMS/{folder_path}"
                    if sp_name not in storage_path_cache:
                        storage_path_cache[sp_name] = client.get_or_create_storage_path(  # type: ignore[union-attr]
                            sp_name, sp_path
                        )
                    storage_path_id = storage_path_cache[sp_name]

                upload_name = (row.get("original_name") or Path(rel).name).strip()
                tmp_suffix = Path(upload_name).suffix or ".bin"
                with zf.open(rel) as src, tempfile.NamedTemporaryFile(suffix=tmp_suffix) as tmp:
                    tmp.write(src.read())
                    tmp.flush()
                    task_id = client.upload_document(
                        file_path=Path(tmp.name),
                        title=row["title"],
                        created_date=row["created"],
                        document_type_id=doc_type_id,
                        tag_ids=tag_ids,
                        storage_path_id=storage_path_id,
                        upload_filename=upload_name,
                    )
                print(f"[{idx}] [OK] docid={row['docid']} task={task_id}")
                if docid:
                    existing_docids.add(docid)
                existing_keys.add(key)
                success += 1
            except Exception as exc:
                detail = ""
                resp = getattr(exc, "response", None)
                if resp is not None:
                    try:
                        detail = f" body={resp.text[:500]}"
                    except Exception:
                        detail = ""
                msg = f"{exc}{detail}".lower()
                if "duplicate" in msg:
                    skipped_duplicates += 1
                    print(f"[{idx}] [SKIP_DUPLICATE] docid={row['docid']}: {exc}{detail}")
                elif "not supported" in msg or "file type" in msg:
                    skipped_unsupported += 1
                    unsupported_rows.append(
                        {
                            "docid": str(row.get("docid", "")),
                            "original_name": str(row.get("original_name", "")),
                            "archive_rel_path": rel,
                            "folder_path": str(row.get("folder_path", "")),
                            "reason": "paperless_rejected:not_supported",
                        }
                    )
                    print(f"[{idx}] [SKIP_UNSUPPORTED] docid={row['docid']}: {exc}{detail}")
                else:
                    failed += 1
                    print(f"[{idx}] [ERROR] docid={row['docid']}: {exc}{detail}")

    if unsupported_rows:
        out_path = Path("/data/ecodms-export/ecodms_unsupported.csv")
        try:
            import csv as _csv

            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("w", newline="", encoding="utf-8") as f:
                w = _csv.DictWriter(
                    f,
                    fieldnames=["docid", "original_name", "archive_rel_path", "folder_path", "reason"],
                )
                w.writeheader()
                w.writerows(unsupported_rows)
            print(f"unsupported_written={out_path} count={len(unsupported_rows)}")
        except Exception as exc:
            print(f"unsupported_write_failed: {exc}")

    print(
        "Import done. "
        f"success={success}, failed={failed}, skipped_duplicates={skipped_duplicates}, "
        f"skipped_existing={skipped_existing}, skipped_unsupported={skipped_unsupported}, dry_run={dry_run}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import ecoDMS offline export ZIP into Paperless with hierarchy tags")
    parser.add_argument(
        "--backup-sql",
        default="/data/ecodms-export/raw/backup.sql",
        help="Path to ecoDMS backup.sql",
    )
    parser.add_argument(
        "--export-zip",
        default="/data/ecodms-export/raw/workdir/exports/f436f12b-2033-498f-9a1d-758bc777d8d6.zip",
        help="Path to ecoDMS offline export zip containing archive/export.xml",
    )
    parser.add_argument(
        "--manifest",
        default="/data/ecodms-export/ecodms_paperless_manifest.csv",
        help="Output manifest CSV path",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not upload, only show planned actions")
    parser.add_argument("--max-files", type=int, default=0, help="Limit files for import (0 = all)")
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="Build manifest only, skip import",
    )
    args = parser.parse_args()

    backup_sql = Path(args.backup_sql)
    export_zip = Path(args.export_zip)
    manifest_path = Path(args.manifest)

    if not backup_sql.exists():
        raise RuntimeError(f"backup.sql not found: {backup_sql}")
    if not export_zip.exists():
        raise RuntimeError(f"export zip not found: {export_zip}")

    folder_paths = parse_systemordner_paths(backup_sql)
    klass_latest = parse_klassifizierung_latest(backup_sql)
    export_records = parse_export_xml_from_zip(export_zip)
    rows = build_manifest_rows(export_records, klass_latest, folder_paths)

    write_manifest(rows, manifest_path)
    print(f"manifest_rows={len(rows)}")
    print(f"manifest_path={manifest_path}")

    if args.manifest_only:
        return

    import_from_manifest(export_zip, rows, dry_run=args.dry_run, max_files=args.max_files)


if __name__ == "__main__":
    main()
