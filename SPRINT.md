# Sprint 2D – Transformation von Kraken-Rohdaten in fachliche Ereignisse

## Ziel

Revisionssicher importierte Kraken-Rohdaten werden atomar, idempotent und
nachvollziehbar in providerneutrale wirtschaftliche Fakten transformiert.

## Ausgangslage

Sprint 2C bewahrt Ledger- und Trade-CSV-Zeilen unverändert mit externer
Kraken-ID, ImportSession, Hash und technischen Metadaten auf.

## Scope

- explizite Transformationsläufe und eine Entscheidung je RawImportRecord;
- Earn-/Staking-Rewards und interne Bewegungen;
- TradeExecution, AcquisitionLot, DisposalEvent und FeeEvent;
- versionierte Asset-Aliase und konservative Pair-Auflösung;
- Ledger-/Trade-Abgleich und eindeutig referenzierte Ledger-only-Vorgänge;
- vollständige Raw-/Session-/Run-Provenienz und Bewertungsvormerkungen;
- strukturierte Review-, Konflikt-, Duplikat- und Fehlerfälle.

## Nicht-Ziele

Kursabruf, tatsächliche EUR-Bewertung, Steuerjournal, FIFO, Gewinn-/Verlust-,
Freigrenzen- oder Steuerberechnung, Empfehlungen, Verkäufe, API-Synchronisation
sowie Web- oder API-Endpunkte bleiben ausgeschlossen.

## Transformationsregeln

Jeder ausgewählte Rohdatensatz erhält genau eine Entscheidung. Die Identität
einer Projektion besteht aus Provider, externer Datensatzidentität,
Ereignistyp und Transformationsversion. Abweichender Payload bei gleicher
Identität wird als Konflikt gespeichert.

## Reward-Klassifikation

Positive `earn/reward`-Datensätze und konservativ eindeutige positive
Legacy-`staking`-Datensätze erzeugen Erwerbe. Brutto, Gebühr und Netto bleiben
getrennt. `TaxTreatmentHint` ist nur ein überprüfbarer Hinweis, keine
individuelle Rechtsentscheidung.

## Interne Bewegungen

Allocation, Autoallocation, Deallocation, Migration sowie Spot-/Staking-
Umbuchungen werden als interne Bewegung entschieden und erzeugen weder Erwerb
noch Veräußerung.

## Trades

Jede Kraken-`txid` bleibt eine eigene TradeExecution. Buy und Sell erzeugen
den erhaltenen Erwerb; die Hingabe eines Kryptowerts erzeugt zusätzlich eine
Veräußerung. Mehrere Ausführungen derselben `ordertxid` bleiben getrennt.

## Gebühren

Tradegebühren werden als FeeEvent gespeichert. Rewardgebühren bleiben als
Brutto-/Gebühr-/Netto-Bestandteil des Erwerbs nachvollziehbar. Es findet keine
Gewinnberechnung statt.

## Provenienz

DomainProvenance verknüpft jedes erzeugte Objekt mit allen beteiligten
RawImportRecords, ImportSessions und dem TransformationRun.

## Review-Fälle

Unbekannte Assets, uneindeutige Paare, unbekannte Earn-/Staking-Fälle,
ungültige Vorzeichen, Kostenabweichungen und ungesicherte Ledger-Gruppen werden
nicht geraten, sondern strukturiert zur Review vorgelegt.

## Akzeptanzkriterien

- jeder geprüfte Rohdatensatz besitzt genau eine Entscheidung;
- Wiederholung derselben Version erzeugt keine doppelten Projektionen;
- Konflikte überschreiben keine vorhandenen Fakten;
- alle Mengen verwenden Decimal und alle Zeiten aware UTC;
- der Lauf ist atomar und fachliche Reviews führen nicht zum Rollback;
- Domain und Application bleiben frei von Kraken und SQLAlchemy.

## Tests

Synthetische Tests decken Assets, Paare, Rewards, interne Bewegungen, Trades,
Ledger-only-Gruppen, Gebühren, Reconciliation, Idempotenz, Provenienz,
Atomarität, Migration und Architekturgrenzen ab. Backend-Coverage bleibt bei
100 Prozent.

## Dokumentation

Developer Guide, Architektur, Importdokumentation, ADR 0010 und
`docs/sprint-2d-summary.md` beschreiben Vertrag, BMF-Bezug und Grenzen.

## Definition of Done

- Implementierung, Migration, Tests und Dokumentation stimmen überein.
- Alle Repository-Prüfungen bestehen.
- Es gibt keine Kursabfrage, FIFO- oder Steuerjournal-Logik.

## Umgesetzter Stand

Sprint 2D ist umgesetzt. Die Backend-, Migrations-, Frontend-, Dokumentations-,
Shell-, Compose- und Docker-Prüfungen sind bestanden.
