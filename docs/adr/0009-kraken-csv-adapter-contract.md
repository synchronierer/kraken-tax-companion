# ADR 0009: Kraken CSV Adapter Contract

- Status: Accepted
- Date: 2026-07-30

## Context

Kraken Ledger History und Trades History besitzen unterschiedliche,
veränderliche CSV-Schemata. Rohimport, providerbezogene Strukturprüfung und
spätere steuerliche Interpretation müssen getrennt bleiben.

## Decision

Ein SQLAlchemy-freier Adapter unter `app/adapters/kraken/` erkennt normalisierte
Header deterministisch. Ledger und Trades sind getrennte Importarten mit
eigenen Quellen und Vertragsversionen. Der Dateiname ist nur Provenienz.

Originalheader und -werte bleiben erhalten. Unbekannte Zusatzfelder und
zukünftige Typ-, Subtyp- und Ordertypwerte sind zulässig. Asset-Codes werden
nicht normalisiert. Eine typisierte Ansicht mit UTC-`datetime` und `Decimal`
ergänzt die Evidenz, ersetzt sie aber nicht.

Die gesamte Datei wird vor `ImportService` validiert. Probleme werden möglichst
gemeinsam mit Code, Zeile und Feld zurückgegeben; kein Record wird persistiert.
Die generische Engine bleibt für Hash, Idempotenz, Transaktion, Persistenz und
Audit zuständig.

Earn- und Staking-Einträge sind ausschließlich Rohdaten. Es gibt keine
steuerliche Bewertung und keine Erzeugung von `EarnLot`, `Sale`, Journal-,
FIFO- oder Empfehlungsobjekten. Überlappende Exporte dürfen dieselbe externe
Transaktions-ID enthalten. Dateiübergreifende Zusammenführung und
Deduplizierung folgen erst in Sprint 2D.

## Consequences

- Unbekannte Kraken-Erweiterungen bleiben revisionssicher.
- Ungültige Dateien werden atomar abgewiesen.
- CSV-Inhalte werden nie ausgeführt. Eine spätere Spreadsheet-Ausgabe muss
  führende `=`, `+`, `-` und `@` sicher darstellen, ohne Rohdaten zu ändern.
- Keine Migration oder Änderung des Sprint-2B-Hashvertrags ist nötig.
