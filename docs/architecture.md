# Architecture

## Context

Kraken Tax Companion preserves evidence and explains every derived result.
Sprint 2C adds a bounded Kraken CSV input adapter to source-neutral ingestion,
without tax, FIFO, or recommendation behavior.

## System Overview

```text
Raw Layer -> Domain Layer -> Tax Layer -> Presentation Layer
```

The Raw Layer preserves external evidence. The Domain Layer represents neutral
economic facts. Future layers may consume those facts but never mutate sources.
Sprint 2D adds the provider-neutral Domain Layer while keeping valuation and
the Tax Layer separate.

## Nur lesender Kraken-Live-Zugriff

Der private Kraken-REST-Adapter liegt ausschließlich in Infrastructure. Die
API orchestriert Connection- und Ledger-Diagnose; die Vorschau und der
CSV/API-Abgleich öffnen keine Unit of Work. Der Adapter signiert
form-urlencoded POST-Anfragen, erzeugt prozessweit monoton steigende Nonces und
liefert ein vom Kraken-Rohformat getrenntes Diagnosemodell. Erst der durch
Digest und ausdrückliche Bestätigung gesicherte Import übergibt kanonische
Datensätze atomar an die vorhandene Importengine. Seine Idempotenz beruht auf
der unveränderten Ledger-ID.

## Import Pipeline

```text
External Source
        |
        v
Import Adapter
        |
        v
Raw Import Layer
        |
        v
Validation Layer
        |
        v
Idempotency Layer
        |
        v
Persistence Layer
```

Adapters remain outside the generic engine. `ImportService` orchestrates an
immutable `ImportContext`, validation, canonical hashing, duplicate detection,
raw persistence, audit creation, and session completion. It does not transform
raw records into domain entities.

`app/adapters/kraken/` contains the Kraken schema contract, immutable parser
DTOs, and application orchestration. The generic import core and domain do not
import this package. The parser uses neither SQLAlchemy nor router or UI code.

## Transformation Pipeline

```text
RawImportRecord
      |
      v
Kraken interpretation boundary
      |
      v
TransformationDecision + provider-neutral facts
      |
      v
DomainProvenance + ValuationRequirement
```

`TransformationRun` owns a versioned atomic projection over one or more import
sessions. Every selected raw record receives exactly one decision. Stable
projection keys prevent duplicates across overlapping exports; conflicts and
unknown classifications are persisted for review instead of guessed.

Asset and pair knowledge is confined to the Kraken boundary and uses only an
explicit versioned alias register. Domain facts retain raw and canonical asset
codes. Trade, acquisition, disposal and fee facts remain distinct so later
valuation and FIFO can consume them without rewriting source evidence.

## Backend

### API Layer

FastAPI is the transport boundary. Dependency construction belongs to the
composition root; domain entities remain unaware of HTTP. Sprint 2C exposes no
import endpoint.

### Application Services

Services receive repositories, validators, clocks, ID generators, and
unit-of-work factories explicitly. `ImportService` is the source-neutral import
use case.

### Domain Modules

Framework-independent dataclasses define domain, raw, audit, session, and error
entities. Creation validates Decimal amounts, required text, counters, and
aware UTC timestamps.

### Infrastructure

SQLAlchemy maps domain classes imperatively. Repository protocols point inward.
Concrete SQLAlchemy repositories and the unit of work implement those ports.

## Import Session Lifecycle

The centralized state machine permits only declared transitions:

```text
CREATED -> RECEIVED -> VALIDATING -> HASHING
        -> CHECKING_DUPLICATES -> PERSISTING -> COMPLETED
```

`COMPLETED` is also permitted directly after duplicate checking. Active states
may transition to `FAILED` or `CANCELLED`. Terminal states reject further
transitions. Every transition updates the aware UTC timestamp; terminal states
also set the end time.

## Transactions and Failure Evidence

A successful or skipped import runs inside one SQLAlchemy unit of work. Any
failure rolls back the attempted session, raw record, and audit event together.
A separate recovery transaction then persists the failed session and
`ImportError`; otherwise the evidence of failure would be lost in that same
rollback.

