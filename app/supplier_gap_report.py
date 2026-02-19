import csv
import json
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from config import SETTINGS

DATA_ROOT = Path("/data")
EXPORTS = DATA_ROOT / "exports"
LOGS = DATA_ROOT / "logs"

TARGET_SUPPLIERS = {
    "vodafone": ["vodafone", "kabel deutschland", "unitymedia"],
    "hetzner": ["hetzner"],
    "tibber": ["tibber"],
    "goldgas": ["goldgas", "goldgas.de", "21716206"],
    "sipgate": ["sipgate"],
    "strato": ["strato", "strato.de"],
    "aliexpress": ["aliexpress", "ali express", "alibaba"],
    "az-delivery": ["az-delivery", "az delivery", "azdelivery"],
    "usenext": ["usenext", "use-next", "use next"],
}


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def load_rows() -> List[Dict[str, str]]:
    export_csv = EXPORTS / SETTINGS.export_filename
    if export_csv.exists() and export_csv.stat().st_size > 0:
        with export_csv.open("r", newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    fallback_json = LOGS / "last_nonempty_export.json"
    if fallback_json.exists() and fallback_json.stat().st_size > 0:
        try:
            payload = json.loads(fallback_json.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return [dict(x) for x in payload if isinstance(x, dict)]
        except Exception:
            return []
    return []


def build_gap_report(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    report: List[Dict[str, str]] = []
    for supplier, aliases in TARGET_SUPPLIERS.items():
        matched = []
        for row in rows:
            merchant = _norm(str(row.get("haendler", "")))
            filename = _norm(str(row.get("datei", "")))
            if any(alias in merchant or alias in filename for alias in aliases):
                matched.append(row)

        strong = []
        for row in matched:
            try:
                amount = float(str(row.get("brutto_betrag", "0") or "0").replace(",", "."))
            except Exception:
                amount = 0.0
            doc_type = _norm(str(row.get("dokumenttyp", "")))
            suffix = Path(str(row.get("datei", "") or "")).suffix.lower()
            if suffix == ".pdf":
                strong.append(row)
                continue
            if amount > 0:
                strong.append(row)
                continue
            if doc_type in {"invoice", "rechnung", "steuerbeleg"}:
                strong.append(row)
                continue

        found_merchants = sorted(
            {
                str(r.get("haendler", "")).strip()
                for r in matched
                if str(r.get("haendler", "")).strip()
            }
        )
        found_docs = len(matched)
        strong_docs = len(strong)
        if strong_docs > 0:
            status = "OK"
        elif found_docs > 0:
            status = "WEAK"
        else:
            status = "MISSING"
        action = (
            "Kein Handlungsbedarf"
            if status == "OK"
            else "Portal-Download manuell, danach Dateien nach /srv/taxdrop legen und make run ausfuehren"
        )
        report.append(
            {
                "steuer_jahr": str(SETTINGS.tax_year),
                "lieferant": supplier,
                "status": status,
                "belege_gesamt": str(found_docs),
                "belege_stark": str(strong_docs),
                "haendler_gefunden": " | ".join(found_merchants),
                "naechster_schritt": action,
            }
        )
    return report


def write_report(rows: List[Dict[str, str]]) -> Path:
    out = EXPORTS / f"supplier_gap_{SETTINGS.tax_year}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "steuer_jahr",
        "lieferant",
        "status",
        "belege_gesamt",
        "belege_stark",
        "haendler_gefunden",
        "naechster_schritt",
    ]
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Supplier gap report")
    parser.add_argument("--tax-year", type=int, help="Explizites Steuerjahr")
    parser.add_argument(
        "--current-year",
        action="store_true",
        help="Aktuelles Kalenderjahr statt Vorjahr verwenden",
    )
    args = parser.parse_args()

    current_year = datetime.now().year
    if args.tax_year is not None:
        SETTINGS.tax_year = args.tax_year
    elif args.current_year:
        SETTINGS.tax_year = current_year
    else:
        SETTINGS.tax_year = current_year - 1

    rows = load_rows()
    report = build_gap_report(rows)
    out = write_report(report)
    missing = sum(1 for r in report if r["status"] == "MISSING")
    print(f"rows_in_export_basis={len(rows)}")
    print(f"missing_suppliers={missing}")
    print(f"report={out}")


if __name__ == "__main__":
    main()
