# IceAI Tax 2025

**KI-gestuetzte Steuerbeleg-Pipeline fuer die private Einkommenssteuererklaerung 2025**

> Automatische Erfassung, OCR, Klassifizierung und Archivierung von Steuerbelegen
> mit lokaler KI (Ollama), Paperless-NGX, ecoDMS und Steuerbox-Integration.

---

## Ueberblick

IceAI Tax 2025 ist ein selbst-gehosteter Docker-Stack, der den kompletten Workflow
von der Belegerfassung bis zur fertigen Steuererklaerung automatisiert:

1. **Erfassen** - Belege aus E-Mail (IMAP), Webportalen (Amazon, eBay, Vodafone), Outlook-PST-Archiven und manueller Ablage einsammeln
2. **Erkennen** - OCR via Tesseract + Poppler fuer Bilder und PDFs
3. **Extrahieren** - Strukturierte Daten (Haendler, Betrag, Datum, MwSt) via lokalem LLM (Ollama/Qwen2.5)
4. **Klassifizieren** - Automatische Zuordnung zu Steuerkategorien (Werbungskosten, Sonderausgaben, Haushaltsnahe Leistungen)
5. **Archivieren** - Import nach Paperless-NGX und/oder ecoDMS
6. **Exportieren** - CSV-Export und E-Mail-Upload an Buhl Steuerbox fuer WISO Steuer

![Status Dashboard](docs/assets/screenshots/01-status-dashboard.png)

---

## Architektur

```
+------------------------------------------------------------------+
|                    DATENQUELLEN (Intake)                          |
|  +------------+ +------------+ +-------------+ +---------------+ |
|  | IMAP/Email | | Webportale | | PST/Outlook | | Manuelle      | |
|  | (Gmail,    | | (Amazon,   | | Archive     | | Ablage (SMB)  | |
|  |  iCloud,   | |  eBay,     | | (readpst)   | | /srv/taxdrop  | |
|  |  Proton)   | |  Vodafone) | |             | |               | |
|  +-----+------+ +-----+------+ +------+------+ +-------+-------+ |
+--------|--------------|--------------|-----------------|---------+
         |              |              |                 |
         v              v              v                 v
   +-------------------------------------------------------------+
   |                    PIPELINE INBOX                             |
   |              /data/inbox/ (alle Formate)                     |
   +---------------------------+---------------------------------+
                               |
                    +----------v-----------+
                    |    OCR & Extraktion   |
                    |  Tesseract + Poppler  |
                    |  (deu+eng, PDF->Text) |
                    +----------+-----------+
                               |
                    +----------v-----------+
                    |   Ollama LLM (lokal) |
                    |   Qwen 2.5 7B/14B    |
                    |   JSON-Schema-Output  |
                    +----------+-----------+
                               |
                    +----------v-----------+
                    |   Tax Classifier     |
                    |   (taxonomy.yml)     |
                    |   Regel + KI-basiert |
                    +----------+-----------+
                               |
         +---------------------+---------------------+
         |                     |                     |
   +-----v------+    +--------v--------+    +-------v--------+
   | ecoDMS     |    | CSV-Export       |    | Steuerbox      |
   | Archiv     |    | steuer_2025_    |    | Upload via      |
   | (optional) |    | export.csv      |    | E-Mail (SMTP)   |
   +------------+    +-----------------+    +----------------+
                               |
                    +----------v-----------+
                    |   Paperless-NGX      |
                    |   Dokumentenmgmt     |
                    |   (1381 Dokumente)   |
                    +----------------------+
```

---

## Komponenten

### Docker-Services (13 Container)

| Service | Image | Funktion | Port |
|---------|-------|----------|------|
| **paperless** | paperless-ngx:latest | Dokumentenmanagement & Volltextsuche | 8010 |
| **db** | postgres:16 | Paperless-Datenbank | - |
| **redis** | redis:7 | Paperless-Cache & Queue | - |
| **gotenberg** | gotenberg:8.11 | PDF-Konvertierung | - |
| **tika** | apache/tika:3.2.1 | Dokumenten-Parsing | - |
| **tax-pipeline** | python:3.13 + OCR | Haupt-Pipeline (OCR, Klassifizierung, Export) | - |
| **status-web** | python:3.13 | Web-Dashboard & Review-UI | 8787 |
| **portal-fetcher** | python:3.13 + Selenium | Portal-Automatisierung | - |
| **portal-browser** | selenium/chromium | Headless Browser + noVNC | 7900 |
| **proton-bridge** | protonmail-bridge | ProtonMail IMAP-Bridge | 1143 |
| **scan-mover** | alpine:3.19 | inotifywait: Scanner-SMB → Paperless Inbox | - |
| **paperless-ai** | clusterzx/paperless-ai | KI-Tagging via Ollama (Tags, Korrespondenten, Titel) | 3100 |
| **storage-path-sync** | tax-pipeline | Periodische Archiv-Einsortierung nach Person/Jahr | - |

