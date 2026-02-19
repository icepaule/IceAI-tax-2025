import email
import imaplib
import os
import re
import shlex
import socket
import ssl
import json
from email.header import decode_header
from email.message import Message
from pathlib import Path
from typing import List, Set

import yaml

from config import SETTINGS

ALLOWED_SUFFIXES = {".pdf", ".txt", ".xml", ".csv", ".jpg", ".jpeg", ".png"}


def _decode(value: str) -> str:
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="ignore"))
        else:
            out.append(text)
    return "".join(out)


def _clean_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    text = re.sub(r"_+", "_", text).strip("._")
    return text or "mail"


def _extract_body_text(msg: Message, max_chars: int) -> str:
    chunks: List[str] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if "attachment" in str(part.get("Content-Disposition", "")).lower():
            continue
        content_type = (part.get_content_type() or "").lower()
        if content_type not in {"text/plain", "text/html"}:
            continue
        raw = part.get_payload(decode=True)
        if raw is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = raw.decode(charset, errors="ignore")
        except Exception:
            decoded = raw.decode("utf-8", errors="ignore")
        chunks.append(_clean_text(decoded) if content_type == "text/html" else decoded.strip())
    text = "\n\n".join(c for c in chunks if c)
    if max_chars > 0:
        return text[:max_chars]
    return text


def _load_accounts() -> List[dict]:
    accounts: List[dict] = []
    accounts_file = Path(SETTINGS.imap_accounts_file)
    if accounts_file.exists():
        try:
            loaded = yaml.safe_load(accounts_file.read_text(encoding="utf-8")) or {}
            rows = loaded.get("accounts", loaded) if isinstance(loaded, dict) else loaded
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        accounts.append(row)
        except Exception as exc:
            print(f"IMAP accounts file warning: {exc}")

    if accounts:
        return accounts

    if SETTINGS.imap_host and SETTINGS.imap_user and SETTINGS.imap_password:
        return [
            {
                "name": SETTINGS.imap_user,
                "host": SETTINGS.imap_host,
                "port": SETTINGS.imap_port,
                "user": SETTINGS.imap_user,
                "password": SETTINGS.imap_password,
                "use_ssl": SETTINGS.imap_use_ssl,
                "folder": SETTINGS.imap_folder,
                "search": SETTINGS.imap_search,
                "max_messages": SETTINGS.imap_max_messages,
                "filter_regex": SETTINGS.imap_filter_regex,
                "exclude_regex": SETTINGS.imap_exclude_regex,
                "body_fallback_enabled": SETTINGS.imap_body_fallback_enabled,
                "body_max_chars": SETTINGS.imap_body_max_chars,
                "mark_seen": SETTINGS.imap_mark_seen,
            }
        ]
    return []


