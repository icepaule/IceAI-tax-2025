import json
import os
import re
import time
import base64
from pathlib import Path
from typing import Any, Set

import requests


COOKIE_FILE = Path("/data/portal/state/vodafone.cookies.json")
URLS_FILE = Path("/data/portal/state/vodafone_invoice_urls.txt")
OUT_DIR = Path("/data/portal/downloads/vodafone/2025")


def _extract_customer_base_from_urls(text: str) -> str | None:
    m = re.search(
        r"(https://api\.vodafone\.de/meinvodafone/v2/customer/urn:vf-de:cable:can:[^/\s]+)",
        text,
        re.IGNORECASE,
    )
    return m.group(1) if m else None


def _collect_invoice_doc_urls(obj: Any, out: Set[str]) -> None:
    if isinstance(obj, dict):
        for value in obj.values():
            _collect_invoice_doc_urls(value, out)
        return
    if isinstance(obj, list):
        for item in obj:
            _collect_invoice_doc_urls(item, out)
        return
    if isinstance(obj, str):
        for match in re.findall(
            r"https://api\.vodafone\.de/meinvodafone/v2/customer/urn:vf-de:cable:can:[^\"'\s<>]+/invoiceDocument/\d+",
            obj.replace("\\/", "/"),
            re.IGNORECASE,
        ):
            out.add(match)


def _build_urls_from_invoice_payload(payload: Any, tax_year: int, customer_base: str) -> list[str]:
    urls: Set[str] = set()
    if not isinstance(payload, dict):
        return []
    invoices = payload.get("invoices", [])
    if not isinstance(invoices, list):
        return []
    for item in invoices:
        if not isinstance(item, dict):
            continue
        date_text = str(item.get("date", "") or "")
        if not date_text.startswith(f"{tax_year}-"):
            continue
        documents = item.get("documents", [])
        if not isinstance(documents, list):
            continue
        for doc in documents:
            if not isinstance(doc, dict):
                continue
            document_id = str(doc.get("documentId", "") or "").strip()
            if not document_id.isdigit():
                continue
            urls.add(f"{customer_base}/invoiceDocument/{document_id}")
    return sorted(urls)


def _invoice_id(url: str) -> str:
    m = re.search(r"/invoiceDocument/(\d+)", url)
    return m.group(1) if m else "unknown"


def main() -> None:
    if not COOKIE_FILE.exists():
        raise SystemExit(f"missing cookie file: {COOKIE_FILE}")
    if not URLS_FILE.exists():
        raise SystemExit(f"missing urls file: {URLS_FILE}")

    raw_urls = URLS_FILE.read_text(encoding="utf-8", errors="ignore")
    customer_base = _extract_customer_base_from_urls(raw_urls)
    if not customer_base:
        raise SystemExit("could not infer customer base from vodafone_invoice_urls.txt")

    list_url = f"{customer_base}/invoice"
    print("LIST_URL", list_url)

    session = requests.Session()
    auth_bearer = (os.getenv("VODAFONE_AUTH_BEARER", "") or "").strip()
    if not auth_bearer:
        raise SystemExit("missing VODAFONE_AUTH_BEARER env var")

    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "disablespinner": "true",
            "timeout": "45000",
            "wf-api-name": "invoice-GET",
            "x-api-key": os.getenv("VODAFONE_API_KEY", ""),
            "x-vf-api": str(int(time.time() * 1000)),
            "x-vf-clientid": "MyVFWeb",
            "Origin": "https://www.vodafone.de",
            "Referer": "https://www.vodafone.de/",
            "Authorization": auth_bearer,
        }
    )

    cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        domain = cookie.get("domain") or ".vodafone.de"
        session.cookies.set(name, value, domain=domain)

    response = session.get(list_url, timeout=60)
    print("LIST_STATUS", response.status_code, response.headers.get("Content-Type", ""))
    if response.status_code >= 300:
        print(response.text[:1000])
        raise SystemExit(1)

    payload = response.json()
    Path("/data/portal/state/vodafone_invoice_list_response.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    urls: Set[str] = set()
    for url in _build_urls_from_invoice_payload(payload, 2025, customer_base):
        urls.add(url)
    _collect_invoice_doc_urls(payload, urls)
    print("INVOICE_URLS", len(urls))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    for url in sorted(urls):
        r = session.get(url, timeout=60, allow_redirects=True)
        if r.status_code >= 300:
            print("DOC_FAIL", r.status_code, url)
            continue
        ctype = (r.headers.get("Content-Type", "") or "").lower()
        blob = None
        if "application/pdf" in ctype:
            blob = r.content
        else:
            try:
                data = r.json()
            except Exception:
                data = {}
            if isinstance(data, dict):
                mime = str(data.get("mime", "") or "").lower()
                encoded = data.get("data")
                if "pdf" in mime and isinstance(encoded, str) and encoded.strip():
                    try:
                        blob = base64.b64decode(encoded, validate=False)
                    except Exception:
                        blob = None
            next_url = ""
            if isinstance(data, dict):
                for key in ("url", "downloadUrl", "download_url", "documentUrl", "href"):
                    value = data.get(key)
                    if isinstance(value, str) and value.startswith("http"):
                        next_url = value
                        break
            if next_url:
                rr = session.get(next_url, timeout=60, allow_redirects=True)
                if rr.status_code < 300 and "application/pdf" in (
                    rr.headers.get("Content-Type", "") or ""
                ).lower():
                    blob = rr.content
        if not blob:
            print("DOC_NOPDF", url)
            continue
        target = OUT_DIR / f"vodafone_2025_invoice_{_invoice_id(url)}.pdf"
        if target.exists():
            continue
        target.write_bytes(blob)
        downloaded += 1
    print("DOWNLOADED", downloaded)


if __name__ == "__main__":
    main()
