# Sprint 3B – FIFO, Steuerjournal und Exporte

## Ziel und sichtbares Nutzerergebnis

Sprint 3B ergänzt die bewerteten fachlichen Ereignisse um eine deterministische
FIFO-Zuordnung, ein unveränderliches Steuerjournal, Jahresauswertungen sowie
CSV- und PDF-Arbeitsdokumente. Die deutsche Weboberfläche zeigt Berechnungen,
Bestände, Zuordnungen, Journal und Exporte ausschließlich aus Backenddaten.

## Ausgangslage

Sprint 3A ist abgeschlossen. Import, Transformation, EUR-Bewertung,
Provider-Evidenz, manuelle Kurse, Reviewfälle, Same-Origin-Weboberfläche und
beide unterstützten Datenbankdialekte bilden die geprüfte Eingabeschicht.

## Scope

- versionierte FIFO-, Gebühren-, Klassifikations-, Journal- und Exportregeln
- partielle und losübergreifende Zuordnungen mit exakter Decimal-Arithmetik
- unveränderliche Bestands-, Zuordnungs-, Berechnungs- und Journalnachweise
- idempotente, supersedierbare Steuerberechnungsläufe
- Jahreszusammenfassung und gemeinsame Reviewdarstellung
- sechs CSV-Arbeitsdateien und ein serverseitiger PDF-Steuerbericht
- typisierte Listen-, Detail-, Berechnungs-, Export- und Download-Endpunkte
- UI-Seiten Steuerübersicht, FIFO, Bestände, Steuerjournal und Exporte
- Migration `0006_fifo_tax_journal_exports` für SQLite und PostgreSQL
- dauerhafte Preflight-, PostgreSQL- und Hostvalidierungsscripte

## Fachliche Verträge

FIFO sortiert Erwerbslose nach UTC-Zeitpunkt und technischer UUID. Erwerbs- und
Veräußerungsgebühren werden proportional verteilt; die letzte Teilzuordnung
erhält die exakte Decimal-Restdifferenz. Fehlende Bewertung, unvollständige
Historie, Kryptogebühr ohne nachgewiesenen Bestandsabgang oder zu geringer
Bestand erzeugen einen Reviewfall statt einer Schätzung.

Ein Rechenlauf ist durch Zeitraum, Daten-Snapshot und Regel-Fingerprint
idempotent. Abweichende Daten oder Regeln erzeugen einen neuen Lauf mit
`supersedes_id`; abgeschlossene Detailnachweise bleiben erhalten.

## Nicht-Ziele

Nicht enthalten sind automatische Verkäufe, Handelsausführung, Empfehlungen,
Benachrichtigungen, ELSTER, Steuererklärungsübermittlung, Mehrmandantenbetrieb,
Cloud-Synchronisation und individuelle Steuerberatung.

## Akzeptanzkriterien und Definition of Done

Die fachlichen Domain-, API-, Export-, UI-, Sicherheits- und
Migrationsverträge werden synthetisch getestet. Backend-Coverage bleibt bei
100 Prozent. Ruff, Black, MyPy strict, Backend- und Frontendtests, ESLint,
Typecheck, Produktionsbuild, Markdownlint, SQLite, PostgreSQL, Docker-Smoke,
Same-Origin-Download und `git diff --check` müssen erfolgreich sein.

## Status

**In Umsetzung.** Die Implementierung ist vorbereitet; die vollständige
Hostvalidierung und sämtliche Qualitätsgates stehen vor der Freigabe noch aus.

## Ergänzung: bestätigter Kraken-Live-Ledger-Import

Der Sprint umfasst einen nur lesenden CSV–API-Abgleich und einen durch Digest
und ausdrückliche Bestätigung gesicherten Live-Ledger-Import. Diese Ergänzung
ändert den offenen Sprintstatus nicht. Transformation ist opt-in; Bewertung,
FIFO und Handelsaktionen werden nicht automatisch ausgelöst.

Die aktive Kraken-Domaintransformation trägt die Vertragsversion
`kraken-domain-v2`. Sie verwendet dieselbe generische Assetidentität wie der
CSV/API-Abgleich und erzeugt für syntaktisch gültige neue Assets keine
Alias-Reviews. v1-Läufe bleiben unveränderte Historie; identische vorhandene
Domainobjekte werden in v2 wiederverwendet. Diese Ergänzung ändert den Status
**In Umsetzung** nicht.

Die aktive EUR-Methodenversion ist `eur-valuation-v2`. Für Staking-Rewards
werden Bruttoertrag, Gebührenkandidat und Netto-Anschaffungswert getrennt
nachgewiesen. Der historische v1-Nettovertrag bleibt unverändert; Migration
0008 füllt alte Entscheidungen nicht künstlich auf. Ein Steuerabzug der
Plattformgebühr wird nicht automatisch bestätigt. Sprint 3B bleibt offen.
