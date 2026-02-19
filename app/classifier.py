from pathlib import Path
from typing import Any, Dict

import yaml


class TaxClassifier:
    def __init__(self, taxonomy_path: str = "/config/taxonomy.yml") -> None:
        self.taxonomy = yaml.safe_load(Path(taxonomy_path).read_text(encoding="utf-8"))

    def classify(self, extracted: Dict[str, Any], full_text: str) -> Dict[str, str]:
        merchant = (extracted.get("merchant") or "").strip().lower()
        text = f"{full_text}\n{merchant}\n{extracted.get('line_items_summary', '')}".lower()

        overrides = self.taxonomy.get("merchant_overrides", {})
        categories = self.taxonomy.get("categories", {})

        selected_key = None

        for key, value in overrides.items():
            if key in merchant:
                selected_key = value
                break

        if selected_key is None:
            for category_key, entry in categories.items():
                keywords = entry.get("keywords", [])
                if any(keyword.lower() in text for keyword in keywords):
                    selected_key = category_key
                    break

        if selected_key is None:
            selected_key = "sonstiges_pruefung"

        selected = categories[selected_key]
        return {
            "category": selected_key,
            "ecodms_path": selected["ecodms_path"],
            "ecodms_mainfolder_oid": selected.get("ecodms_mainfolder_oid", "1"),
            "ecodms_folder_oid": selected.get("ecodms_folder_oid", "1.7.4"),
            "steuer_web_hint": selected["steuer_web_hint"],
        }