### Python-Module

| Modul | Funktion |
|-------|----------|
| `main.py` | Pipeline-Orchestrator: Sammeln, OCR, Extraktion, Export |
| `classifier.py` | Regelbasierte Steuerkategorie-Zuordnung via taxonomy.yml |
| `ollama_client.py` | LLM-Extraktion: Haendler, Betrag, Datum, MwSt aus OCR-Text |
| `imap_collector.py` | Multi-Account IMAP-Sammler mit Regex-Filter |
| `pst_eml_collector.py` | Outlook PST/EML-Archiv-Extraktion via readpst |
| `portal_fetcher.py` | Selenium-basiertes Portal-Scraping (Amazon, eBay, Vodafone) |
| `steuerbox_uploader.py` | SMTP-basierter Upload an Buhl Steuerbox |
| `ecodms_client.py` | ecoDMS REST-API Client (Upload + Klassifizierung) |
| `ecodms_offline_importer.py` | ecoDMS Backup-Parser fuer Paperless-Migration |
| `paperless_importer.py` | Paperless-NGX API Client (Import + Tagging) |
| `status_server.py` | HTTP-Dashboard mit Status, Review-UI und Datei-Browser |
| `supplier_gap_report.py` | Pflichtanbieter-Lueckenanalyse |
| `setup_paperless_tags.py` | Erstellt Steuer-Tags, Korrespondenten und Storage Paths in Paperless |
| `paperless_storage_path_sync.py` | Sortiert KI-getaggte Dokumente in Archiv-Verzeichnisse |
| `config.py` | Zentrale Konfiguration aus Umgebungsvariablen |

---

## Datenfluss im Detail

### Phase 1: Belegerfassung

**IMAP-Sammler** (`imap_collector.py`):
- Durchsucht konfigurierte E-Mail-Konten (Gmail, iCloud, ProtonMail, lokaler Mailserver)
- Filtert Mails per Regex nach Rechnungsbegriffen (rechnung, invoice, bestellung, ...)
- Extrahiert PDF-Anhaenge oder speichert Mail-Body als Fallback (.txt)
- Zustandsverwaltung: Bereits verarbeitete UIDs werden in JSON-State gespeichert

**Portal-Fetcher** (`portal_fetcher.py`):
- Automatisierter Login via Selenium WebDriver + Cookie-Persistenz
- Amazon: Bestellhistorie durchlaufen, Rechnungs-PDFs herunterladen
- eBay: Kaufuebersicht scrapen, Rechnungen/Screenshots erfassen
- Vodafone: MeinVodafone API fuer Rechnungs-PDFs
- noVNC (Port 7900) fuer interaktiven Login bei 2FA/Captchas

**PST-Collector** (`pst_eml_collector.py`):
- Verarbeitet Outlook PST-Archive via `readpst`
- Extrahiert steuerrelevante Anhaenge aus EML-Dateien

**Manuelle Ablage**:
- SMB-Share unter `/srv/taxdrop` fuer manuelle Downloads etc.
- Dateien werden automatisch in die Pipeline-Inbox uebernommen

**Dokumentenscanner** (Scan-to-SMB):
- Brother MFC-J5730DW (oder anderer Scanner mit Scan-to-SMB)
- Scannt nach `\\<server>\scanner` (SMB-Share)
- `scan-mover` verschiebt PDFs automatisch in Paperless Inbox
- `paperless-ai` taggt per Ollama: Steuerkategorie, Korrespondent, Dokumenttyp, Titel, Steuerjahr

### Phase 2: OCR & Text-Extraktion

- **PDFs**: `pdftotext` (nativ) oder `pdftoppm` + Tesseract OCR (gescannte PDFs)
- **Bilder**: Tesseract OCR (deu+eng)
- **Textdateien**: Direkt verwendet (E-Mail-Body-Fallback)
- Max. 5 Seiten pro PDF, 4000 Zeichen an LLM