Import failures and future domain failures are separated by `ErrorCategory`.
Sprint 2C persists only import failures and reserves the domain category and
exception base for later business validation.

## Persistence

### SQLite

UUIDs use SQLAlchemy's portable type. Decimal amounts use canonical strings to
avoid binary floating-point conversion. Reads restore UTC awareness.

Batch idempotency queries session source and SHA-256 inside the unit of work.
Raw records are unique by session and sequence, allowing equal payloads in one
artifact. SQLite's serialized writer model is supported. Concurrent PostgreSQL
import workers remain disabled until ADR 0008's atomic claim is implemented.

### PostgreSQL Migration Path

The Decimal adapter selects `NUMERIC(38,18)` for PostgreSQL. `UtcDateTime`
selects `TIMESTAMP WITH TIME ZONE`, while its SQLite representation remains
compatible with the local database and both paths enforce aware UTC values at
the Python boundary. A central structured-data type selects PostgreSQL `JSONB`
and SQLite `JSON`; mappings and migrations share this dialect contract.
Portable UUID, foreign-key, enum, and constraint definitions avoid
SQLite-specific application logic.

### Alembic

Revision `0001_domain_foundation` creates the foundational tables. Revision
`0002_generic_import_engine` introduces the session lifecycle fields,
the original idempotency constraint, and import errors without seed data.
Revision `0003_import_batch_model` adds persistent batch hashes, error
summaries, ordered record metadata, and replaces record-content uniqueness
with session-position uniqueness.
Revision `0004_domain_transformation` adds runs, decisions, issues, economic
facts, valuation requests and full raw-to-domain provenance.
Revision `0005_eur_valuation` adds immutable valuation evidence and aligns the
single JSON column introduced by revision 0003 with the PostgreSQL `JSONB`
contract. Alembic uses a narrow custom comparison only for the physical
representation of `UtcDateTime`: PostgreSQL `timezone=True` is equivalent,
whereas `timezone=False` and unrelated type changes continue to produce drift.
Revision `0006_fifo_tax_journal_exports` ergänzt unveränderliche
FIFO-Detailnachweise, Steuerjournal und sichere Exportartefakte. Rechen- und
Exportläufe sind kontrollierte Statushüllen; ihre Detailnachweise bleiben
append-only.

## Frontend

Die React-Anwendung ist ein Präsentations-Consumer der versionierten REST-API.
Sprint 3A ergänzt den vertikalen Import-, Transformations- und
Bewertungsworkflow. Fachliche Berechnungen verbleiben im Backend.

## Bewertungspipeline

`ValuationRequirement` wird durch einen atomaren `ValuationRun` aufgelöst.
Application-Code hängt am `HistoricalPriceProvider`; CoinGecko und HTTP liegen
in Infrastructure. Normalisierte Beobachtungen ergeben unveränderliche
Tagespreise und versionierte Entscheidungen. Manuelle Nachweise bleiben neben
automatischer Evidenz erhalten.

Die eigenständige Provider-Evidenz liegt zwischen HTTP-Adapter und Tagespreis.
Sie speichert nur begrenzte normalisierte Beobachtungen und keine geheimen
Requestdaten.

Reward-Komponenten und deren Run-Summen verwenden operandengesteuerte lokale
Decimal-Kontexte. Multiplikationen reservieren die vollständige mögliche
Koeffizientenbreite; Summen berücksichtigen Exponentenausrichtung und
Übertrag. Damit bleiben die Invarianten ohne Float, Toleranz oder fachliche
Rundung exakt, ohne den globalen Decimal-Kontext zu verändern.

Tagespreise und Bewertungsentscheidungen sind append-only. Duplikate werden
erkannt; Korrekturen und neue Methoden- oder Providervertragsversionen
erzeugen Nachfolger mit `supersedes_id`. API-Details bilden die Kette von
ImportSession über Rohdatensatz, Transformation und Domainobjekt bis zu
Requirement, Evidenz, Tagespreis, Entscheidung und Audit ab. Fehlende
optionale Glieder bleiben ausdrücklich `null` oder leer.

## FIFO- und Steuerpipeline

