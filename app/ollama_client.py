import json
from pathlib import Path
from typing import Any, Dict

import requests

from config import SETTINGS


class OllamaClient:
    def __init__(self, schema_path: str = "/config/ollama_schema.json") -> None:
        self.base_url = SETTINGS.ollama_base_url.rstrip("/")
        self.model = SETTINGS.ollama_model
        self.timeout = SETTINGS.ollama_timeout_seconds
        self.schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))

    def extract(self, document_text: str) -> Dict[str, Any]:
        prompt = (
            "Du bist ein Extraktionsmodell fuer private Steuerbelege in Deutschland. "
            "Lies den Belegtext und liefere nur Daten gemaess JSON-Schema. "
            "Wenn ein Feld fehlt, nutze leere Strings oder 0.0. "
            "Sei konservativ und setze review_required=true bei Unsicherheit.\n\n"
            f"Belegtext:\n{document_text[: SETTINGS.ollama_max_prompt_chars]}"
        )
        raw_content = "{}"
        payload = {
            "model": self.model,
            "stream": False,
            "format": self.schema,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "options": {"temperature": 0, "num_predict": 256},
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=(10, self.timeout),
            )
            if response.status_code < 300:
                data = response.json()
                raw_content = data.get("message", {}).get("content", "{}")
            elif response.status_code == 404:
                raise requests.HTTPError("chat endpoint not available")
            else:
                response.raise_for_status()
        except requests.RequestException:
            # Runtime fallback for slow/fragile chat+schema path on smaller GPUs:
            # retry via /api/generate with compact prompt and JSON mode.
            compact_chars = min(800, max(300, SETTINGS.ollama_max_prompt_chars))
            compact_prompt = (
                "Extrahiere nur JSON fuer Steuerbeleg-Felder. "
                "Fehlende Felder als leer/0.0 und review_required=true bei Unsicherheit.\n\n"
                f"Belegtext:\n{document_text[:compact_chars]}"
            )
            generate_payload = {
                "model": self.model,
                "stream": False,
                "format": "json",
                "prompt": compact_prompt,
                "options": {"temperature": 0, "num_predict": 256},
            }
            fallback = requests.post(
                f"{self.base_url}/api/generate",
                json=generate_payload,
                timeout=(10, min(self.timeout, 90)),
            )
            fallback.raise_for_status()
            data = fallback.json()
            raw_content = data.get("response", "{}")
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            parsed = {
                "document_type": "unknown",
                "merchant": "",
                "invoice_number": "",
                "invoice_date": "",
                "currency": "EUR",
                "gross_amount": 0.0,
                "net_amount": 0.0,
                "vat_amount": 0.0,
                "line_items_summary": "",
                "tax_relevance_hint": "",
                "confidence": 0.0,
                "review_required": True,
            }
        return parsed