def _bool_account(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_env_value(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("${") and text.endswith("}"):
        return os.getenv(text[2:-1], "")
    return text


def _load_imap_state(account_name: str) -> Set[str]:
    path = Path("/data/logs") / f"imap_state_{_safe_name(account_name)}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload.get("processed_uids", [])
        if isinstance(items, list):
            return {str(x) for x in items if str(x).strip()}
    except Exception:
        pass
    return set()


def _save_imap_state(account_name: str, processed_uids: Set[str]) -> None:
    path = Path("/data/logs") / f"imap_state_{_safe_name(account_name)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"processed_uids": sorted(processed_uids)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_attachments(target_dir: Path) -> List[str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    saved: List[str] = []
    accounts = _load_accounts()
    if not accounts:
        return []

    for account in accounts:
        host = _resolve_env_value(account.get("host", ""))
        user = _resolve_env_value(account.get("user", ""))
        password = _resolve_env_value(account.get("password", ""))
        if not password:
            password = _resolve_env_value(account.get("password_env", ""))
        if (
            not password
            and host == SETTINGS.imap_host
            and user == SETTINGS.imap_user
            and SETTINGS.imap_password
        ):
            password = SETTINGS.imap_password
        if not host or not user or not password:
            continue
        raw_port = _resolve_env_value(account.get("port", SETTINGS.imap_port))
        port = int(raw_port or SETTINGS.imap_port)
        use_ssl = _bool_account(account.get("use_ssl"), SETTINGS.imap_use_ssl)
        use_starttls = _bool_account(account.get("use_starttls"), False)
        starttls_verify = _bool_account(account.get("starttls_verify"), True)
        # Allow `${ENV}` indirections in YAML for these string fields.
        folder = _resolve_env_value(account.get("folder", SETTINGS.imap_folder)) or SETTINGS.imap_folder or "INBOX"
        search = _resolve_env_value(account.get("search", SETTINGS.imap_search)) or SETTINGS.imap_search or "ALL"
        max_messages = int(account.get("max_messages", SETTINGS.imap_max_messages))
        filter_regex = _resolve_env_value(account.get("filter_regex", SETTINGS.imap_filter_regex)) or str(
            SETTINGS.imap_filter_regex
        )
        exclude_regex = _resolve_env_value(account.get("exclude_regex", SETTINGS.imap_exclude_regex)) or str(
            SETTINGS.imap_exclude_regex
        )
        body_fallback_enabled = _bool_account(
            account.get("body_fallback_enabled"), SETTINGS.imap_body_fallback_enabled
        )
        body_max_chars = int(account.get("body_max_chars", SETTINGS.imap_body_max_chars))
        mark_seen = _bool_account(account.get("mark_seen"), SETTINGS.imap_mark_seen)
        account_name = str(account.get("name", user))
        processed_uids = _load_imap_state(account_name)

        client = None
        try:
            socket.setdefaulttimeout(SETTINGS.imap_timeout_seconds)
            if use_ssl:
                client = imaplib.IMAP4_SSL(host, port, timeout=SETTINGS.imap_timeout_seconds)
            else:
                client = imaplib.IMAP4(host, port, timeout=SETTINGS.imap_timeout_seconds)
                if use_starttls:
                    if starttls_verify:
                        client.starttls()
                    else:
                        insecure_ctx = ssl.create_default_context()
                        insecure_ctx.check_hostname = False
                        insecure_ctx.verify_mode = ssl.CERT_NONE
                        client.starttls(ssl_context=insecure_ctx)
            client.login(user, password)
            client.select(folder, readonly=not mark_seen)

            try:
                criteria = shlex.split(search) if search.strip() else ["ALL"]
            except ValueError:
                criteria = [search]
            # Prefer UID-based scanning so batches can advance without re-reading the newest messages.
            # Fallback to sequence ids if UID search isn't supported.
            use_uid = True
            status, message_ids = client.uid("search", None, *criteria)
            if status != "OK":
                use_uid = False
                status, message_ids = client.search(None, *criteria)
            if status != "OK":
                continue

            ids = message_ids[0].split()

            # Apply batch window on unprocessed UIDs/IDs from the tail (most recent first).
            if use_uid:
                ids = [i for i in ids if i.decode(errors="ignore") not in processed_uids]
            if max_messages > 0 and len(ids) > max_messages:
                ids = ids[-max_messages:]

            filter_pattern = re.compile(filter_regex, re.IGNORECASE) if filter_regex.strip() else None
            exclude_pattern = re.compile(exclude_regex, re.IGNORECASE) if exclude_regex.strip() else None

            for msg_id in ids:
                if use_uid:
                    status, msg_data = client.uid("fetch", msg_id, "(RFC822)")
                else:
                    status, msg_data = client.fetch(msg_id, "(RFC822)")
                if status != "OK":
                    continue

                raw_message = None
                for item in msg_data or []:
                    if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                        raw_message = bytes(item[1])
                        break
                if not raw_message:
                    continue

                msg = email.message_from_bytes(raw_message)
                sender = _decode(str(msg.get("From", "")))
                subject = _decode(str(msg.get("Subject", "")))
                body_text = _extract_body_text(msg, max_chars=body_max_chars)

                attachment_names: List[str] = []
                for part in msg.walk():
                    if "attachment" not in str(part.get("Content-Disposition", "")).lower():
                        continue
                    filename = part.get_filename()
                    if filename:
                        attachment_names.append(_decode(filename))

                haystack = "\n".join([sender, subject, body_text, " ".join(attachment_names)])
                if filter_pattern and not filter_pattern.search(haystack):
                    continue
                header_haystack = "\n".join([sender, subject])
                if exclude_pattern and exclude_pattern.search(header_haystack):
                    continue

                msg_key = msg_id.decode(errors="ignore")

                # Prefix attachments with stable metadata so we can later map them back to IMAP batches.
                # This also reduces filename collisions across messages.
                imap_prefix = f"imap_{_safe_name(account_name)}_{msg_key}_"

                saved_for_message = 0
                for part in msg.walk():
                    content_disposition = str(part.get("Content-Disposition", ""))
                    if "attachment" not in content_disposition.lower():
                        continue

                    filename = part.get_filename()
                    if not filename:
                        continue

                    filename = _decode(filename).replace("/", "_")
                    suffix = Path(filename).suffix.lower()
                    if suffix not in ALLOWED_SUFFIXES:
                        continue

                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue

                    output = target_dir / f"{imap_prefix}{filename}"
                    if output.exists():
                        output = target_dir / f"{imap_prefix}{int(datetime.now().timestamp())}_{filename}"
                    output.write_bytes(payload)
                    saved.append(str(output))
                    saved_for_message += 1

                if saved_for_message == 0 and body_fallback_enabled and body_text.strip():
                    sender_hint = _safe_name(sender.split("<")[0] if sender else "mail")
                    subject_hint = _safe_name(subject)[:80]
                    fallback_name = f"mail_{_safe_name(account_name)}_{msg_key}_{sender_hint}_{subject_hint}.txt"
                    output = target_dir / fallback_name
                    if output.exists():
                        output = target_dir / f"{_safe_name(account_name)}_{msg_key}_{int(output.stat().st_mtime)}_{fallback_name}"
                    output.write_text(
                        f"From: {sender}\nSubject: {subject}\n\n{body_text}\n",
                        encoding="utf-8",
                        errors="ignore",
                    )
                    saved.append(str(output))

                if use_uid:
                    processed_uids.add(msg_key)
                    # Save incrementally so we can resume after interruptions.
                    _save_imap_state(account_name, processed_uids)

                if mark_seen:
                    if use_uid:
                        client.uid("store", msg_id, "+FLAGS", "\\Seen")
                    else:
                        client.store(msg_id, "+FLAGS", "\\Seen")
        except (imaplib.IMAP4.error, TimeoutError, socket.timeout, OSError) as exc:
            print(f"IMAP collector warning [{account_name}]: {exc}")
        finally:
            try:
                if client is not None:
                    client.logout()
            except Exception:
                pass

    return saved
