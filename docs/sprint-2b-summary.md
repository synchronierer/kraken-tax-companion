# Sprint 2B – Zusammenfassung

Sprint 2B – Generic Import Engine ist vollständig implementiert, geprüft und
lokal committed.

## Umgesetzte Import-Infrastruktur

- Generische, providerunabhängige JSON-Import-Pipeline
- Unveränderlicher `ImportContext` mit:
  - Import-Session
  - Quelle und Version
  - UTC-Eingangszeit
  - Benutzer- oder Systemakteur
  - Correlation-ID
- Zentral definierter Import-Zustandsautomat:
  - `CREATED`
  - `RECEIVED`
  - `VALIDATING`
  - `HASHING`
  - `CHECKING_DUPLICATES`
  - `PERSISTING`
  - `COMPLETED`
  - `FAILED`
  - `CANCELLED`
- Explizit geprüfte zulässige Zustandsübergänge
- Generisches Validierungsframework ohne provider- oder fachbezogene Regeln
- Deterministische Canonical-JSON-Erzeugung
- Reproduzierbare SHA-256-Hashbildung
- Optionale Integritätsprüfung gegen einen erwarteten Hash
- Idempotenz über die Kombination aus Quelle und Content-Hash
- Zusätzlicher Unique Constraint als Schutz vor konkurrierenden Duplikaten
- Keine doppelten `RawImportRecord`- oder `AuditEvent`-Einträge bei Wiederholung
- Abgeschlossene Import-Session mit Skip-Zähler für Duplikate
- SQLAlchemy-Implementierungen der Repository-Interfaces
- SQLAlchemy-basierte Unit of Work
- Transaktionale Orchestrierung durch `ImportService`
- Rollback des vollständigen Importversuchs bei Fehlern
- Separate Recovery-Transaktion zur revisionssicheren Fehlerpersistenz
- Persistentes `ImportError`-Modell
- Getrennte Kategorien für Import- und spätere Fachfehler
- Neue Alembic-Migration `0002_generic_import_engine`
- Aktualisierte Architektur-, Import-, API- und Changelog-Dokumentation
- ADR 0005 bis ADR 0007

## Audit- und Idempotenzverhalten

Ein neu gespeicherter Rohdatensatz erzeugt genau ein
`raw_import.persisted`-Audit-Ereignis. Ein wiederholter Import identischer Daten
erzeugt weder einen weiteren Rohdatensatz noch ein weiteres Audit-Ereignis.

Die Import-Session dokumentiert unabhängig davon jeden Importversuch. Dadurch
bleiben erfolgreiche, übersprungene und fehlgeschlagene Abläufe
nachvollziehbar.

## Bewusst nicht enthalten

- Keine Kraken-API
- Keine Kraken-spezifischen Adapter oder Modelle
- Keine API-Keys, Signaturen oder Zugangsdaten
- Keine öffentliche Import-REST-API
- Keine Transformation in Fachobjekte
- Keine Ledger- oder Earn-Verarbeitung
- Keine Kursabfragen
- Keine Steuerberechnung
- Keine FIFO-Logik
- Keine Verkaufs- oder Empfehlungslogik
- Keine Home-Assistant-Integration

## Qualitätsergebnis

- Ruff bestanden
- Black bestanden
- MyPy strict über Anwendung und Tests bestanden
- 42 PyTests bestanden
- 100 Prozent Backend-Testabdeckung
- Alembic Upgrade, Driftcheck, Downgrade und erneutes Upgrade bestanden
- Migration der persistenten Compose-Datenbank auf Revision 0002 bestanden
- ESLint bestanden
- TypeScript-Prüfung bestanden
- Frontend-Produktionsbuild bestanden
- Markdown-Lint für 40 Dateien bestanden
- Beide Docker-Images erfolgreich gebaut
- Backend-Container healthy
- Backend-Healthcheck antwortet mit HTTP 200
- Frontend-Container healthy und antwortet mit HTTP 200
- Prüfung auf Zugangsdaten und nicht versionierbare Artefakte bestanden
- Arbeitsbaum nach dem Sprint-Commit sauber

## Verwendete Tool-Versionen

- Python 3.12.10
- Ruff 0.16.0
- Black 25.12.0
- MyPy 1.20.2
- PyTest 8.4.2
- Alembic 1.18.5
- Node.js 20.20.2
- npm 11.12.1
- ESLint 9.39.5
- TypeScript 5.8.3
- Vite 7.3.6
- markdownlint-cli2 0.22.1

## Sprint-Commit

- `004ed8e` –
  `feat(imports): implement generic idempotent import engine`

Der Commit ist lokal auf `main` vorhanden. Zum Zeitpunkt dieses Berichts liegt
`main` einen Commit vor `origin/main`.

## Grundlage für den nächsten Sprint

Die nächste Ausbaustufe kann Adapter für konkrete Quellen auf den generischen
Importvertrag setzen. Fachliche Transformationen sollten weiterhin getrennt
von Rohdatenannahme, Validierung, Idempotenz und Persistenz bleiben.

Insbesondere bleiben Kraken-Anbindung, Domain-Mapping, Steuerlogik, FIFO und
Empfehlungen eigenständige, ausdrücklich zu planende Arbeitspakete.
