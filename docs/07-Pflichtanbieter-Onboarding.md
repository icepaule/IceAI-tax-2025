# Pflichtanbieter Onboarding

Ziel: Rechnungen fuer das Steuerjahr 2025 aus Pflichtquellen regelmaessig in den Pipeline-Intake bringen.

## Quellen
| Anbieter | Primärweg | Fallback | Zielpfad |
|---|---|---|---|
| Vodafone | Portal-Download (monatliche Rechnung PDF) | E-Mail-Anhaenge | `/srv/taxdrop` |
| Hetzner | E-Mail-Anhaenge + Portal-Download | manuell aus Konsole | `/srv/taxdrop` |
| Tibber | Portal/App-Rechnungen (PDF) | E-Mail-Anhaenge | `/srv/taxdrop` |
| goldgas.de (Vertrag 21716206) | Portal-Download (Abschlag/Jahresabrechnung) | E-Mail-Anhaenge | `/srv/taxdrop` |

## Benötigte Angaben pro Anbieter
- Portal-Login (Benutzername/Kundennummer)
- Passwort oder App-Passwort (falls vorhanden)
- 2FA-Status (ja/nein, TOTP/SMS/E-Mail)
- Abrechnungsrhythmus (monatlich/jaehrlich)
- Erwartete Dokumenttypen (Rechnung, Abschlag, Jahresabrechnung)

## Technischer Ablauf
1. Portalrechnung herunterladen.
2. Datei nach `/srv/taxdrop` legen.
3. Pipeline ausfuehren: `make run`.
4. Ergebnis pruefen:
   - `data/exports/steuer_2025_export.csv`
   - `data/exports/steuer_2025_pruefliste.csv`
   - `data/exports/supplier_gap_2025.csv`

## Offene Punkte
- Vodafone, Tibber, goldgas.de sind aktuell noch `MISSING` im Gap-Report.
- Hetzner wurde bereits erkannt, sollte aber weiterhin monatlich geprueft werden.
