import json
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver import ChromeOptions
from selenium.webdriver.common.by import By

URL = "https://www.vodafone.de/meinvodafone/services/ihre-rechnungen/rechnungen"
COOKIE_FILE = Path("/data/portal/state/vodafone.cookies.json")


def _click_more(driver, rounds: int = 5) -> None:
    xpaths = [
        "//button[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'mehr anzeigen')]",
        "//a[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'mehr anzeigen')]",
        "//*[@aria-label and contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'mehr anzeigen')]",
    ]
    for _ in range(rounds):
        clicked = False
        for xpath in xpaths:
            elems = driver.find_elements(By.XPATH, xpath)
            for elem in elems:
                try:
                    if not elem.is_displayed():
                        continue
                    driver.execute_script("arguments[0].click();", elem)
                    time.sleep(1.0)
                    clicked = True
                    break
                except Exception:
                    continue
            if clicked:
                break
        if not clicked:
            return


def main() -> None:
    options = ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Remote(command_executor="http://portal-browser:4444/wd/hub", options=options)
    try:
        driver.set_page_load_timeout(60)
        driver.get(URL)
        if COOKIE_FILE.exists():
            cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
            for cookie in cookies:
                cookie = dict(cookie)
                cookie.pop("sameSite", None)
                try:
                    driver.add_cookie(cookie)
                except Exception:
                    continue
            driver.get(URL)
        time.sleep(4.0)
        print("URL", driver.current_url)

        _click_more(driver, rounds=8)

        queries = [
            "//a[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'rechnung') and contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'pdf')]",
            "//button[contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'rechnung') and contains(translate(normalize-space(.),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'pdf')]",
            "//*[@aria-label and contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'rechnung') and contains(translate(@aria-label,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'pdf')]",
            "//a[contains(translate(@href,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'.pdf')]",
        ]
        total = 0
        for xpath in queries:
            elems = driver.find_elements(By.XPATH, xpath)
            print("XPATH_COUNT", len(elems), xpath)
            total += len(elems)
            for elem in elems[:12]:
                try:
                    print(
                        "CAND",
                        (elem.text or "").strip()[:120],
                        (elem.get_attribute("href") or "").strip()[:180],
                        (elem.get_attribute("aria-label") or "").strip()[:120],
                        (elem.get_attribute("title") or "").strip()[:120],
                    )
                except Exception:
                    continue
        print("TOTAL_CAND", total)
        Path("/data/portal/state/vodafone_debug_source.html").write_text(
            driver.page_source, encoding="utf-8"
        )
        print("SAVED", "/data/portal/state/vodafone_debug_source.html")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
