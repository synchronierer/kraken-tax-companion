# Import

## Goals

The import engine preserves external JSON as immutable evidence. It is generic,
transactional, auditable, and independent of any exchange or transport.

Raw import still ends at immutable persistence. Sprint 2D invokes a separate
explicit transformation service; import itself performs no domain
transformation, tax calculation, FIFO allocation, pricing, or recommendation.

## Supported Sources

The compatibility entry point accepts one UTF-8 JSON object as `str` or
`bytes`. The primary batch contract accepts an ordered sequence of generic JSON
records. The Kraken CSV adapter translates Ledger History and Trades History
files into this contract.

## Live-Ledger: Vorschau vor bestätigtem Import

Die serverseitige Kraken-Ledger-Vorschau lädt alle Offset-Seiten eines
Zeitraums, prüft Pflichtfelder und Duplikate und erzeugt einen Ledger-ID-Digest,
legt aber keine ImportSession oder RawImportRecords an. Nach dokumentiertem
Vergleich mit einem CSV-Export desselben Zeitraums kann der Benutzer den Digest
ausdrücklich bestätigen. Der Import ruft Kraken erneut ab und persistiert nur
bei unverändertem Digest. CSV und API verwenden dieselbe Ledger-ID als
gemeinsame Idempotenzidentität.

## Kraken CSV Adapter

Ledger requires `txid`, `time`, `type`, `asset`, `amount`, and `fee`. Trades
requires `txid`, `ordertxid`, `pair`, `time`, `type`, `ordertype`, `price`,
`cost`, `fee`, and `vol`. BOM, outer header whitespace, and case are normalized
only for detection. Original headers and values remain the payload.
Normalization collisions and mixed schemas are errors.

Unknown columns and future type values remain unchanged. `aclass` is not
interpreted. `ledgers` remains a raw string and may additionally have a parsed
tuple view. Kraken asset codes are not normalized.

Times are parsed as aware UTC. Decimal fields use `Decimal`; decimal commas,
grouping separators, and scientific notation are outside this CSV contract.
The parser limits a field to 1 MiB and a file to 128 columns.

An invalid file never reaches `ImportService`. Problems are collected with a
stable code, source line, and field where possible. CSV values remain inert
data; a future spreadsheet export must safely render formula prefixes.

## Import Context

Each run creates an `ImportSession` and immutable `ImportContext` containing:

- source and source version;
- aware UTC receipt time;
- user or system actor;
- correlation ID; and
- the matching import session;
- an original file or logical source name (falling back to `source`);
- optional descriptive user metadata; and
- optional, explicitly controlled identity data.

Context construction rejects values that differ from the session.
Source, version, logical source name, and identity data define context
equality. Receipt time, session, actor, correlation ID, and descriptive
metadata provide provenance only and do not affect equality or the content
hash.

## State Machine

The normal lifecycle is:

```text
CREATED
  -> RECEIVED
  -> VALIDATING
  -> HASHING
  -> CHECKING_DUPLICATES
  -> PERSISTING
  -> COMPLETED
```

A duplicate transitions from `CHECKING_DUPLICATES` directly to `COMPLETED`.
Active states may end as `FAILED` or `CANCELLED`. The allowed-transition map is
centralized and invalid transitions fail explicitly.

## Validation

The generic validation layer rejects empty input, invalid UTF-8 or JSON, and
non-object JSON roots. `ImportValidator` is a protocol for composable rules.
`RequiredFieldsValidator` supplies source-neutral required-field validation.

No domain or provider-specific rules are included. Unsupported JSON values,
including non-finite numbers, fail canonicalization.

## Canonical Hashing

Hash input is the UTF-8 encoding of canonical JSON with:

- object keys sorted recursively;
- no insignificant whitespace;
- Unicode emitted directly rather than ASCII escapes;
- JSON arrays kept in their original order; and
- non-finite or non-JSON values rejected.

SHA-256 is calculated over those bytes and stored as a lowercase 64-character
hexadecimal string. An optional expected hash is compared case-insensitively.
JSON object key order therefore does not affect identity, while any meaningful
value or array-order change does.

For batches the ordered canonical records are framed with a versioned domain
separator and byte lengths before SHA-256 is updated incrementally. Record
order is significant. Unicode and line endings inside JSON string values are
preserved; only JSON syntax is canonicalized. Import time, UUIDs, actor data,
file name, and user metadata never enter this hash.

## Idempotency