Die providerneutrale Core-Funktion konsumiert nur aufgelöste
Bewertungsentscheidungen. Sie sortiert Erwerbe stabil nach UTC-Zeitpunkt und
UUID, erzeugt Bestandslose und verteilt Veräußerungen deterministisch. API und
Persistence orchestrieren; SQLAlchemy und FastAPI bleiben außerhalb des Core.

Regelversionen und ein kanonischer Snapshot bilden mit dem Zeitraum den
Idempotenzvertrag. Abweichende Daten oder Regeln erzeugen einen neuen Lauf mit
Vorgängerreferenz. Exporte verwenden registrierte Artefakte, sichere
Basisnamen und ein konfiguriertes Wurzelverzeichnis.

## Cross-Cutting Concerns

### Configuration and Logging

Runtime configuration comes exclusively from environment values. One
application logging configuration remains the operational boundary.

### Security

Raw payloads and exception details are sensitive evidence. They are kept out of
logs and public APIs. No credentials, provider client, or API keys exist.

### Testing

Tests cover invariants, lifecycle transitions, canonical hashing, validation,
idempotency, rollback, audit creation, exact persistence, and migrations.

## Dependency Rules

- Presentation depends on application interfaces.
- Application services depend on domain ports.
- Persistence depends on domain entities, never the reverse.
- Import adapters depend on the generic import boundary.
- Raw evidence produces versioned facts; facts never rewrite evidence.
- The future Tax Layer may only consume Domain Layer facts.

## Deployment

Docker Compose runs the backend and frontend with persistent data, log, and
export volumes.

Auch Versionen folgen dieser Grenze: Die Berechnungsversion ist
providerneutral im Core definiert, während Provider-Vertrag und
Asset-Mappingversion Eigentum des konkreten Infrastructure-Adapters sind.
Import und optionale Direkttransformation verwenden dieselbe requestgebundene
Unit-of-Work-Factory, sodass API-Dependency-Overrides und Produktionszugriffe
garantiert denselben Datenbankkontext sehen.

Das CoinGecko-Mapping ist eine explizite Infrastructure-Allowlist. Version
`coingecko-asset-map-v2` ordnet die unterstützten Kraken-Staking-Assets ADA,
ATOM, BTC, DOT, EIGEN, ETH, GRT, KAVA und XTZ festen Provider-IDs zu. Besonders
`ATOM -> cosmos` und `EIGEN -> eigenlayer` werden nicht aus Symbolen
geraten. Der Providervertrag `market-chart-range-v1` bleibt davon getrennt;
die tatsächlich verwendete Provider-ID wird in ProviderEvidence festgehalten.
Nicht registrierte Assets erzeugen einen Review statt einer Laufzeitsuche.
Die Allowlist wird gegen `/coins/list` geprüft. Veränderliche Anzeigenamen
sind informativ; die explizite ID und das Symbol bilden den technischen
Identitätsvertrag.

## Kanonischer Kraken-Ledgervertrag

CSV- und Private-REST-Adapter erzeugen denselben providerinternen
`CanonicalKrakenLedgerRecord`. Die Infrastructure verantwortet Signatur,
Pagination und technische Fehler; der Adapter verantwortet Quellen- und
Assetnormalisierung. Die generische Importengine kennt Kraken nicht und nutzt
nur den kanonischen externen Schlüssel. Migration 0007 ergänzt dessen
quellenübergreifend eindeutige Persistenz. Vergleich und Vorschau sind
read-only; nur der bestätigte Digest-Import öffnet eine atomare Unit of Work.

Der versionierte Ledger-Normalisierer trennt Rohcode, Basisasset,
Produktmarker und Produktsuffix. Aliasnormalisierung ist explizit;
syntaktisch gültige unbekannte Basisassets werden ohne Bedeutungsannahme per
Identität normalisiert. Dadurch bleibt die Integration vorwärtskompatibel,
ohne `.S`, `.B`, `.F` oder `.M` als Wallet zu interpretieren. Nur leere,
ungültige oder nicht eindeutig zerlegbare Codes sind blockierende Unknowns.

