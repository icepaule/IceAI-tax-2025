# ToDo

## Offene Belegquellen 2025
- [ ] Vodafone: Rechnungen/Belege manuell aus Kundenportal exportieren und nach `/srv/taxdrop` legen.
- [ ] Hetzner: Rechnungen/Belege manuell aus Robot/Cloud-Konsole exportieren und nach `/srv/taxdrop` legen.
- [ ] Tibber: Rechnungen/Belege manuell aus App/Portal exportieren und nach `/srv/taxdrop` legen.
- [ ] Gruengas: Rechnungen/Belege manuell aus Kundenportal exportieren und nach `/srv/taxdrop` legen.
- [ ] Nach jedem Portal-Export `make collect` laufen lassen.

## Optional: Home Assistant MQTT Energiekosten
- [ ] Pruefen, welche MQTT Topics fuer Gesamtverbrauch/Bezug in HA vorhanden sind (kWh, Zeitraum).
- [ ] Tarifbasis festlegen (Arbeitspreis ct/kWh, Grundpreis anteilig monatlich/jahrlich).
- [ ] Importer bauen: MQTT/HA-Werte fuer 2025 in CSV exportieren (`data/exports/energie_2025.csv`).
- [ ] Kostenberechnung ableiten und in Steuer-Pruefliste aufnehmen.
- [ ] Relevanz fuer private Steuererklaerung dokumentieren (i. d. R. nur Sonderfaelle, z. B. Arbeitszimmer-Anteil).