### Phase 3: KI-Extraktion (Ollama)

Das lokale LLM (Qwen 2.5) extrahiert strukturierte Daten:

```json
{
  "document_type": "invoice",
  "merchant": "Amazon EU S.a.r.L.",
  "invoice_number": "206-1234567-8901234",
  "invoice_date": "2025-01-15",
  "currency": "EUR",
  "gross_amount": 99.99,
  "net_amount": 84.02,
  "vat_amount": 15.97,
  "line_items_summary": "USB-C Hub, HDMI Adapter",
  "tax_relevance_hint": "Arbeitsmittel",
  "confidence": 0.95,
  "review_required": false
}
```

- JSON-Schema-Enforcement via `/api/chat`
- Fallback auf `/api/generate` bei kleineren GPUs
- Timeout: 120 Sekunden, danach Stub mit `confidence=0.0`

### Phase 4: Klassifizierung

Der `TaxClassifier` ordnet Kategorien zu:

| Kategorie | Beispiel-Keywords | Steuer-Hinweis |
|-----------|-------------------|----------------|
| `werbungskosten_arbeitsmittel` | Monitor, Tastatur, Laptop, Headset | Werbungskosten > Arbeitsmittel |
| `werbungskosten_fahrtkosten` | Bahn, Ticket, Fahrkarte, Tank | Werbungskosten > Fahrtkosten |
| `sonderausgaben_spenden` | Spende, Charity, Zuwendung | Sonderausgaben > Spenden |
| `haushaltsnahe_dienstleistungen` | Handwerker, Schornsteinfeger, Garten | Haushaltsnahe Leistungen |
| `sonstiges_pruefung` | (Fallback - manuelle Pruefung) | Manuelle Pruefung |

Zweistufig: 1) Haendler-Override, 2) Keyword-Match, 3) Fallback

### Phase 5: Deduplizierung

- **Hash-basiert**: SHA256 ueber Dateiinhalt
- **Semantisch**: Fingerprint aus Haendler + Datum + Rechnungsnummer + Betrag

### Phase 6: Export & Archivierung

- **CSV-Export**: `steuer_2025_export.csv` (sichere Belege) + `steuer_2025_pruefliste.csv` (Prueffaelle)
- **Steuerbox**: Per SMTP-Mail mit Anhang an Buhl Steuerbox -> WISO Steuer Import
- **ecoDMS**: REST-API Upload mit automatischer Klassifizierung
- **Paperless-NGX**: Langzeitarchiv mit Volltextsuche und hierarchischen Tags

---

## Scanner-Pipeline (Scan-to-AI)

Automatischer Workflow vom physischen Dokument bis zum fertig getaggten Archiv:

```
Brother Scanner (Scan-to-SMB)
    |
    v
/srv/scanner (Samba Share)
    |  scan-mover (inotifywait)
    v
/data/inbox (Paperless Consumer, 30s Polling)
    |
    v
Paperless-NGX (OCR via Tika/Gotenberg)
    |  paperless-ai (alle 5 Min)
    v
Ollama LLM (Qwen 2.5, lokal)
    |
    v
Tags, Korrespondenten, Dokumenttypen, Titel, Steuerjahr
    |  storage-path-sync (alle 10 Min)
    v
Archiv/{Person}/{Jahr}/{Korrespondent}/{Titel}.pdf
```

### Scanner-Betrieb

```bash
# Scanner-Pipeline starten (scan-mover + paperless-ai + storage-path-sync)
make scan-up

# Scanner-Pipeline stoppen
make scan-down

# Logs
make scan-logs      # scan-mover
make ai-logs        # paperless-ai

# Tags und Storage Paths in Paperless anlegen
make setup-tags

# Storage-Path-Zuordnung manuell ausfuehren
make sync-storage-paths
make sync-storage-paths-dry   # Vorschau
```

### Steuerkategorien (paperless-ai)

Die KI ordnet Dokumente automatisch folgenden Kategorien zu:

