import argparse
import json
import os
import re
import hashlib
from datetime import datetime, timezone
from email import message_from_binary_file
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import List, Set, Tuple


ALLOWED_SUFFIXES = {".pdf", ".xml", ".csv", ".jpg", ".jpeg", ".png"}


def _safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or ""))
    text = re.sub(r"_+", "_", text).strip("._")
    return text or "mail"


def _clean_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_body_text(msg: Message, max_chars: int) -> str:
    chunks: List[str] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if "attachment" in str(part.get("Content-Disposition", "")).lower():
            continue
        ctype = (part.get_content_type() or "").lower()
        if ctype not in {"text/plain", "text/html"}:
            continue
        raw = part.get_payload(decode=True)
        if raw is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = raw.decode(charset, errors="ignore")
        except Exception:
            decoded = raw.decode("utf-8", errors="ignore")
        chunks.append(_clean_text(decoded) if ctype == "text/html" else decoded.strip())
    text = "\n\n".join(c for c in chunks if c)
    return text[:max_chars] if max_chars > 0 else text


def _parse_date_header(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _in_tax_year(dt: datetime | None, tax_year: int) -> bool:
    if dt is None:
        return True
    start = datetime(tax_year, 1, 1, tzinfo=timezone.utc)
    end = datetime(tax_year + 1, 1, 1, tzinfo=timezone.utc)
    return start <= dt.astimezone(timezone.utc) < end


def _load_state(state_path: Path) -> Set[str]:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        items = payload.get("processed", [])
        if isinstance(items, list):
            return {str(x) for x in items if str(x).strip()}
    except Exception:
        pass
    return set()


def _save_state(state_path: Path, processed: Set[str]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"processed": sorted(processed)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _marker_for(path: Path) -> str:
    st = path.stat()
    return f"{path.as_posix()}::{st.st_size}::{st.st_mtime_ns}"


def _sha1_bytes(payload: bytes) -> str:
    h = hashlib.sha1()
    h.update(payload)
    return h.hexdigest()


def collect_from_eml_tree(
    source_root: Path,
    target_dir: Path,
    source_name: str,
    tax_year: int,
    filter_re: re.Pattern | None,
    exclude_re: re.Pattern | None,
    body_fallback_enabled: bool,
    body_max_chars: int,
    max_messages: int,
    state_path: Path,
) -> Tuple[int, int]:
    """
    Returns: (messages_processed, files_written)
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    processed = _load_state(state_path)
    messages_processed = 0
    files_written = 0

    emls = sorted(source_root.rglob("*.eml"))
    for eml_path in emls:
        if max_messages > 0 and messages_processed >= max_messages:
            break

        marker = _marker_for(eml_path)
        if marker in processed:
            continue

        try:
            with eml_path.open("rb") as h:
                msg = message_from_binary_file(h)
        except Exception:
            processed.add(marker)
            continue

        dt = _parse_date_header(str(msg.get("Date", "")))
        if not _in_tax_year(dt, tax_year):
            processed.add(marker)
            continue

        sender = str(msg.get("From", "")).strip()
        subject = str(msg.get("Subject", "")).strip()
        body_text = _extract_body_text(msg, max_chars=body_max_chars)

        attachment_names: List[str] = []
        for part in msg.walk():
            if "attachment" not in str(part.get("Content-Disposition", "")).lower():
                continue
            fn = part.get_filename() or ""
            if fn:
                attachment_names.append(fn)

        haystack = "\n".join([sender, subject, body_text, " ".join(attachment_names)])
        if filter_re and not filter_re.search(haystack):
            processed.add(marker)
            continue
        header_haystack = "\n".join([sender, subject])
        if exclude_re and exclude_re.search(header_haystack):
            processed.add(marker)
            continue

        messages_processed += 1

        prefix = f"pst_{_safe_name(source_name)}_{_safe_name(eml_path.stem)}_"
        saved_for_message = 0
        for part in msg.walk():
            if "attachment" not in str(part.get("Content-Disposition", "")).lower():
                continue
            filename = part.get_filename()
            if not filename:
                continue
            filename = str(filename).replace("/", "_")
            suffix = Path(filename).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue

            # Avoid collisions and keep stable names.
            digest = _sha1_bytes(payload)[:10]
            out = target_dir / f"{prefix}{digest}_{_safe_name(filename)}"
            if out.exists():
                out = target_dir / f"{prefix}{digest}_{int(datetime.now().timestamp())}_{_safe_name(filename)}"
            out.write_bytes(payload)
            files_written += 1
            saved_for_message += 1

        if saved_for_message == 0 and body_fallback_enabled and body_text.strip():
            sender_hint = _safe_name(sender.split("<")[0] if sender else "mail")
            subject_hint = _safe_name(subject)[:80]
            out = target_dir / f"mail_{_safe_name(source_name)}_{_safe_name(eml_path.stem)}_{sender_hint}_{subject_hint}.txt"
            if out.exists():
                out = target_dir / f"{_safe_name(source_name)}_{_safe_name(eml_path.stem)}_{int(out.stat().st_mtime)}_{out.name}"
            out.write_text(f"From: {sender}\nSubject: {subject}\nDate: {msg.get('Date','')}\n\n{body_text}\n", encoding="utf-8", errors="ignore")
            files_written += 1

        processed.add(marker)
        if messages_processed % 200 == 0:
            _save_state(state_path, processed)

    _save_state(state_path, processed)
    return messages_processed, files_written


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect receipts from readpst-exported .eml trees")
    parser.add_argument("--source-root", action="append", required=True, help="Root dir containing .eml files")
    parser.add_argument("--target-dir", default="/data/pipeline_inbox_pst_001", help="Pipeline inbox target dir")
    parser.add_argument("--source-name", default="outlook", help="Prefix for collected filenames/state")
    parser.add_argument("--tax-year", type=int, default=int(os.getenv("TAX_YEAR", "2025")))
    parser.add_argument("--max-messages", type=int, default=int(os.getenv("MAIL_MAX_MESSAGES", "0")))
    parser.add_argument("--body-fallback", default=os.getenv("MAIL_BODY_FALLBACK", "true"))
    parser.add_argument("--body-max-chars", type=int, default=int(os.getenv("MAIL_BODY_MAX_CHARS", "60000")))
    parser.add_argument("--filter-regex", default=os.getenv("MAIL_FILTER_REGEX", "").strip())
    parser.add_argument("--exclude-regex", default=os.getenv("MAIL_EXCLUDE_REGEX", "").strip())
    args = parser.parse_args()

    filter_re = re.compile(args.filter_regex, re.IGNORECASE) if args.filter_regex else None
    exclude_re = re.compile(args.exclude_regex, re.IGNORECASE) if args.exclude_regex else None
    body_fallback_enabled = str(args.body_fallback).strip().lower() in {"1", "true", "yes", "on"}

    target_dir = Path(args.target_dir)
    state_path = Path("/data/logs") / f"pst_state_{_safe_name(args.source_name)}.json"

    total_msgs = 0
    total_files = 0
    for root in args.source_root:
        msgs, files = collect_from_eml_tree(
            source_root=Path(root),
            target_dir=target_dir,
            source_name=args.source_name,
            tax_year=args.tax_year,
            filter_re=filter_re,
            exclude_re=exclude_re,
            body_fallback_enabled=body_fallback_enabled,
            body_max_chars=args.body_max_chars,
            max_messages=args.max_messages,
            state_path=state_path,
        )
        total_msgs += msgs
        total_files += files

    print(f"done source_name={args.source_name} tax_year={args.tax_year} messages={total_msgs} files={total_files} target={target_dir}")


if __name__ == "__main__":
    main()

