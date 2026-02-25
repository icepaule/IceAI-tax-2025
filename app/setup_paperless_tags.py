"""
Setup script: creates tags, correspondents, and storage paths in Paperless-NGX.

Usage:
    docker compose run --rm tax-pipeline python setup_paperless_tags.py

Idempotent -- safe to run multiple times.
"""

import os
import sys

import requests


class PaperlessSetup:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update(
            {"Authorization": f"Token {token}", "Accept": "application/json"}
        )

    def _get_or_create(self, endpoint: str, name: str) -> int:
        r = self.s.get(
            f"{self.base_url}/api/{endpoint}/",
            params={"name__iexact": name, "page_size": 100},
            timeout=30,
        )
        r.raise_for_status()
        for row in r.json().get("results", []):
            if str(row.get("name", "")).strip().lower() == name.lower():
                print(f"  [exists] {endpoint}: {name}")
                return int(row["id"])

        c = self.s.post(
            f"{self.base_url}/api/{endpoint}/",
            json={"name": name},
            timeout=30,
        )
        c.raise_for_status()
        print(f"  [created] {endpoint}: {name}")
        return int(c.json()["id"])

    def create_tag(self, name: str) -> int:
        return self._get_or_create("tags", name)

    def create_correspondent(self, name: str) -> int:
        return self._get_or_create("correspondents", name)

    def create_storage_path(self, name: str, path: str) -> int:
        r = self.s.get(
            f"{self.base_url}/api/storage_paths/",
            params={"name__iexact": name, "page_size": 100},
            timeout=30,
        )
        r.raise_for_status()
        for row in r.json().get("results", []):
            if str(row.get("name", "")).strip().lower() == name.lower():
                print(f"  [exists] storage_path: {name}")
                return int(row["id"])

        c = self.s.post(
            f"{self.base_url}/api/storage_paths/",
            json={"name": name, "path": path},
            timeout=30,
        )
        c.raise_for_status()
        print(f"  [created] storage_path: {name} -> {path}")
        return int(c.json()["id"])


TAGS = [
    # Person tags -- customize with your own names
    # "Person-A",
    # "Person-B",
    # Steuer hierarchy
    "Steuer/Werbungskosten",
    "Steuer/Werbungskosten/Arbeitsmittel",
    "Steuer/Werbungskosten/Fahrtkosten",
    "Steuer/Werbungskosten/Homeoffice",
    "Steuer/Werbungskosten/Fortbildung",
    "Steuer/Werbungskosten/Telekommunikation",
    "Steuer/Hausverwaltung",
    "Steuer/Hausverwaltung/Nebenkosten",
    "Steuer/Hausverwaltung/Miete",
    "Steuer/Versicherungen",
    "Steuer/Versicherungen/Haftpflicht",
    "Steuer/Versicherungen/Hausrat",
    "Steuer/Versicherungen/KFZ",
    "Steuer/Versicherungen/Leben",
    "Steuer/Versicherungen/Berufsunfaehigkeit",
    "Steuer/Gesundheit",
    "Steuer/Gesundheit/Arzt",
    "Steuer/Gesundheit/Apotheke",
    "Steuer/Gesundheit/Krankenkasse",
    "Steuer/Bank",
    "Steuer/Bank/Kontoauszug",
    "Steuer/Bank/Kreditkarte",
    "Steuer/Bank/Kredit",
    "Steuer/Sonderausgaben",
    "Steuer/Sonderausgaben/Spenden",
    "Steuer/Haushaltsnahe Leistungen",
    "Steuer/Pruefung",
    # Source / processing tags
    "source:scanner",
    "ki-verarbeitet",
    # Year tags
    "steuerjahr:2025",
    "steuerjahr:2026",
]

# Customize: Add your own correspondents here
CORRESPONDENTS = [
    # "Vorname Nachname",
]

# Customize: Storage paths for per-person archive directories
# Uses Paperless-NGX template variables: {created_year}, {correspondent}, {title}
STORAGE_PATHS = [
    ("Archiv Person-A", "Archiv/Person-A/{created_year}/{correspondent}/{title}"),
    ("Archiv Person-B", "Archiv/Person-B/{created_year}/{correspondent}/{title}"),
    ("Archiv Allgemein", "Archiv/Allgemein/{created_year}/{correspondent}/{title}"),
]


def main() -> None:
    base_url = os.getenv("PAPERLESS_URL", "http://127.0.0.1:8010").strip()
    token = os.getenv("PAPERLESS_API_TOKEN", "").strip()

    if not token:
        print("ERROR: PAPERLESS_API_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    p = PaperlessSetup(base_url, token)

    print("=== Creating tags ===")
    for tag in TAGS:
        p.create_tag(tag)

    print("\n=== Creating correspondents ===")
    for corr in CORRESPONDENTS:
        p.create_correspondent(corr)

    print("\n=== Creating storage paths ===")
    for name, path in STORAGE_PATHS:
        p.create_storage_path(name, path)

    print("\nDone.")


if __name__ == "__main__":
    main()
