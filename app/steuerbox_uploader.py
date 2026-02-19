import mimetypes
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Dict

from config import SETTINGS


class SteuerboxUploader:
    def enabled(self) -> bool:
        return (
            SETTINGS.steuerbox_enabled
            and bool(SETTINGS.steuerbox_email)
            and bool(SETTINGS.smtp_host)
            and bool(SETTINGS.smtp_from)
        )

    def send_document(self, file_path: Path, metadata: Dict[str, str]) -> str:
        if not self.enabled():
            return "disabled"

        msg = EmailMessage()
        msg["From"] = SETTINGS.smtp_from
        msg["To"] = SETTINGS.steuerbox_email
        msg["Subject"] = (
            f"{SETTINGS.steuerbox_subject_prefix} | {metadata.get('steuer_web_hint', 'Unkategorisiert')}"
        )

        body = (
            "Automatischer Upload aus tax-ai-stack\n\n"
            f"Datei: {file_path.name}\n"
            f"Kategorie: {metadata.get('steuer_web_hint', '')}\n"
            f"Haendler: {metadata.get('merchant', '')}\n"
            f"Belegdatum: {metadata.get('invoice_date', '')}\n"
            f"Betrag: {metadata.get('gross_amount', '')} {metadata.get('currency', '')}\n"
            f"Confidence: {metadata.get('confidence', '')}\n"
            f"Review required: {metadata.get('review_required', '')}\n"
        )
        msg.set_content(body)

        mime_type, _ = mimetypes.guess_type(file_path.name)
        maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
        msg.add_attachment(file_path.read_bytes(), maintype=maintype, subtype=subtype, filename=file_path.name)

        try:
            if SETTINGS.smtp_use_tls:
                with smtplib.SMTP(SETTINGS.smtp_host, SETTINGS.smtp_port, timeout=30) as smtp:
                    smtp.starttls()
                    if SETTINGS.smtp_user:
                        smtp.login(SETTINGS.smtp_user, SETTINGS.smtp_password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(SETTINGS.smtp_host, SETTINGS.smtp_port, timeout=30) as smtp:
                    if SETTINGS.smtp_user:
                        smtp.login(SETTINGS.smtp_user, SETTINGS.smtp_password)
                    smtp.send_message(msg)
        except Exception as exc:
            return f"error:{exc}"

        return "sent"
