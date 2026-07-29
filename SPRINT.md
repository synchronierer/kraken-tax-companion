# Sprint 2C – Kraken CSV Adapter

## Ziel

Kraken-Exporte für Ledger History und Trades History werden deterministisch
erkannt, vollständig validiert und als geordnete Rohdatensätze an die
generische Import Engine übergeben.

## Ausgangslage

Sprint 2B stellt atomaren Batchimport, kanonischen Hash, Idempotenz, Audit,
externe IDs und technische Metadaten bereit.

## Scope

- abgegrenzter Kraken-Adapter unter `backend/app/adapters/kraken/`;
- UTF-8-CSV-Verarbeitung mit der Standardbibliothek;
- unveränderliche DTOs für Ledger-, Trade- und Batchdaten;
- gesammelt zurückgegebene, strukturierte Validierungsprobleme;
- atomare Übergabe vollständig gültiger Dateien an `ImportService`;
- synthetische Unit-, Integrations- und Architekturtests.

## Nicht-Ziele

- Kraken-API, Zugangsdaten, PDF- oder Balance-Snapshot-Import;
- Asset-Normalisierung oder Zusammenführung von Trades und Ledgers;
- EarnLots, Sales, Steuerjournal, FIFO, Empfehlungen oder Steuerbewertung.

## Unterstützte Exporttypen

- `kraken-ledgers-csv`, Quelle `kraken-ledgers`, Vertrag
  `kraken-ledgers-csv-v1`;
- `kraken-trades-csv`, Quelle `kraken-trades`, Vertrag
  `kraken-trades-csv-v1`.

## Adaptervertrag

Der Dateiname ist beschreibende Provenienz. Identität entsteht aus Quelle und
geordnetem Record-Hash. Originalheader, Originalwerte, Zusatzfelder,
Quelldateizeile und Kraken-Transaktions-ID bleiben erhalten.

## Validierung

Header werden nach BOM-Entfernung, Trimmen und Kleinschreibung verglichen.
Kollisionen, gemischte oder unbekannte Schemata, beschädigtes UTF-8,
Semikolondateien, fehlerhafte Zeilen, Zeit- und Decimalwerte sowie doppelte
`txid` werden maschinenlesbar gemeldet. Unbekannte Felder und Typwerte bleiben
zulässig.

## Akzeptanzkriterien

- Ledger und Trades werden ohne Dateinamenheuristik erkannt.
- Alle Zeilen sind gültig oder es findet keine Persistenz statt.
- Zeitwerte sind aware UTC; Finanzwerte verwenden ausschließlich `Decimal`.
- LF/CRLF, BOM und Dateiname ändern bei gleichen Records den Hash nicht.
- Ledger und Trades besitzen getrennte Idempotenzräume.
- Es entstehen keine steuerlichen Domainobjekte.

## Tests

Erkennung, CSV-Technik, Kraken-Zeilen, Fehleraggregation, Atomarität,
Idempotenz, Provenienz und Architekturgrenzen werden mit synthetischen Daten
getestet. Pytest erzwingt 100 Prozent Backend-Coverage.

## Dokumentation

Developer Guide, Architektur- und Importdokumentation, ADR 0009 und
`docs/sprint-2c-summary.md` beschreiben Vertrag und Grenzen.

## Definition of Done

- Implementierung, Tests und Dokumentation stimmen überein.
- Alle Repository-Prüfungen einschließlich Alembic und Docker bestehen.
- Keine Migration und keine Änderung am generischen Hashvertrag sind nötig.

## Umgesetzter Stand

Sprint 2C ist umgesetzt. Steuerliche Transformation und dateiübergreifende
Deduplizierung verbleiben in Sprint 2D.
