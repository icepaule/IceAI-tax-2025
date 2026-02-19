import os
from dataclasses import dataclass
from datetime import datetime
from dotenv import load_dotenv


load_dotenv()
DEFAULT_TAX_YEAR = datetime.now().year - 1


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    return value in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    paperless_url: str = os.getenv("PAPERLESS_URL", "http://127.0.0.1:8010")
    paperless_api_token: str = os.getenv("PAPERLESS_API_TOKEN", "")
    paperless_admin_user: str = os.getenv("PAPERLESS_ADMIN_USER", "")
    paperless_admin_password: str = os.getenv("PAPERLESS_ADMIN_PASSWORD", "")

    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:14b-instruct")
    ollama_timeout_seconds: int = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120"))
    ollama_max_prompt_chars: int = int(os.getenv("OLLAMA_MAX_PROMPT_CHARS", "4000"))
    ocr_langs: str = os.getenv("OCR_LANGS", "deu+eng")
    ocr_pdf_max_pages: int = int(os.getenv("OCR_PDF_MAX_PAGES", "5"))
    process_text_docs: bool = _bool("PROCESS_TEXT_DOCS", False)

    ecodms_base_url: str = os.getenv("ECODMS_BASE_URL", "")
    ecodms_username: str = os.getenv("ECODMS_USERNAME", "")
    ecodms_password: str = os.getenv("ECODMS_PASSWORD", "")
    ecodms_skip_tls_verify: bool = _bool("ECODMS_SKIP_TLS_VERIFY", True)
    ecodms_classification_path: str = os.getenv(
        "ECODMS_CLASSIFICATION_PATH", "\\Steuer\\2025\\Privat"
    )
    ecodms_auto_upload: bool = _bool("ECODMS_AUTO_UPLOAD", False)
    ecodms_mainfolder_oid: str = os.getenv("ECODMS_MAINFOLDER_OID", "1")
    ecodms_default_folder_oid: str = os.getenv("ECODMS_DEFAULT_FOLDER_OID", "1.7.4")
    ecodms_version_controlled: bool = _bool("ECODMS_VERSION_CONTROLLED", False)

    tax_year: int = int(os.getenv("TAX_YEAR", str(DEFAULT_TAX_YEAR)))
    default_currency: str = os.getenv("DEFAULT_CURRENCY", "EUR")
    export_filename: str = os.getenv("EXPORT_FILENAME", f"steuer_{DEFAULT_TAX_YEAR}_export.csv")
    unsicher_filename: str = os.getenv(
        "UNSICHER_FILENAME", f"steuer_{DEFAULT_TAX_YEAR}_pruefliste.csv"
    )
    reference_2024_path: str = os.getenv("REFERENCE_2024_PATH", "/data/reference")
    imap_host: str = os.getenv("IMAP_HOST", "")
    imap_port: int = int(os.getenv("IMAP_PORT", "993"))
    imap_user: str = os.getenv("IMAP_USER", "")
    imap_password: str = os.getenv("IMAP_PASSWORD", "")
    imap_accounts_file: str = os.getenv("IMAP_ACCOUNTS_FILE", "/config/imap_accounts.yml")
    imap_folder: str = os.getenv("IMAP_FOLDER", "INBOX")
    imap_use_ssl: bool = _bool("IMAP_USE_SSL", True)
    imap_search: str = os.getenv(
        "IMAP_SEARCH",
        f"SINCE 01-Jan-{DEFAULT_TAX_YEAR} BEFORE 01-Jan-{DEFAULT_TAX_YEAR + 1}",
    )
    imap_max_messages: int = int(os.getenv("IMAP_MAX_MESSAGES", "400"))
    imap_filter_regex: str = os.getenv(
        "IMAP_FILTER_REGEX",
        r"(rechnung|invoice|bestellung|bestellbest(?:a|ä)tigung|auftragsbest(?:a|ä)tigung|"
        r"quittung|kassenbon|beleg|order confirmation|receipt|tax invoice|"
        r"amazon|ebay|kleinanzeigen|aliexpress|az-delivery|az delivery)",
    )
    imap_exclude_regex: str = os.getenv(
        "IMAP_EXCLUDE_REGEX",
        r"(phishing|malware|spam|newsletter|xing|trusted shops|cape malware analysis|phishcheck)",
    )
    imap_body_fallback_enabled: bool = _bool("IMAP_BODY_FALLBACK_ENABLED", True)
    imap_body_max_chars: int = int(os.getenv("IMAP_BODY_MAX_CHARS", "120000"))
    imap_mark_seen: bool = _bool("IMAP_MARK_SEEN", False)
    imap_timeout_seconds: int = int(os.getenv("IMAP_TIMEOUT_SECONDS", "30"))
    steuerbox_enabled: bool = _bool("STEUERBOX_ENABLED", False)
    steuerbox_email: str = os.getenv("STEUERBOX_EMAIL", "")
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_use_tls: bool = _bool("SMTP_USE_TLS", True)
    smtp_from: str = os.getenv("SMTP_FROM", "")
    steuerbox_subject_prefix: str = os.getenv(
        "STEUERBOX_SUBJECT_PREFIX", f"Steuerbeleg {DEFAULT_TAX_YEAR}"
    )
    status_web_host: str = os.getenv("STATUS_WEB_HOST", "0.0.0.0")
    status_web_port: int = int(os.getenv("STATUS_WEB_PORT", "8787"))
    manual_drop_dir: str = os.getenv("MANUAL_DROP_DIR", "/data/manual-drop")
    ecodms_export_dir: str = os.getenv("ECODMS_EXPORT_DIR", "/data/ecodms-export")
    portal_collect_enabled: bool = _bool("PORTAL_COLLECT_ENABLED", True)


SETTINGS = Settings()
