import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse

import requests

from selenium import webdriver
from selenium.common.exceptions import (
    InvalidSessionIdException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver import ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

PORTAL_URLS = {
    "amazon": "https://www.amazon.de/gp/css/order-history",
    "ebay": "https://www.ebay.de/mye/myebay/purchase",
    "vodafone": "https://www.vodafone.de/meinvodafone/services/ihre-rechnungen/rechnungen",
    # Hetzner: interactive login via noVNC is the most reliable.
    # Default to the invoice page on accounts.hetzner.com (user confirmed).
    "hetzner": os.getenv("HETZNER_CONSOLE_URL", "https://accounts.hetzner.com/invoice"),
}

LINK_PATTERNS = {
    "amazon": ["Rechnung", "Invoice", "Bestellrechnung", "Download"],
    "ebay": ["Rechnung", "Invoice", "Bestelldetails", "Download"],
    "vodafone": ["Rechnung", "PDF", "Rechnung (PDF)", "Download", "Dokument", "Herunterladen"],
    # Best-effort only; in practice Hetzner is usually handled via interactive downloads.
    "hetzner": ["Invoice", "Rechnung", "PDF", "Download", "Herunterladen"],
}

ORDER_SELECTORS = {
    "amazon": [
        "div.order",
        "div.order-card",
        "div.a-box-group",
        "div.js-order-card",
    ],
    "ebay": [
        "div.order-r",
        "div.order-card",
        "div.m-order-card",
        "div[data-test-id='order-card']",
    ],
    "vodafone": [
        "tr",
        "table tr",
        "div.invoice",
        "div[class*='invoice']",
        "div[class*='bill']",
        "li",
    ],
    # Best-effort: invoice rows are typically table rows; keep it broad.
    "hetzner": ["tr", "table tr", "div", "li"],
}

NEXT_PAGE_SELECTORS = {
    "amazon": [
        "ul.a-pagination li.a-last a",
        "a[aria-label*='Nächste']",
        "a:has-text('Weiter')",
    ],
    "ebay": [
        "a[aria-label*='Next']",
        "button[aria-label*='Next']",
    ],
    "vodafone": [
        "a[aria-label*='Nächste']",
        "a[aria-label*='Next']",
        "button[aria-label*='Nächste']",
        "button[aria-label*='Next']",
        "a[rel='next']",
    ],
    # Hetzner billing pages may paginate; try common next buttons.
    "hetzner": [
        "a[aria-label*='Next']",
        "button[aria-label*='Next']",
        "a[rel='next']",
        "button[title*='Next']",
    ],
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    value = re.sub(r"_+", "_", value).strip("._")
    return value or "unbekannt"


def build_driver(download_dir: Path, headless: bool) -> WebDriver:
    remote_url = os.getenv("SELENIUM_REMOTE_URL", "http://portal-browser:4444/wd/hub")

    options = ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    prefs = {
        "download.default_directory": str(download_dir),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
    }
    options.add_experimental_option("prefs", prefs)

    driver = webdriver.Remote(command_executor=remote_url, options=options)
    driver.set_page_load_timeout(120)
    return driver


def save_cookies(driver: WebDriver, cookie_file: Path) -> None:
    save_json(cookie_file, driver.get_cookies())


def load_cookies(driver: WebDriver, cookie_file: Path, portal_url: str) -> bool:
    if not cookie_file.exists():
        return False

    cookies = load_json(cookie_file, [])
    driver.get(portal_url)
    for cookie in cookies:
        cookie = dict(cookie)
        cookie.pop("sameSite", None)
        try:
            driver.add_cookie(cookie)
        except Exception:
            continue
    driver.get(portal_url)
    return True


def interactive_login(portal: str, year: int, wait_seconds: int) -> None:
    portal_root = Path("/data/portal")
    cookie_file = portal_root / "state" / f"{portal}.cookies.json"
    download_dir = portal_root / "downloads" / portal / str(year)
    download_dir.mkdir(parents=True, exist_ok=True)

    driver = build_driver(download_dir=download_dir, headless=False)
    try:
        if portal == "hetzner":
            print(f"[{portal}] noVNC window ready at http://<host>:7900", flush=True)
            print(
                f"[{portal}] Please login (if needed), then ensure you are on https://accounts.hetzner.com/invoice",
                flush=True,
            )
            print(f"[{portal}] Download all invoices for {year}.", flush=True)
            print(f"[{portal}] Waiting {wait_seconds}s for interactive actions...", flush=True)
            # Don't block on slow page loads; user can navigate in VNC anyway.
            try:
                driver.get(PORTAL_URLS[portal])
            except TimeoutException:
                pass
        else:
            driver.get(PORTAL_URLS[portal])
            print(
                f"[{portal}] noVNC login window ready. Open http://<host>:7900 and login within {wait_seconds}s.",
                flush=True,
            )
        deadline = time.time() + wait_seconds
        last_count = -1
        while time.time() < deadline:
            time.sleep(5)
            try:
                # Keep session alive while user logs in interactively.
                _ = driver.current_url
                # Helpful progress for portals where the user clicks downloads manually.
                if portal == "hetzner":
                    count = len([p for p in download_dir.glob("*") if p.is_file()])
                    if count != last_count:
                        print(f"[{portal}] Downloads in folder so far: {count}", flush=True)
                        last_count = count
            except InvalidSessionIdException:
                print(f"[{portal}] Session closed during login wait.", flush=True)
                break
        try:
            save_cookies(driver, cookie_file)
            print(f"[{portal}] Cookies saved: {cookie_file}", flush=True)
        except InvalidSessionIdException:
            print(f"[{portal}] Session ended before cookies could be saved. Re-run interactive login.", flush=True)
    finally:
        try:
            driver.quit()
        except InvalidSessionIdException:
            pass


def collect_order_elements(driver: WebDriver, portal: str):
    selectors = ORDER_SELECTORS.get(portal, [])
    for selector in selectors:
        elems = driver.find_elements(By.CSS_SELECTOR, selector)
        if elems:
            return elems
    return []


def _links_in_element(order_el, patterns: List[str]):
    links = []
    seen_labels = set()
    selectors = [
        "a",
        "button",
        "[role='button']",
        "[onclick]",
        "[data-testid*='download']",
    ]
    for selector in selectors:
        try:
            elems = order_el.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue
        for elem in elems:
            text = (elem.text or "").strip()
            attrs = " ".join(
                [
                    elem.get_attribute("href") or "",
                    elem.get_attribute("title") or "",
                    elem.get_attribute("aria-label") or "",
                    elem.get_attribute("download") or "",
                    elem.get_attribute("data-testid") or "",
                ]
            ).strip()
            haystack = f"{text} {attrs}".lower()
            if not haystack:
                continue
            if any(p.lower() in haystack for p in patterns):
                label = re.sub(r"\s+", " ", haystack)[:220]
                if label in seen_labels:
                    continue
                seen_labels.add(label)
                links.append(elem)
    return links


def _order_key(text: str, index: int) -> str:
    m = re.search(r"(bestellung|order)\s*#?\s*([A-Z0-9-]{6,})", text, re.IGNORECASE)
    if m:
        return slug(m.group(2))
    return f"order_{index:03d}"


def goto_next_page(driver: WebDriver, portal: str) -> bool:
    selectors = NEXT_PAGE_SELECTORS.get(portal, [])
    for selector in selectors:
        try:
            elems = driver.find_elements(By.CSS_SELECTOR, selector)
            for el in elems:
                if not el.is_displayed():
                    continue
                href = el.get_attribute("href")
                if href:
                    driver.get(href)
                else:
                    driver.execute_script("arguments[0].click();", el)
                time.sleep(2.0)
                return True
        except Exception:
            continue
    return False


def click_vodafone_more(driver: WebDriver, rounds: int = 8) -> int:
    clicked = 0
    xpaths = [
        "//button[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'mehr anzeigen')]",
        "//a[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'mehr anzeigen')]",
        "//*[@aria-label and contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'mehr anzeigen')]",
    ]
    for _ in range(rounds):
        found = False
        for xpath in xpaths:
            try:
                elems = driver.find_elements(By.XPATH, xpath)
            except Exception:
                continue
            for elem in elems:
                try:
                    if not elem.is_displayed():
                        continue
                    driver.execute_script("arguments[0].click();", elem)
                    time.sleep(1.0)
                    clicked += 1
                    found = True
                    break
                except Exception:
                    continue
            if found:
                break
        if not found:
            break
    return clicked


def click_vodafone_pdf_actions(driver: WebDriver) -> int:
    clicked = 0
    seen = set()
    xpaths = [
        "//a[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'rechnung') and contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'pdf')]",
        "//button[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'rechnung') and contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'pdf')]",
        "//*[@aria-label and contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'rechnung') and contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'pdf')]",
        "//a[contains(translate(@href,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'.pdf')]",
    ]
    for xpath in xpaths:
        try:
            elems = driver.find_elements(By.XPATH, xpath)
        except Exception:
            continue
        for elem in elems:
            try:
                if not elem.is_displayed():
                    continue
                marker = " ".join(
                    [
                        (elem.text or "").strip(),
                        elem.get_attribute("href") or "",
                        elem.get_attribute("aria-label") or "",
                        elem.get_attribute("title") or "",
                    ]
                ).strip().lower()
                marker = re.sub(r"\s+", " ", marker)[:260]
                if not marker or marker in seen:
                    continue
                seen.add(marker)
                driver.execute_script("arguments[0].click();", elem)
                time.sleep(1.0)
                clicked += 1
            except Exception:
                continue
    return clicked


def _invoice_id_from_url(url: str) -> str:
    match = re.search(r"/invoiceDocument/(\d+)", url)
    if match:
        return match.group(1)
    return slug(url)[-18:] or "invoice"


def _vodafone_urls_from_text(text: str) -> List[str]:
    if not text:
        return []
    normalized = text.replace("\\/", "/")
    pattern = r"https://api\.vodafone\.de/meinvodafone/v2/customer/urn:vf-de:cable:can:[^\"'\s<>]+/invoiceDocument/\d+"
    found = re.findall(pattern, normalized, re.IGNORECASE)
    out: List[str] = []
    seen: Set[str] = set()
    for item in found:
        value = item.strip()
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _vodafone_manual_urls() -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    raw_env = os.getenv("VODAFONE_INVOICE_URLS", "")
    sources = [raw_env]
    manual_file = Path("/data/portal/state/vodafone_invoice_urls.txt")
    if manual_file.exists():
        try:
            sources.append(manual_file.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            pass
    for source in sources:
        for url in _vodafone_urls_from_text(source):
            if url in seen:
                continue
            seen.add(url)
            out.append(url)
    return out


def _collect_invoice_doc_urls(obj: object, out: Set[str]) -> None:
    if isinstance(obj, dict):
        for value in obj.values():
            _collect_invoice_doc_urls(value, out)
        return
    if isinstance(obj, list):
        for item in obj:
            _collect_invoice_doc_urls(item, out)
        return
    if not isinstance(obj, str):
        return
    for url in _vodafone_urls_from_text(obj):
        out.add(url)


def _extract_invoice_urls_from_payload(payload: object) -> List[str]:
    urls: Set[str] = set()
    _collect_invoice_doc_urls(payload, urls)
    return sorted(urls)


def _build_vodafone_api_headers(user_agent: str) -> Dict[str, str]:
    api_key = os.getenv("VODAFONE_API_KEY", "")
    now_ms = str(int(time.time() * 1000))
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "disablespinner": "true",
        "timeout": "45000",
        "wf-api-name": "invoice-GET",
        "x-api-key": api_key,
        "x-vf-api": now_ms,
        "x-vf-clientid": os.getenv("VODAFONE_CLIENT_ID", "MyVFWeb"),
    }
    if user_agent:
        headers["User-Agent"] = user_agent
    return headers


def _vodafone_invoice_list_url(manual_urls: List[str], fallback_source: str) -> Optional[str]:
    all_urls = manual_urls + _vodafone_urls_from_text(fallback_source)
    if not all_urls:
        return None
    first = all_urls[0]
    match = re.search(
        r"(https://api\.vodafone\.de/meinvodafone/v2/customer/urn:vf-de:cable:can:[^/]+)/invoiceDocument/\d+",
        first,
        re.IGNORECASE,
    )
    if not match:
        return None
    return f"{match.group(1)}/invoice"


def download_vodafone_api_docs(driver: WebDriver, download_dir: Path, year: int) -> int:
    manual_urls = _vodafone_manual_urls()
    urls = manual_urls + _vodafone_urls_from_text(driver.page_source)
    if not urls:
        return 0

    unique_urls: List[str] = []
    seen: Set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique_urls.append(url)

    session = requests.Session()
    try:
        user_agent = str(driver.execute_script("return navigator.userAgent") or "")
    except Exception:
        user_agent = ""
    if user_agent:
        session.headers.update({"User-Agent": user_agent})
    session.headers.update({"Accept": "application/pdf,application/json,text/plain,*/*"})
    for cookie in driver.get_cookies():
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        domain = cookie.get("domain") or ".vodafone.de"
        session.cookies.set(name, value, domain=domain)

    list_url = _vodafone_invoice_list_url(manual_urls, driver.page_source)
    if list_url:
        try:
            list_headers = _build_vodafone_api_headers(user_agent)
            list_response = session.get(list_url, headers=list_headers, timeout=60, allow_redirects=True)
            if list_response.status_code < 300:
                payload = list_response.json()
                for item in _extract_invoice_urls_from_payload(payload):
                    if item not in seen:
                        seen.add(item)
                        unique_urls.append(item)
        except Exception:
            pass

    downloaded = 0
    for url in unique_urls:
        pdf_bytes: Optional[bytes] = None
        try:
            response = session.get(url, timeout=60, allow_redirects=True)
        except Exception:
            continue
        if response.status_code >= 300:
            continue
        content_type = (response.headers.get("Content-Type", "") or "").lower()
        if "application/pdf" in content_type:
            pdf_bytes = response.content
        else:
            redirect_url = ""
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    for key in ("url", "downloadUrl", "download_url", "documentUrl", "href"):
                        candidate = payload.get(key)
                        if isinstance(candidate, str) and candidate.startswith("http"):
                            redirect_url = candidate
                            break
            except Exception:
                redirect_url = ""
            if redirect_url:
                try:
                    parsed = urlparse(redirect_url)
                    domain = parsed.hostname or ""
                    if domain.endswith("vodafone.de"):
                        second = session.get(redirect_url, timeout=60, allow_redirects=True)
                    else:
                        second = requests.get(redirect_url, timeout=60, allow_redirects=True)
                    second_type = (second.headers.get("Content-Type", "") or "").lower()
                    if second.status_code < 300 and "application/pdf" in second_type:
                        pdf_bytes = second.content
                except Exception:
                    pdf_bytes = None
        if not pdf_bytes:
            continue
        invoice_id = _invoice_id_from_url(url)
        output = download_dir / f"vodafone_{year}_invoice_{invoice_id}.pdf"
        if output.exists():
            continue
        output.write_bytes(pdf_bytes)
        downloaded += 1
    return downloaded


def dedupe_files(download_dir: Path, index_file: Path) -> Dict[str, int]:
    idx = load_json(index_file, {"hashes": {}})
    known_hashes = idx.get("hashes", {})
    removed = 0
    kept = 0

    for file_path in sorted(download_dir.rglob("*")):
        if not file_path.is_file():
            continue
        file_hash = sha256_file(file_path)
        if file_hash in known_hashes:
            file_path.unlink(missing_ok=True)
            removed += 1
            continue
        known_hashes[file_hash] = str(file_path)
        kept += 1

    idx["hashes"] = known_hashes
    save_json(index_file, idx)
    return {"kept": kept, "removed": removed}


def fetch_portal(portal: str, year: int) -> None:
    portal_root = Path("/data/portal")
    cookie_file = portal_root / "state" / f"{portal}.cookies.json"
    index_file = portal_root / "state" / f"{portal}.dedupe.json"
    download_dir = portal_root / "downloads" / portal / str(year)
    screenshot_dir = portal_root / "screenshots" / portal / str(year)

    download_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    driver = build_driver(download_dir=download_dir, headless=True)
    try:
        loaded = load_cookies(driver, cookie_file, PORTAL_URLS[portal])
        if not loaded:
            print(f"[{portal}] No cookie file found. Run with --interactive first: {cookie_file}")
            return

        try:
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except TimeoutException:
            pass

        if portal == "hetzner":
            # Best-effort headless fetch. In most cases, use --interactive and download invoices via noVNC.
            invoices_url = os.getenv("HETZNER_INVOICES_URL", "https://accounts.hetzner.com/invoice")
            driver.get(invoices_url)
            time.sleep(3.0)
            shot = screenshot_dir / f"{portal}_{year}_billing_invoices.png"
            try:
                driver.save_screenshot(str(shot))
            except Exception:
                pass

        orders = collect_order_elements(driver, portal)
        if not orders:
            # fallback whole page screenshot
            page_shot = screenshot_dir / f"{portal}_{year}_orders_page.png"
            driver.save_screenshot(str(page_shot))
            print(f"[{portal}] No order cards detected. Saved page screenshot: {page_shot}")
        downloaded_clicks = 0
        fallback_shots = 0
        scanned_orders = 0
        scanned_pages = 0
        before_files = {str(p) for p in download_dir.glob("*") if p.is_file()}
        max_pages = int(os.getenv("PORTAL_MAX_PAGES", "12"))
        seen_urls: Set[str] = set()

        while scanned_pages < max_pages:
            current_url = driver.current_url
            if current_url in seen_urls:
                break
            seen_urls.add(current_url)

            orders = collect_order_elements(driver, portal)
            scanned_pages += 1
            if not orders and scanned_pages == 1:
                page_shot = screenshot_dir / f"{portal}_{year}_orders_page.png"
                driver.save_screenshot(str(page_shot))
            if portal == "vodafone":
                click_vodafone_more(driver)
                orders = collect_order_elements(driver, portal)
                downloaded_clicks += click_vodafone_pdf_actions(driver)
                downloaded_clicks += download_vodafone_api_docs(driver, download_dir, year)

            order_count = min(len(orders), 250)
            for idx in range(order_count):
                refreshed_orders = collect_order_elements(driver, portal)
                if idx >= len(refreshed_orders):
                    continue
                order_el = refreshed_orders[idx]
                scanned_orders += 1
                try:
                    text = (order_el.text or "").strip()
                except StaleElementReferenceException:
                    continue
                key = _order_key(text, (idx + 1) + (scanned_pages * 1000))
                try:
                    links = _links_in_element(order_el, LINK_PATTERNS[portal])
                except StaleElementReferenceException:
                    links = []
                if not links and portal == "vodafone":
                    # Vodafone often reveals PDF actions only after selecting a billing row.
                    try:
                        driver.execute_script("arguments[0].click();", order_el)
                        time.sleep(0.4)
                        links = _links_in_element(order_el, LINK_PATTERNS[portal])
                        if not links:
                            parent_el = order_el.find_element(By.XPATH, "..")
                            links = _links_in_element(parent_el, LINK_PATTERNS[portal])
                    except Exception:
                        links = []

                if links:
                    clicked = 0
                    for link in links:
                        try:
                            driver.execute_script("arguments[0].click();", link)
                            time.sleep(1.2)
                            clicked += 1
                            downloaded_clicks += 1
                        except Exception:
                            continue
                    if clicked == 0:
                        shot = screenshot_dir / f"{portal}_{year}_{key}_fallback.png"
                        try:
                            order_el.screenshot(str(shot))
                        except StaleElementReferenceException:
                            driver.save_screenshot(str(shot))
                        fallback_shots += 1
                else:
                    shot = screenshot_dir / f"{portal}_{year}_{key}_fallback.png"
                    try:
                        order_el.screenshot(str(shot))
                    except Exception:
                        driver.save_screenshot(str(shot))
                    fallback_shots += 1

            if portal == "vodafone":
                downloaded_clicks += click_vodafone_pdf_actions(driver)
                downloaded_clicks += download_vodafone_api_docs(driver, download_dir, year)

            if not goto_next_page(driver, portal):
                break

        stats = dedupe_files(download_dir, index_file)
        after_files = {str(p) for p in download_dir.glob("*") if p.is_file()}
        new_files_count = max(0, len(after_files - before_files))

        # If links were clicked but no files arrived, keep evidence screenshots for finance office.
        if downloaded_clicks > 0 and new_files_count == 0 and fallback_shots == 0:
            page_shot = screenshot_dir / f"{portal}_{year}_orders_fallback_fullpage.png"
            driver.save_screenshot(str(page_shot))
            fallback_shots += 1
            fallback_orders = collect_order_elements(driver, portal)
            for idx, order_el in enumerate(fallback_orders[:30], start=1):
                try:
                    key = _order_key((order_el.text or "").strip(), idx)
                except StaleElementReferenceException:
                    continue
                shot = screenshot_dir / f"{portal}_{year}_{key}_fallback.png"
                try:
                    order_el.screenshot(str(shot))
                    fallback_shots += 1
                except Exception:
                    continue
        print(f"[{portal}] Pages scanned: {scanned_pages}, orders scanned: {scanned_orders}")
        print(f"[{portal}] Clicked download candidates: {downloaded_clicks}")
        print(f"[{portal}] Fallback screenshots: {fallback_shots} -> {screenshot_dir}")
        print(f"[{portal}] New files this run: {new_files_count}")
        print(f"[{portal}] Dedupe result: kept={stats['kept']} removed_duplicates={stats['removed']} in {download_dir}")
    finally:
        driver.quit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Portal fetcher for amazon/ebay/vodafone invoices")
    parser.add_argument("--portal", choices=["amazon", "ebay", "vodafone", "hetzner"], required=True)
    parser.add_argument("--year", type=int, default=int(os.getenv("TAX_YEAR", "2025")))
    parser.add_argument("--interactive", action="store_true", help="Open browser for manual login via noVNC")
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=int(os.getenv("PORTAL_INTERACTIVE_WAIT_SECONDS", "240")),
        help="Only for --interactive: how long to keep the VNC session open",
    )
    args = parser.parse_args()

    if args.interactive:
        interactive_login(args.portal, args.year, args.wait_seconds)
    else:
        fetch_portal(args.portal, args.year)


if __name__ == "__main__":
    main()