Diese Normalisierung ist zugleich die einzige Kraken-Assetgrenze für CSV,
Private REST, Rohdatensatz und Domaintransformation. Der Adapter persistiert
die kanonische Identität in den technischen Metadaten; die Transformation
verwendet sie bevorzugt und fällt bei älteren Rohdatensätzen auf dieselbe
Funktion zurück. Eine zweite, kleinere Aliasliste im fachlichen Lauf gibt es
nicht. Produktvarianten bleiben technische Quellenmerkmale und werden nicht
als Walletsemantik interpretiert.

`kraken-domain-v1` bleibt als historischer Transformationsvertrag lesbar und
reproduzierbar. Der aktive Vertrag `kraken-domain-v2` verwendet die generische
Identitätsnormalisierung. Fachliche Stable Keys bleiben dabei gegenüber v1
stabil: eine identische v1-Projektion wird im v2-Lauf referenziert und als
`DOMAIN_EVENT_REUSED` entschieden, nicht erneut erzeugt. Nur bisher wegen der
alten Aliasgrenze fehlende Projektionen werden angelegt; frühere Decisions und
Reviews bleiben unverändert.

## Rewardbewertung und Steuergrenze

Migration 0008 erweitert die unveränderliche `ValuationDecision` additiv um
Brutto-, Gebühren- und Nettokomponenten. `eur-valuation-v1` bleibt als
Nettovertrag reproduzierbar; `eur-valuation-v2` ist der aktive Vertrag. Alle
drei Komponenten referenzieren denselben DailyPrice und dieselbe
ProviderEvidence. Historische Zeilen bleiben nullable und werden nicht durch
erfundene Werte aufgefüllt.

FIFO erhält ausschließlich Nettomenge und Netto-Anschaffungswert. Das
Steuerjournal liest den Bruttoertrag aus dem expliziten Feld und behandelt die
Plattformgebühr als überprüfbaren Kandidaten. Damit kann keine generische
`eur_value`-Spalte versehentlich zugleich Ertrag und Bestandswert bedeuten.

Tax-Core und Tax-API verwenden für endliche Decimal-Operationen dieselbe
operandengesteuerte Exaktarithmetik wie die Rewardbewertung. Lokale Kontexte
decken die vollständigen Koeffizienten und Exponenten der konkreten Operanden
ab; der globale Kontext bleibt unverändert. Das gilt für Inventarkosten,
Journalwerte, FIFO-Akkumulatoren sowie Gewinn-, Verlust-, Bestands- und
Reward-Summen. Die möglicherweise nicht terminierende proportionale Division
bleibt davon getrennt und weiterhin Eigentum der versionierten
Last-Remainder-Gebührenregel.

## Manuelle Steuer-Reviewentscheidungen

Sprint 3C trennt den unveränderlichen Gebührenkandidaten der Bewertung von der
ebenfalls unveränderlichen Nutzerentscheidung. `TaxReviewDecision` referenziert
genau eine `ValuationDecision` und den historischen `TaxReviewCase`. Eine
Änderung erzeugt eine lückenlos versionierte Nachfolgezeile mit
`supersedes_id`; die höchste validierte Version ist effektiv. Die gemeinsame
`batch_id` auf jeder Einzelentscheidung liefert Gruppierungs- und
Auditprovenienz, ohne ein redundantes Batch-Domainobjekt einzuführen.

Die API validiert sämtliche Bulkfälle vor dem ersten Insert und persistiert
Entscheidungen und AuditEvents in einer Transaktion. Sie startet niemals einen
Taxlauf. Erst ein separat ausgelöster Lauf konsumiert die effektiven
Entscheidungen. Entscheidung-ID, Version und Wert sind Bestandteil des
Tax-Snapshots. Historische Reviews, Bewertungen, Journale und Taxläufe werden
nicht verändert.

Die Python-Dataclass ist bewusst nicht `frozen`, weil SQLAlchemy die Instanz
instrumentieren muss. Persistenz-Immutability entsteht durch den zentralen
`before_update`-/`reject_update`-Schutz des Mappers. Eine fachliche Änderung
erfolgt ausschließlich als neue Zeile mit höherer Version und
`supersedes_id`; ein SQL-UPDATE des historischen Datensatzes wird abgewiesen.