| Gruppe | Unterkategorien |
|--------|----------------|
| **Werbungskosten** | Arbeitsmittel, Fahrtkosten, Homeoffice, Fortbildung, Telekommunikation |
| **Hausverwaltung** | Nebenkosten, Miete |
| **Versicherungen** | Haftpflicht, Hausrat, KFZ, Leben, Berufsunfaehigkeit |
| **Gesundheit** | Arzt, Apotheke, Krankenkasse |
| **Bank** | Kontoauszug, Kreditkarte, Kredit |
| **Sonderausgaben** | Spenden |
| **Haushaltsnahe Leistungen** | Handwerker, Reinigung, Garten |

---

## Schnellstart

### Voraussetzungen
- Docker + Docker Compose
- Ollama-Server mit `qwen2.5:7b-instruct` Modell
- Optional: ecoDMS, Steuerbox-Konto

### Installation

```bash
git clone https://github.com/icepaule/IceAI-tax-2025.git
cd IceAI-tax-2025

# Bootstrap: Verzeichnisse anlegen, Container bauen
make bootstrap

# .env mit eigenen Zugangsdaten befuellen
cp .env.example .env
nano .env

# Stack starten
make up
```

### Betrieb

```bash
# Belege aus allen Quellen einsammeln
make collect

# Pipeline ausfuehren (OCR + Klassifizierung + Export)
make run

# Nur aktuelles Jahr
make run-current

# Status-Dashboard
make status
# -> http://localhost:8787

# Portal-Logins (interaktiv via noVNC)
make portal-login-amazon
make portal-login-ebay
make portal-login-vodafone

# Portal-Downloads
make portal-fetch-amazon
make portal-fetch-ebay
make portal-fetch-vodafone

# Pflichtanbieter-Lueckenanalyse
make supplier-gap

# ecoDMS-Migration nach Paperless
make migrate-ecodms-dry  # Testlauf
make migrate-ecodms      # Echter Import
```

---

## Web-Interfaces

### Status Dashboard (Port 8787)
- Uebersicht: Verarbeitete Belege, Fehler, Duplikate
- Letzte 10 Dokumente mit Haendler und Kategorie
- Pflichtanbieter-Status
- Manuelle Ablage und Portal-Downloads
- Export-Dateien
- Review-UI: Kategorien manuell korrigieren
- JSON-API: `/api/status`

### Paperless-NGX (Port 8010)
- Volltextsuche ueber alle archivierten Belege
- Hierarchische Tags (nach Steuerkategorie)
- Dokumentenvorschau und -download

![Paperless Login](docs/assets/screenshots/02-paperless-login.png)

### paperless-ai KI-Tagging (Port 3100)
- Automatische Verschlagwortung neuer Dokumente via Ollama
- Erkennt Personen, Steuerkategorien, Korrespondenten und Dokumenttypen
- Vergibt Steuerjahr-Tags basierend auf Belegdatum
- Scan-Intervall: alle 5 Minuten

![paperless-ai Dashboard](docs/assets/screenshots/05-paperless-ai-dashboard.png)
![paperless-ai Settings](docs/assets/screenshots/07-paperless-ai-settings.png)

### Selenium noVNC (Port 7900)
- Interaktiver Browser fuer Portal-Logins
- 2FA/Captcha-Eingabe bei Amazon, eBay, Vodafone

---

## Konfiguration

Alle Einstellungen in `.env` (siehe `.env.example`):

| Variable | Beschreibung |
|----------|-------------|
| `OLLAMA_BASE_URL` | URL des Ollama-Servers |
| `OLLAMA_MODEL` | LLM-Modell (z.B. `qwen2.5:7b-instruct-q4_1`) |
| `PAPERLESS_PORT` | Paperless-NGX Port (Standard: 8010) |
| `IMAP_ACCOUNTS_FILE` | Pfad zur Multi-Account IMAP-Config |
| `IMAP_FILTER_REGEX` | Regex fuer Rechnungs-Keywords |
| `ECODMS_AUTO_UPLOAD` | ecoDMS Upload aktivieren |
| `STEUERBOX_ENABLED` | Steuerbox-Upload aktivieren |
| `TAX_YEAR` | Steuerjahr (Standard: 2025) |
| `STATUS_WEB_PORT` | Dashboard-Port (Standard: 8787) |

IMAP-Konten werden in separaten YAML-Dateien konfiguriert (`config/imap_accounts.example.yml`).

---

## Steuerkategorien (taxonomy.yml)

Die Taxonomie definiert die Zuordnung von Belegen zu Steuerkategorien:

