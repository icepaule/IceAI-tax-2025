# Konfiguration

## Pflichtwerte in `.env`
- `OLLAMA_BASE_URL`, `OLLAMA_MODEL`
- `ECODMS_BASE_URL`, `ECODMS_USERNAME`, `ECODMS_PASSWORD`
- `ECODMS_MAINFOLDER_OID=1`
- `ECODMS_DEFAULT_FOLDER_OID=1.7.5`
- `STEUERBOX_EMAIL=<deine-steuerbox-id>@buhl-steuerbox.de`
- `SMTP_FROM=deine-email@example.de`

## Sicherheit
- Geheimnisse nur in `.env`, nie im Code
- Bei `#` im Passwort Wert in Anfuehrungszeichen setzen, z. B. `"...##"`

## Mapping
- `config/taxonomy.yml` steuert Kategorie -> ecoDMS OID -> Steuer-Hinweis

## Portal-Fetcher
- Sessions werden unter `data/portal/state/` gespeichert.
- Downloads unter `data/portal/downloads/`.
- Fuer Amazon/eBay zuerst `portal-login-*`, danach `portal-fetch-*`.

## Samba Drop
- `MANUAL_DROP_DIR=/manual-drop`
- Host-Samba liefert die Dateien aus `/srv/taxdrop` in diesen Container-Pfad.
- Alles im Share wird automatisch in den Pipeline-Inbox (`data/inbox`) verschoben.
