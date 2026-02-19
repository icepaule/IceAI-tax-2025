# tax-ai-stack (Paperless + Ollama + ecoDMS)

Erster Test-Stack fuer private Steuerbelege 2025.

## Ziel
- Belege in `data/inbox/` einlesen
- OCR fuer Bilder/PDFs (Tesseract + Poppler)
- Via Ollama strukturierte Daten extrahieren
- Steuerkategorie zuordnen
- CSV fuer Steuer-Import erzeugen
- Optional Upload nach ecoDMS via API

## Schnellstart
1. `cd /root/tax-ai-stack`
2. `make bootstrap`
3. `cp -n .env.example .env`
4. `.env` mit deinen Zugangsdaten befuellen
5. `make up`
6. Testdatei in `data/inbox/` legen
7. `make dry-run`
8. Fuer IMAP-Einsammeln: `docker compose run --rm tax-pipeline sh -c "pip install --no-cache-dir -r requirements.txt && python main.py --mode collect"`
9. Status-Web starten: `make status` und dann `http://<host>:8787`
10. Portal-Fetcher starten: `make portal-up`

## Ausgaben
- `data/exports/steuer_2025_export.csv`
- `data/exports/steuer_2025_pruefliste.csv`
- `data/exports/vergleich_2024_2025.csv`
- `data/logs/last_run.json`
- `data/logs/status.json`
- `data/logs/processed_semantic.json`

## 2024 Referenz
Lege dein Export-Backup als `CSV` oder `JSON` nach `data/reference/`.

## ecoDMS
Aktiviere Upload ueber `.env`:
- `ECODMS_AUTO_UPLOAD=true`
- `ECODMS_USERNAME`, `ECODMS_PASSWORD` setzen

Hinweis: API-Endpunkte koennen je nach ecoDMS Version/Lizenz abweichen. Der Upload-Call ist als testbarer Integrations-Stub implementiert.

## Steuerbox
Der Upload erfolgt per E-Mail mit Anhang an die Steuerbox-Adresse.

Pflichtfelder in `.env`:
- `STEUERBOX_ENABLED=true`
- `STEUERBOX_EMAIL=<deine-steuerbox-id>@buhl-steuerbox.de`
- `SMTP_HOST`, `SMTP_PORT`
- `SMTP_FROM=deine-email@example.de`
- Optional Auth: `SMTP_USER`, `SMTP_PASSWORD`
- Bei selbstsigniertem Mailserver: Port `25` + `SMTP_USE_TLS=true` (STARTTLS) bevorzugen.

## Status-Web
- HTML Dashboard: `/`
- JSON API: `/api/status`
- Zeigt letzte 10 Dokumente, ecoDMS/Steuerbox-Status, offene Inbox-Dokumente, IMAP-Zaehler.
- Review UI (klickbar): `/review`
  - Kategorie per Dropdown setzen
  - "Pruefung noetig" per Dropdown setzen
  - reviewed CSV erzeugen: `steuer_2025_export_reviewed.csv`

## Portal-Fetcher (Amazon/eBay)
- noVNC Browser starten: `make portal-up`
- noVNC URL: `http://<host>:7900`
- Login-Session speichern:
  - `make portal-login-amazon`
  - `make portal-login-ebay`
  - `make portal-login-vodafone`
- Downloadlauf fuer 2025:
  - `make portal-fetch-amazon`
  - `make portal-fetch-ebay`
  - `make portal-fetch-vodafone`
- Downloads landen in `data/portal/downloads/<portal>/2025/`
- Wenn keine Rechnung erkennbar ist, werden Fallback-Screenshots unter `data/portal/screenshots/<portal>/2025/` erzeugt.
- Dedupe aktiv: identische Downloads werden per SHA256 entfernt.
- Lege die Downloads in den Host-Share `//<host>/taxdrop` (oder direkt nach `/srv/taxdrop`).

## Samba Share (manuelle Ablage)
- Host-Samba Share: `taxdrop`
- Share-Pfad: `/srv/taxdrop`
- Zugriff: Gast / world-read-writable (unsicher, nur in vertrautem LAN verwenden)
- Standard SMB Port 445 (Host-Samba)

Beispiel Linux Mount:
`sudo mount -t cifs //<host-ip>/taxdrop /mnt/taxdrop -o guest,vers=3.0`

Dateien aus `/srv/taxdrop` werden bei jedem Pipeline-Lauf automatisch nach `data/inbox/` uebernommen.
Die Pipeline dedupliziert Belege per SHA256 (`data/logs/processed_hashes.json`) und semantisch per OCR-Daten (`data/logs/processed_semantic.json`): Verkäufer + Datum + Rechnungsnr. + Produkt / Betrag.

## Projektdokumentation
- `docs/00-Architektur.md`
- `docs/01-Installation.md`
- `docs/02-Konfiguration.md`
- `docs/03-Betrieb.md`
- `docs/04-Bedienungsanleitung.md`
- `docs/05-Screenshots.md`
- `docs/06-TODO.md`
- `docs/07-paperless-ai-plan.md`

## Modell-Empfehlung fuer Ollama
- Standard: `qwen2.5:14b`
- Optional fuer bessere Tabellen-/Extraktionstreue: `qwen2.5:32b-instruct` (mehr RAM/GPU noetig)

## OCR
- OCR ist in der Tax-Pipeline integriert (PNG/JPG/PDF).
- Konfiguration:
  - `OCR_LANGS=deu+eng`
  - `OCR_PDF_MAX_PAGES=5`

## IMAP Filter (historische Mails)
- Beispiel fuer Vorjahr: `IMAP_SEARCH=SINCE 01-Jan-2025 BEFORE 01-Jan-2026`
- `IMAP_FILTER_REGEX` durchsucht Absender, Betreff, Anhangnamen und Mailtext nach Beleg-/Rechnungsbegriffen.
- Falls keine Anhaenge vorhanden sind, kann der Mailtext als `.txt` gespeichert werden:
  - `IMAP_BODY_FALLBACK_ENABLED=true`
- Mehrere Postfaecher:
  - `IMAP_ACCOUNTS_FILE=/config/imap_accounts.yml`
  - Vorlage: `config/imap_accounts.example.yml`

## ecoDMS -> Paperless Migration
- Lege einen ecoDMS Exportbaum unter `data/ecodms-export/` ab (Ordnerstruktur bleibt erhalten).
- Dry-Run:
  - `make migrate-ecodms-dry`
- Echter Import nach Paperless:
  - `make migrate-ecodms`
- Der Import setzt:
  - Dokumenttyp: `ecoDMS Import`
  - Tags: `source:ecodms`, `migration:ecodms` und hierarchische Tags wie `ecodms/Steuer/2026 fuer Steuerjahr 2025/...`
- Auth:
  - bevorzugt `PAPERLESS_API_TOKEN`
  - alternativ `PAPERLESS_ADMIN_USER` + `PAPERLESS_ADMIN_PASSWORD` (Token wird automatisch geholt)