Identity is the pair `(source, content_hash)`. Before persistence, the engine
checks the raw repository for this pair. An identical repeat:

- creates no `RawImportRecord`;
- creates no additional raw-persistence `AuditEvent`;
- completes its own `ImportSession`; and
- records one received and one skipped item.

This compatibility behavior is retained for existing callers. Migration 0003
removes the record-level uniqueness constraint because equal records are valid
inside one ordered batch.

For the batch API the pair is `(source, import_hash)` and is queried through
the session repository in the same unit of work. Completed attempts return
`ImportOutcome.DUPLICATE`. Failed attempts are relevant registrations too and
are skipped unless the caller explicitly supplies `retry_failed=True`; a retry
gets a fresh session. No global retry state exists. Concurrent PostgreSQL
workers require the atomic claim strategy documented in ADR 0008 before they
are enabled.

Kraken Ledger and Trades use the separate sources `kraken-ledgers` and
`kraken-trades`. External IDs are `kraken:ledger:<txid>` and
`kraken:trade:<txid>`. Duplicate `txid` values in one file are invalid; equal
`ordertxid` values are valid for multiple trade executions. Different batches
may retain the same external ID. Cross-file deduplication is handled by the
separate Sprint-2D transformation contract.

## Fachliche Transformation

Ein `TransformationRun` wählt eine oder mehrere abgeschlossene ImportSessions.
Für jeden RawImportRecord wird genau eine Entscheidung gespeichert:
Fachereignis, interne Bewegung, Review, Unsupported, Duplikat oder Konflikt.
Keine Entscheidung wird aus einem freien Fehlermeldungstext abgeleitet.

Die Kraken-Transformation erkennt positive `earn/reward`- und konservativ
eindeutige Legacy-`staking`-Rewards. Interne Allocation- und
Spot-/Staking-Bewegungen erzeugen keine Erwerbe. Trades bleiben je `txid`
getrennt und erzeugen providerneutrale Trades, Erwerbe, gegebenenfalls
Veräußerungen und Gebühren. Ledger-only spend/receive wird ausschließlich über
eine gemeinsame stabile Referenz gruppiert.

Der Stable Key enthält Provider, externe ID, Ereignistyp und
Transformationsversion. Gleiche Evidenz wird als Duplikat entschieden,
abweichende Evidenz als Konflikt. Eine neue Version ist die einzige
kontrollierte Neu-Projektion. `DomainProvenance` bewahrt alle Raw- und
Session-Verknüpfungen.

Unbekannte Assets und Paare werden nicht geraten. Bewertungsvormerkungen
enthalten EUR, Zeitpunkt und `DAILY_AVERAGE`, aber keinen erfundenen Kurs.
Direkte EUR-Kosten und -Erlöse bleiben native Kraken-Werte.

## Immutable Storage

New input is stored as ordered `RawImportRecord` rows containing source,
record hash, original parsed JSON payload, import-session reference, zero-based
sequence, optional external ID, separate technical metadata, and UTC creation
time. Repeated equal payloads in the same artifact remain distinguishable by
position. ORM update hooks reject later mutation.

## Transactions

Session progress, raw evidence, and its audit event use one SQLAlchemy unit of
work. Errors roll the entire attempt back. A recovery unit of work persists the
failed session and `ImportError` so operational failure evidence survives.

## Audit and Provenance

Lifecycle events cover `import.created`, `import.started`, `import.completed`,
`import.failed`, and `import.duplicate_detected`. The compatibility path also
retains `raw_import.persisted`. Events contain identities and summaries, never
raw payloads.

## Error Reporting

`ImportError` records UTC time, session, category, stable error code,
description, original exception summary, and affected record when available.

Machine-readable issues contain code, message, category, and optional record
position and field. Categories distinguish technical input, structural
validation, transformation, and persistence. SQLAlchemy exceptions are
translated into persistence failures at the application boundary. Expected
duplicates are results, not unexpected system exceptions.

## Security

Raw payloads and exception summaries may contain sensitive information. They
remain persistence data and are not emitted through a public endpoint. The
engine contains no credentials, API keys, signatures, or network client.

## Testing

Tests verify deterministic hashing, key-order independence, content changes,
validation, lifecycle transitions, duplicate suppression, audit creation,
rollback, failure evidence, repositories, unit-of-work behavior, and Alembic
migrations.

## REST-Upload in Sprint 3A

