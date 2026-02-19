import csv
import json
from pathlib import Path
from typing import Dict, List


def _load_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_json(path: Path) -> List[Dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return items
    return []


def load_reference_records(reference_dir: Path) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    for file_path in reference_dir.glob("*"):
        if file_path.suffix.lower() == ".csv":
            records.extend(_load_csv(file_path))
        elif file_path.suffix.lower() == ".json":
            records.extend(_load_json(file_path))
    return records


def compare_category_totals(reference: List[Dict[str, str]], current: List[Dict[str, str]]) -> List[Dict[str, str]]:
    def totals(rows: List[Dict[str, str]]) -> Dict[str, float]:
        output: Dict[str, float] = {}
        for row in rows:
            category = row.get("steuer_kategorie", "unbekannt")
            amount = row.get("brutto_betrag", "0")
            try:
                value = float(str(amount).replace(",", "."))
            except ValueError:
                value = 0.0
            output[category] = output.get(category, 0.0) + value
        return output

    ref_total = totals(reference)
    cur_total = totals(current)
    categories = sorted(set(ref_total) | set(cur_total))

    rows = []
    for category in categories:
        a = ref_total.get(category, 0.0)
        b = cur_total.get(category, 0.0)
        rows.append(
            {
                "steuer_kategorie": category,
                "summe_2024": f"{a:.2f}",
                "summe_2025": f"{b:.2f}",
                "delta": f"{(b - a):.2f}",
            }
        )
    return rows
