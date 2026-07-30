# ADR 0010: Raw-to-Domain Transformation

- Status: Accepted
- Date: 2026-07-30

## Context

Kraken-Rohdaten sind unveränderliche Evidenz, aber noch keine
providerneutralen wirtschaftlichen Fakten. Das bestehende `EarnLot` speichert
nur eine Menge, `Sale` nur Asset und Menge. Damit lassen sich Brutto, Gebühr,
Netto, Krypto-Tausch, normale Erwerbe, mehrfache Raw-Provenienz und offene
Bewertung nicht verlustfrei ausdrücken.

Das BMF-Schreiben vom 6. März 2025 behandelt passives Staking in den
Randnummern 48 und 48a, Anschaffung, Veräußerung und Verwendungsreihenfolge in
den Randnummern 53 bis 63 sowie Aufzeichnung, Nachvollziehbarkeit und
Kursmethoden in den Randnummern 87 bis 91. Sprint 2D speichert Tatsachen und
Klassifikationshinweise; er erteilt keine individuelle Steuerberatung.

## Decision

### Run und Entscheidung

Jede Ausführung ist ein `TransformationRun` mit explizitem Zustandsautomaten.
Jede ImportSession wird über eine Zuordnungstabelle referenziert. Für jeden
geprüften RawImportRecord entsteht genau eine `TransformationDecision`.
Fachliche Reviews sind erfolgreiche, vollständig dokumentierte Ergebnisse und
führen zu `COMPLETED_WITH_REVIEW`; technische Fehler rollen die Projektion
zurück.

### Versionierung und Idempotenz

Ein stabiler Schlüssel kombiniert Provider, externe Datensatzidentität,
fachlichen Ereignistyp und Transformationsvertragsversion. Gleicher Payload
liefert `DUPLICATE`; abweichender relevanter Payload liefert `CONFLICT`.
Eine neue Version projiziert nur durch explizite Angabe eines neuen Vertrags.
Korrekturen erfolgen durch neue Raw-Evidenz und eine neue Vertragsversion,
nicht durch Überschreiben bestehender Fakten.

### Assets und Paare

Ein versioniertes Register enthält ausschließlich explizite Aliaswerte.
Originalcode, kanonischer Code, Mappingversion und Status bleiben getrennt.
Unbekannte Codes werden vollständig bewahrt und zur Review gestellt. Es gibt
keine Entfernung vermeintlicher `X`-/`Z`-Präfixe. Paare werden nur durch einen
expliziten Trenner oder eine eindeutige Zerlegung aus bekannten Aliaswerten
aufgelöst; der Rohwert bleibt erhalten.

### Rewards und interne Bewegungen

Positive `earn/reward`-Einträge und konservativ eindeutige positive
Legacy-`staking`-Einträge erzeugen `AcquisitionLot`. Brutto, Gebührenmenge und
Netto bleiben getrennt. `TaxTreatmentHint` ist ein korrigierbarer Hinweis,
keine abschließende Rechtsentscheidung. Allocation, Deallocation, Migration
und Spot-/Staking-Umbuchungen werden als interne Bewegung ohne neues
Wirtschaftsgut entschieden.

### Trades, Erwerbe, Veräußerungen und Gebühren

Jede Trade-`txid` wird als eigene `TradeExecution` gespeichert.
`AcquisitionLot` und `DisposalEvent` bilden erhaltene beziehungsweise
hingegebene Assets ab; Krypto-zu-Krypto erzeugt beide. `FeeEvent` bewahrt
Tradegebühren getrennt. Fiat-Hingaben erzeugen keine Krypto-Veräußerung.
Wallet und Plattform werden für Sprint 2D als `kraken`, `default` und
`kraken-spot` gespeichert.

### Reconciliation und Provenienz

Trade-Ledger-Verweise beziehen sich ausschließlich auf Ledger-`txid`.
Vollständige, partielle und fehlende optionale Matches werden explizit
gespeichert. Ledger-only spend/receive wird nur mit gemeinsamer stabiler
Referenz gruppiert. `DomainProvenance` verknüpft jedes Domainobjekt mit allen
beteiligten RawImportRecords, ImportSessions und dem Run.

### Review statt Vermutung

Unbekannte Assets, Paare, Subtypen, ungültige Mengen, erhebliche
Kostenabweichungen, widersprüchliche Payloads und mehrdeutige Gruppen erzeugen
strukturierte Issues. Freie Fehlermeldungstexte steuern keine Transformation.

### Trennung späterer Schichten

`ValuationRequirement` merkt die spätere EUR-Bewertung mit Standardmethode
`DAILY_AVERAGE` vor. Direkte EUR-Werte bleiben native Ausführungsdaten. Es
werden keine Kurse erfunden. Bewertung, Steuerjournal, FIFO,
Verbrauchszuordnung und Gewinnberechnung bleiben spätere, getrennte Schritte.

## Consequences

- Das bestehende `EarnLot` und `Sale` behalten ihre Semantik; neue Fakten
  verwenden additive providerneutrale Entitäten.
- Migration 0004 ergänzt portable Tabellen, Fremdschlüssel, Checks und
  Unique Constraints für SQLite und PostgreSQL.
- Kraken-Interpretation bleibt im Adapter; die Domain kennt weder Kraken noch
  SQLAlchemy.
- Neuverarbeitung ist explizit versioniert und bestehende Projektionen bleiben
  unverändert auditierbar.
