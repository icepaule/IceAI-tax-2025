# Betrieb

## Regelbetrieb
- Sammeln + Verarbeiten: `make run`
- Nur sammeln: `make collect`
- Nur Testlauf: `make dry-run`
- Portal-Fetcher starten: `make portal-up`
- Host-Samba Share nutzen: `//<host>/taxdrop`
- Amazon Login/FETCH: `make portal-login-amazon` / `make portal-fetch-amazon`
- eBay Login/FETCH: `make portal-login-ebay` / `make portal-fetch-ebay`
- Vodafone Login/FETCH: `make portal-login-vodafone` / `make portal-fetch-vodafone`

## Jahreslogik
- Standard: verarbeitet immer das Vorjahr (z. B. in 2027 automatisch Steuerjahr 2026).
- Aktuelles Jahr (Vorschau): `make run-current` oder `make collect-current`
- Explizites Jahr: `make run-year YEAR=2026` oder `make collect-year YEAR=2026`

## ecoDMS Backup vorbereiten
- Backup-ZIP nach `/srv/ecodms-export/` kopieren (z. B. `dmsbackup_2026-02-15_12_33_48.zip`).
- Vorbereitung starten:
  - `make prepare-ecodms-backup`
- Der Schritt prueft:
  - Datei vorhanden und nicht mehr wachsend
  - ZIP-Integritaet (`zip -T`)
  - Entpacken nach `data/ecodms-export/raw`

## ecoDMS Offline-Export nach Paperless
- Manifest aus `backup.sql` + `offline_export/archive/export.xml` erstellen:
  - `make ecodms-manifest`
- Testimport (ohne Upload):
  - `make migrate-ecodms-offline-dry`
- Echter Import:
  - `make migrate-ecodms-offline`
- Wichtige Voraussetzung:
  - `PAPERLESS_API_TOKEN` setzen
  - oder gueltige `PAPERLESS_ADMIN_USER` + `PAPERLESS_ADMIN_PASSWORD`

## Lieferanten-Lueckenreport
- Report erzeugen:
  - `make supplier-gap`
- Ausgabe:
  - `data/exports/supplier_gap_2025.csv`
- Ziel:
  - Schnell sehen, ob Pflichtquellen (z. B. Vodafone, Hetzner, Tibber, Gruengas) bereits in den verarbeiteten Belegen auftauchen.

## Monitoring
- Status UI: `http://<host>:8787`
- JSON Status: `/api/status`
- Logs: `data/logs/last_run.json`, `data/logs/status.json`

## Fehlerbild
- ecoDMS `401`: Credentials/Quoting in `.env` pruefen
- IMAP timeout: Routing/Port/Firewall pruefen
- Steuerbox timeout: SMTP Erreichbarkeit pruefen
