# Sprint 3B – Zwischenstand

Sprint 3C baut auf diesem Stand auf und ergänzt persistenzseitig immutable,
versionierte Nutzerentscheidungen für Staking-Plattformgebühren. Bestehende Kandidaten,
Reviews und Taxläufe bleiben unverändert; Migration 0009 legt ausschließlich
die neue Struktur an.

Sprint 3B erweitert den abgeschlossenen EUR-Bewertungsworkflow um die
providerneutrale FIFO-Engine, ein referenzbasiertes Steuerjournal,
Jahreszusammenfassungen sowie CSV- und PDF-Arbeitsdokumente.

Implementiert sind versionierte Regeln, stabile FIFO-Reihenfolge,
Teilzuordnungen, exakte Gebührenrestverteilung, Reviewfälle,
Idempotenz-Snapshots und supersedierende Rechenläufe. Migration 0006 bildet
Läufe, Lose, Zuordnungen, Berechnungen, Journal und Exporte mit Fremdschlüsseln,
Constraints und Indizes ab.

Die REST-API stellt Berechnungen, Bestände, Zuordnungen, Journal,
Zusammenfassung, Exporte und sichere Downloads bereit. Die deutsche UI ergänzt
Steuerübersicht, FIFO-Zuordnungen, Bestände, Steuerjournal und Exporte; sie
führt keine fachlichen Berechnungen aus.

Dauerhafte Scripte unter `scripts/` bündeln den schnellen Sammel-Preflight,
den isolierten PostgreSQL-Migrationszyklus und den vollständigen Sprint-3B-
Hostvalidator. Der Sprint bleibt offen, bis Backend-Coverage, beide
Datenbankdialekte und der vollständige Hostlauf tatsächlich erfolgreich
bestätigt sind.

## Nur lesende Live-Ledger-Vorbereitung

Als sichere Vorstufe des bestätigten Imports enthält der Arbeitsstand eine
nicht persistierende Kraken-Spot-Ledger-Vorschau. Signatur, Nonce,
Fehlerübersetzung, vollständige Offset-Pagination, Duplikatkonflikte und der
reproduzierbare Ledger-ID-Digest sind automatisiert prüfbar. Es existiert
keine Handels-, Einzahlungs- oder Auszahlungsfunktion.

## Kraken-Live-Ledger (in Arbeit)

Sprint 3B enthält zusätzlich den ausschließlich lesenden Private-REST-Adapter,
halboffene lokale UTC-Filterung, das kanonische CSV/API-Ledgermodell, den
reproduzierbaren Digest-Abgleich und einen ausdrücklich bestätigten atomaren
Import. Migration 0007 schützt die transportübergreifende Ledger-ID. Die UI
verlangt Vergleich und Bestätigung; Transformation bleibt optional, Bewertung
und FIFO werden nicht automatisch gestartet. Der Sprint bleibt bis zur
vollständigen Hostvalidierung offen.

## Kraken-Domaintransformation v2

Der gemeinsame Kraken-Normalisierer ist nun die einzige Quelle für CSV/API-
Abgleich, Live-Import und Domaintransformation. Er unterscheidet explizite
Aliase, eindeutige Identitätscodes und Produktvarianten mit getrenntem Marker
und Suffix. Rohcodes bleiben erhalten; eine Walletbedeutung wird nicht
erfunden.

Der aktive Vertrag `kraken-domain-v2` ersetzt die zu enge Assetklassifikation
von v1. Historische v1-Runs und Decisions bleiben unverändert. v2 verwendet
versionsstabile Domainobjektschlüssel, kennzeichnet vorhandene identische
Projektionen als wiederverwendet und erzeugt nur bislang fehlende Ereignisse.
Rewardgebühren bleiben nach dem bestehenden Vertrag Bestandteil des
AcquisitionLot: `gross_quantity` enthält den Zufluss, `fee_quantity` die
ausgewiesene Gebühr und `quantity` den Nettozufluss; ohne eigenständigen
Kraken-Abgang entsteht kein zusätzliches FeeEvent. Die Hostvalidierung dieses
Zwischenstands steht weiterhin aus.

## EUR-Bewertung von Staking-Rewards v2

Der aktive Vertrag `eur-valuation-v2` trennt Bruttoertrag, einbehaltene
Plattformgebühr und Netto-Anschaffungswert. `eur-valuation-v1` bleibt als
historischer Nettovertrag unverändert. Migration 0008 speichert die Komponenten
explizit und nullable, sodass alte Entscheidungen lesbar bleiben, ohne Werte zu
erfinden. Gebühren werden als steuerlich zu prüfender Kandidat ausgewiesen;
Inventory und FIFO verwenden ausschließlich die Nettomenge. Der Sprint bleibt
bis zum erneuten vollständigen Host-Preflight in Umsetzung.

## CoinGecko-Assetregister v2

Die explizite Bewertungs-Allowlist `coingecko-asset-map-v2` deckt die neun real
vorhandenen Staking-Assets ADA, ATOM, BTC, DOT, EIGEN, ETH, GRT, KAVA und XTZ
ab. ATOM verweist fest auf `cosmos`, EIGEN fest auf `eigenlayer`.
Automatische Symbolsuche und unscharfe Fallbacks bleiben ausgeschlossen;
unbekannte Assets erzeugen weiterhin einen Review. Der getrennte
Providerantwortvertrag `market-chart-range-v1` und bestehende Entscheidungen
werden nicht rückwirkend verändert.
Die Zuordnung wurde vor dem ersten Commit von v2 direkt gegen `/coins/list`
geprüft. Anzeigenamen können sich mit Projekt-Rebrandings ändern; technische
Identität liefern die explizite CoinGecko-ID und das Symbol.