`POST /api/imports/kraken` verwendet unverändert den Kraken-CSV-Adapter und die
generische Engine. `transform=true` startet danach den getrennt auditierten
Transformationsdienst. Dateityp und Größe werden bereits an der HTTP-Grenze
begrenzt; Parserfehler behalten Zeile, Feld und stabilen Code.

Die manuelle Kurs-CSV verwendet
`asset,date,price_eur,source,reason`, UTF-8 beziehungsweise UTF-8-BOM und
einen Punkt als Dezimaltrennzeichen. Sie wird vollständig vor dem Commit
validiert. Fehler nennen Zeile und Feld; eine ungültige Zeile verwirft den
gesamten Import ohne Teilpersistenz.

Die Importantwort enthält dabei optional `transformation` mit Run-ID, Status,
geprüften Datensätzen, Prüffällen und erzeugten Bewertungsanforderungen. Bei
einem identischen erneuten Import wird eine bereits erfolgreiche
Transformation derselben ImportSession referenziert. Existiert noch keine
erfolgreiche Transformation, wird sie auch für den als Duplikat erkannten
Import kontrolliert ausgeführt. Ohne `transform=true` entstehen weder
Domainobjekte noch Bewertungsanforderungen.

Die Importengine bewahrt bei einem Duplikat sowohl die neue, auditierbare
ImportSession des Versuchs als auch den Verweis auf die ursprüngliche Session.
Für die Direkttransformation gilt als Idempotenzschlüssel die ursprüngliche
ImportSession zusammen mit der Transformationsvertragsversion. Ein bereits
erfolgreicher Lauf dieses Schlüssels wird unverändert wiederverwendet; seine
Run-ID und Zusammenfassung werden rekonstruiert. Fehlgeschlagene Läufe gelten
nicht als wiederverwendbarer Erfolg, und eine neue Vertragsversion darf einen
neuen Lauf erzeugen.

Sprint 3B verändert diesen Importvertrag nicht. FIFO und Journal arbeiten nur
auf transformierten und bewerteten Domainobjekten. Duplikatimporte erzeugen
keine zusätzlichen Lose; der Steuer-Snapshot referenziert die stabilen Domain-
und Bewertungsidentitäten.

## Kraken Live API und quellenübergreifende Identität

Der Live-Ledger-Import verwendet dieselbe Importengine wie CSV. Das kanonische
Merkmal `kraken:spot_ledger:<ledger-id>` ist unabhängig vom Transport und in
Migration 0007 eindeutig. Identischer Inhalt wird wiederverwendet;
widersprüchlicher Inhalt wird atomar als `canonical_record_conflict`
abgebrochen. Primärdaten werden nicht überschrieben. Der interne Zeitraum ist
UTC und halboffen (`[start, end)`); Kraken-Grenzen dienen nur zum Abruf einer
Obermenge, die anschließend lokal gefiltert wird. Details stehen in
[Kraken Live API](kraken-live-api.md).

Die kanonische Assetnormalisierung rät keine Coin-Identität: bekannte Kraken-
Aliase werden explizit abgebildet, während ein syntaktisch gültiger neuer Code
per Identität erhalten bleibt. Kontrollierte Suffixe `.S`, `.B`, `.F` und `.M`
sowie ein unmittelbar davor stehender numerischer Produktmarker werden separat
gespeichert. Der vollständige Rohcode und das CSV-Walletfeld bleiben
Quelleninformation; aus einem Suffix wird keine Walletklassifikation
abgeleitet. Nur ungültige oder mehrdeutige Codes erscheinen in
`unknown_asset_mappings` und sperren die Importfreigabe.

CSV/API-Abgleich und Domaintransformation beziehen diese Identität aus
`normalize_kraken_asset`. Neue RawImportRecords tragen Rohcode, Basisasset,
Produktmarker, Produktsuffix und Mappingversion als technische kanonische
Metadaten. Für ältere Rohdatensätze berechnet die Transformation denselben
Vertrag erneut; sie verwendet keine separate Whitelist.

Die aktive Direkttransformation nutzt `kraken-domain-v2`. Ein erneuter Lauf
mit v2 prüft alle Datensätze, übernimmt aber bereits unter v1 erzeugte,
inhaltlich identische Domainobjekte über ihren stabilen fachlichen Schlüssel.
Seine Decisions weisen Wiederverwendung und Neuanlage getrennt aus. Eine
widersprüchliche Projektion wird nicht überschrieben, sondern bleibt ein
strukturierter Reviewkonflikt. Historische v1-Läufe und deren
`asset_alias_unknown`-Entscheidungen werden nicht umgeschrieben.
