# Paperless AI Plan

## Ziel
- ecoDMS-Bestand in Paperless migrieren.
- ecoDMS-Ordnerhierarchie in Paperless ueber hierarchische Tags nachbilden.
- AI-Nachklassifikation fuer unsichere Dokumente vorbereiten.

## Bereits umgesetzt
- Manifest-Builder fuer ecoDMS Offline-Export:
  - `app/ecodms_offline_importer.py`
  - Liest `backup.sql` + `offline_export/archive/export.xml` aus dem Export-ZIP.
  - Erzeugt `data/ecodms-export/ecodms_paperless_manifest.csv` mit:
    - `docid`, `archive_rel_path`, `original_name`, `created`, `doc_type`
    - `folder_oid`, `folder_path`
    - `tags_json` (hierarchisch, z. B. `ecodms/Marcus/Babsi/...`)
- Make Targets:
  - `make ecodms-manifest`
  - `make migrate-ecodms-offline-dry`
  - `make migrate-ecodms-offline`

## Migrationslogik
- Dokumentquelle: `offline_export/archive/ecodms_docid_*.{pdf,docx,...}` im ZIP.
- Ziel in Paperless:
  - Dokumenttyp aus ecoDMS (`dokumentenart`, fallback `ecoDMS Import`)
  - Tags:
    - `source:ecodms`
    - `migration:ecodms`
    - `ecodms_oid:<folder_oid>`
    - hierarchische Tags `ecodms/<Teilpfad>`

## Paperless-AI (empfohlen)
- Fokus auf Nachbearbeitung statt Primärimport:
  - Nur Dokumente mit Tag `migration:ecodms` + `review_required` nachklassifizieren.
  - Einheitliche Felder erzeugen (Lieferant, Rechnungsdatum, Betrag, Steuerrelevanz).
- Sinnvolle Pipeline:
  1. Rohimport (schnell, verlustfrei)
  2. AI-Tagging/Entity-Extraktion für Teilmengen (z. B. nur Steuerordner)
  3. QA-Regeln: keine automatische Loeschung/Umbenennung ohne Review

## E-Mail-Journal (separat)
- Paperless ist kein vollwertiges Mailjournal.
- Für revisionsnahe Mailarchivierung zusaetzlich pruefen:
  - OpenArchiver
  - Mailpiler
- Danach relevante Belege automatisiert nach Paperless spiegeln.

## MailStore Altbestände
- Migrationsstrategie:
  1. Export nach EML/MBOX/PST je Quelle
  2. Journal-System als Primärarchiv
  3. Nur Beleg-relevante Anhaenge per Regel nach Paperless
