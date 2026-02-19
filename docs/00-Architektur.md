# Architektur

## Zielbild
- Paperless-ngx als Intake/Frontend
- Tax-Pipeline fuer Extraktion, Klassifikation, Export
- ecoDMS als finales Archiv
- Steuerbox als optionaler Upload-Kanal
- Status-Web fuer Betriebsuebersicht

## Datenfluss
1. IMAP/Datei-Intake nach `data/inbox`
2. Extraktion via Ollama
3. Regelbasierte Steuerkategorie
4. Upload und Klassifikation in ecoDMS (`mainfolder/folder`)
5. Optional Versand an Steuerbox
6. Exporte und Status in `data/exports` und `data/logs`

## Aktueller Zielordner ecoDMS
- Hauptordner OID: `1` (Marcus)
- Ordner OID: `1.7.5` (2026 fuer Steuerjahr 2025)