```yaml
categories:
  werbungskosten_arbeitsmittel:
    keywords: ["monitor", "tastatur", "drucker", "laptop", "headset"]
    steuer_web_hint: "Werbungskosten > Arbeitsmittel"
  werbungskosten_fahrtkosten:
    keywords: ["bahn", "ticket", "fahrkarte", "tank", "parken"]
    steuer_web_hint: "Werbungskosten > Fahrtkosten"
  sonderausgaben_spenden:
    keywords: ["spende", "charity", "zuwendungsbestaetigung"]
    steuer_web_hint: "Sonderausgaben > Spenden"
  haushaltsnahe_dienstleistungen:
    keywords: ["handwerker", "reinigung", "schornsteinfeger"]
    steuer_web_hint: "Haushaltsnahe Leistungen"

merchant_overrides:
  amazon: werbungskosten_arbeitsmittel
  ebay: werbungskosten_arbeitsmittel
```

---

## Projektstruktur

```
IceAI-tax-2025/
├── app/                          # Python-Anwendung
│   ├── main.py                   # Pipeline-Orchestrator
│   ├── classifier.py             # Steuerkategorie-Zuordnung
│   ├── ollama_client.py          # LLM-Extraktion
│   ├── imap_collector.py         # E-Mail-Sammler
│   ├── pst_eml_collector.py      # Outlook-Archive
│   ├── portal_fetcher.py         # Webportal-Scraping
│   ├── steuerbox_uploader.py     # Steuerbox SMTP-Upload
│   ├── ecodms_client.py          # ecoDMS API
│   ├── paperless_importer.py     # Paperless-NGX Import
│   ├── ecodms_offline_importer.py# ecoDMS Backup-Migration
│   ├── status_server.py          # Web-Dashboard
│   ├── supplier_gap_report.py    # Lueckenanalyse
│   ├── setup_paperless_tags.py   # Tag-/Storage-Path-Setup
│   ├── paperless_storage_path_sync.py # Archiv-Einsortierung
│   ├── config.py                 # Konfiguration
│   ├── requirements.txt          # Python-Abhaengigkeiten
│   └── requirements_portal.txt   # Portal-Abhaengigkeiten
├── config/                       # Konfigurationsdateien
│   ├── taxonomy.yml              # Steuerkategorien
│   ├── ollama_schema.json        # LLM JSON-Schema
│   ├── provider_sources.yml      # Pflichtanbieter
│   └── imap_accounts.example.yml # IMAP-Vorlage
├── docker/
│   └── tax-pipeline.Dockerfile   # Pipeline-Container
├── docs/                         # Dokumentation
│   └── assets/screenshots/       # Screenshots
├── scripts/                      # Hilfsskripte
├── docker-compose.yml            # Docker-Stack
├── Makefile                      # Automatisierung
├── .env.example                  # Umgebungsvariablen-Vorlage
└── .gitignore
```

---

## Sicherheit

- Alle Zugangsdaten ausschliesslich in `.env` (nicht im Repository)
- IMAP-Konfigurationen mit echten Daten in `.gitignore`
- Keine hardcodierten Passwoerter oder API-Keys im Code
- Paperless-NGX mit Token-Authentifizierung
- ecoDMS mit TLS (selbstsigniert moeglich)
- Steuerbox-Upload via STARTTLS
- Selenium-Browser ohne Passwort (nur im lokalen Netz verwenden)

---

## Technologie-Stack

- **Python 3.13** - Pipeline-Logik
- **Ollama + Qwen 2.5** - Lokale KI-Extraktion (kein Cloud-Upload!)
- **Tesseract OCR** - Optische Zeichenerkennung
- **Poppler** - PDF-Verarbeitung
- **Paperless-NGX** - Dokumentenmanagement
- **PostgreSQL 16** - Paperless-Datenbank
- **Redis 7** - Cache & Queue
- **Selenium + Chromium** - Portal-Automatisierung
- **Gotenberg** - PDF-Konvertierung
- **Apache Tika** - Dokumenten-Parsing
- **ProtonMail Bridge** - Verschluesselte E-Mail-Integration
- **Docker Compose** - Container-Orchestrierung

---

## Lizenz

Privates Projekt fuer die Einkommenssteuererklaerung 2025.

---

*Erstellt mit Claude Code (Anthropic) auf einem Intel NUC mit Home Assistant.*
