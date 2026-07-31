# Developer Guide

## Projektüberblick

Kraken Tax Companion ist eine selbst gehostete Anwendung zur transparenten,
auditierbaren Aufbereitung von Börsendaten für steuerorientierte Abläufe. Das
Monorepo enthält ein FastAPI-Backend, eine React-Oberfläche, Alembic-Migrationen
und Docker-Definitionen. Die Anwendung bereitet Nachweise auf und erteilt keine
Steuerberatung.

## Architektur und Schichten

Die Abhängigkeiten zeigen nach innen:

```text
API und UI -> Application -> Domain
Infrastructure ------------^
```

### Domain

`backend/app/core/` enthält frameworkfreie Entitäten, Identifikatoren,
Repository-Protokolle, Zeitregeln und die Unit-of-Work-Abstraktion. Invarianten
werden hier unabhängig von FastAPI und SQLAlchemy ausgedrückt.

### Application

Anwendungsdienste orchestrieren Use Cases über explizite Ports. Dazu gehören
der generische Import unter `backend/app/imports/` und weitere Services unter
`backend/app/services/`. Application-Code besitzt keine ORM-Abhängigkeit.

### Infrastructure

`backend/app/database/` enthält SQLAlchemy-Mappings, konkrete Repositorys,
Session-Erzeugung, Datenbanktypen und die Unit of Work. SQLAlchemy bleibt auf
diese Schicht begrenzt.

### API und UI

FastAPI-Router unter `backend/app/api/` und fachlich abgegrenzte Router sind
Transportadapter. `frontend/` ist eine React- und TypeScript-Präsentation.
Router, ORM-Modelle und UI enthalten keine Fachlogik.

## Datenbank und Alembic

Schemaänderungen erfolgen ausschließlich über Migrationen in
`backend/alembic/versions/`. Aus `backend/`:

```bash
alembic upgrade head
alembic revision --autogenerate -m "describe change"
```

Autogenerierte Migrationen müssen geprüft werden. SQLite ist die anfängliche
Datenbank; portable Typen und Grenzen halten den Weg zu PostgreSQL offen.

`UtcDateTime` verwendet auf PostgreSQL `TIMESTAMP WITH TIME ZONE` und auf
SQLite den kompatiblen zeitzonenlosen Speichertyp; die Python-Grenze akzeptiert
weiterhin ausschließlich aware Werte und normalisiert sie auf UTC. Strukturierte
Nachweise verwenden zentral `STRUCTURED_JSON`: PostgreSQL kompiliert ihn als
`JSONB`, SQLite als `JSON`. Der Alembic-Typvergleich behandelt nur
`UtcDateTime` und einen tatsächlich zeitzonenfähigen PostgreSQL-Zeitstempel als
gleichwertig. Ein `timezone=False`-Mismatch sowie alle anderen Typänderungen
bleiben sichtbar.

Der Sprint-3A-Abschluss hat Upgrade, `alembic check`, Downgrade von 0005 auf
0004, erneutes Upgrade und einen zweiten Check mit PostgreSQL 16 und SQLite
driftfrei bestätigt. Künftige Schemaänderungen müssen denselben Zyklus in einer
isolierten Testdatenbank wiederholen. Dafür gelten die Projektabhängigkeiten
aus `backend/pyproject.toml`, insbesondere `psycopg[binary]`; Anwendung und
Migrationen müssen aus dem zu prüfenden Quellbaum geladen werden.

## Decimal- und UTC-Strategie

Geld, Kurse und Mengen werden durchgehend als `Decimal` verarbeitet. Binäre
Floats dürfen finanzielle oder steuerliche Logik auch nicht vorübergehend
durchlaufen. Persistenzadapter bewahren den exakten Wert.

Alle Zeitpunkte sind timezone-aware und auf UTC normalisiert. Naive Zeitwerte
werden an Grenzen abgewiesen; Serialisierung und Datenbank-Roundtrips müssen
UTC und Zeitzoneninformation erhalten.

## Importarchitektur

Die generische Pipeline validiert geordnete, quellenneutrale JSON-Datensätze,
bildet eine kanonische Streaming-Repräsentation, berechnet SHA-256 und prüft
Idempotenz anhand von Quelle und Import-Hash. `ImportContext` trennt explizite
Identitätsdaten von beschreibender Provenienz. `ImportService` orchestriert
Zustand, typisierte Ergebnisse, Repositorys, Unit of Work, Lifecycle-Audit und
Fehlernachweis.

`RawImportRecord` bewahrt die Eingabe unverändert. Die Import-Session folgt
einem zentral definierten Zustandsmodell. Technische Importfehler sind von
späteren fachlichen Transformationsfehlern getrennt. Ein erfolgreicher Versuch
ist atomar; nach Rollback wird ein technischer Fehlernachweis in einer eigenen
Transaktion persistiert. Provider-Adapter bleiben außerhalb der Engine.

Der Kraken-CSV-Adapter liegt unter `backend/app/adapters/kraken/`. Er validiert
Ledger- und Trade-Exporte vollständig, bevor er geordnete `RawRecordInput`
übergibt. Parser und DTOs besitzen keine SQLAlchemy-, API- oder steuerliche
Abhängigkeit.

## Raw-to-Domain-Transformation

Sprint 2D ergänzt einen expliziten `TransformationRun`. Der Kraken-Rand
klassifiziert RawImportRecords, während `TradeExecution`, `AcquisitionLot`,
`DisposalEvent`, `FeeEvent`, Entscheidungen, Issues und
Bewertungsanforderungen providerneutral bleiben. Jeder geprüfte Rohdatensatz
erhält genau eine Entscheidung; `DomainProvenance` kann mehrere Rohdatensätze
mit einem Fachobjekt verbinden.

Assetcodes werden nur über das versionierte Aliasregister aufgelöst. Unbekannte
Codes und uneindeutige Paare bleiben als Review-Fall erhalten. Projektionen
verwenden einen Stable Key aus Provider, externer ID, Ereignistyp und
Vertragsversion. Eine Wiederholung erzeugt keine Duplikate, ein abweichender
Payload einen Konflikt. Eine Korrektur verwendet neue Evidenz und eine
explizit neue Transformationsversion.

Bewertungsanforderungen speichern nur Auftrag, Zielwährung, Zeitpunkt und
Methode. Sprint 2D fragt keine Kurse ab und enthält weder Steuerjournal noch
FIFO.

## Repository und Unit of Work

Domain-Protokolle definieren Lese- und Schreiboperationen. Application-Dienste
hängen von diesen Protokollen und einer Unit-of-Work-Factory ab, nicht von
SQLAlchemy-Sessions. Die konkrete Unit of Work bündelt zusammengehörige
Änderungen in einer Transaktion und entscheidet explizit über Commit oder
Rollback.

## EUR-Bewertung und UI

`app/core/valuation.py` enthält Methoden und Rechenregeln,
`app/infrastructure/coingecko.py` den ersten HTTP-Adapter. Migration 0005
persistiert Runs, Tagespreise und Entscheidungen. Die API unter `/api` wird von
der React-Oberfläche für Übersicht, Importe, Vorgänge, Bewertungen, Prüffälle
und Systemstatus verwendet. Details stehen in [valuation.md](valuation.md) und
[ui.md](ui.md).

## Teststrategie

Backend-Tests decken Domain-Invarianten, Architekturgrenzen, Importverhalten,
Persistenz und Migrationen ab. Pytest läuft mit strikter Konfiguration und
erzwingt 100 Prozent Coverage. Das Frontend besitzt reproduzierbare
Verhaltenstests sowie Lint, Typecheck und Produktionsbuild. Tests verwenden
ausschließlich synthetische Daten, niemals echte Nutzer- oder Steuerdaten.

## Lokaler Entwicklungsworkflow

Abhängigkeiten installieren und sämtliche primären Prüfungen ausführen:

```bash
make install
make check
```

Der Docker-Stack startet mit:

```bash
cp .env.example .env
docker compose up --build
```

Das Produktionsfrontend verwendet standardmäßig keine absolute API-Adresse.
Der Browser ruft `/api/...` über denselben Host und Port 5173 auf; Nginx
proxyt intern zu `backend:8000`. `VITE_API_BASE_URL` bleibt leer und ist nur
eine bewusst gesetzte Entwicklungsoption. Damit funktionieren entfernte
Browser und wechselnde Server-IP-Adressen ohne Frontend-Neubau.

Die dauerhaft unterstützten statischen, Backend-, Frontend- und
Dokumentationsprüfungen werden mit `make check` ausgeführt. Vor einer Freigabe
sind zusätzlich `docker compose config -q`, ein isolierter Compose-Smoke-Test
und die vollständigen SQLite-/PostgreSQL-Migrationszyklen auszuführen. Der
Sprint-3A-Abschluss hat diesen Vertrag vollständig erfüllt. Temporäre
Validierungs- und Diagnoseartefakte gehören nicht zum dauerhaften
Repositoryvertrag.

Die Sprint-3A-API umfasst Dashboard, Kraken-Upload, Transformationen,
Ereignisse, Requirements, Bewertungsruns, Entscheidungen, manuelle
Einzel-/CSV-Kurse, Preisnachweise, Prüffälle und Systemstatus:

```text
GET  /api/dashboard
POST /api/imports/kraken
GET  /api/imports[/{id}]
POST /api/transformations
GET  /api/transformations[/{id}]
GET  /api/events[/{type}/{id}]
GET  /api/valuation-requirements
POST /api/valuations
GET  /api/valuations[/{id}]
POST /api/prices/manual
POST /api/prices/manual/csv
GET  /api/prices[/{id}]
GET  /api/reviews[/{id}]
GET  /api/system/status
```

Listen validieren Offset und Limit; Bewertungslisten zusätzlich Asset,
Status, Methode und Datumsbereich. Detailantworten enthalten keine
SQLAlchemy-Objekte, sondern Pydantic-validierte Transportdaten.

## Codex-Workflow

`./dev` bestimmt sein Repository unabhängig vom aktuellen Verzeichnis. Es
ist der zentrale Einstieg und zeigt ein Menü für eine neue Codex-Sitzung, die
interaktive Auswahl einer vorhandenen Sitzung, eine Login-Shell oder das
Beenden.

```bash
./dev
```

`./resume` delegiert vollständig an `./dev` und bietet daher dasselbe Menü.
Codex läuft mit `--no-alt-screen` im normalen Terminal. So bleiben der normale
Terminal-Scrollback, das Scrollen mit dem Mausrad sowie das Markieren und
Kopieren von Text erhalten.

Neue Codex-Sitzungen werden so gestartet:

```bash
codex --no-alt-screen --sandbox workspace-write --ask-for-approval never
```

Vorhandene Sitzungen werden mit der interaktiven Sitzungsauswahl fortgesetzt:

```bash
codex --no-alt-screen --sandbox workspace-write --ask-for-approval never resume
```

`danger-full-access` wird nicht verwendet, um lokale Sandboxprobleme zu
umgehen.

## Fehlerbehebung

- Meldet `./dev` ein fehlendes Programm, prüfe Installation und `PATH` für
  `git` beziehungsweise `codex`.
- Bei Codex-Sandboxfehlern zuerst Funktion und Richtlinien von Bubblewrap,
  AppArmor und User-Namespaces prüfen. Nicht auf `danger-full-access`
  ausweichen.
- Startet Codex nicht mit den erwarteten Optionen, verwende `./dev` als
  zentralen Einstieg.
- Prüfe Backend-Befehle bei Importfehlern aus dem Repository-Root oder führe
  Alembic explizit aus `backend/` aus.
- Bei Frontend-Abweichungen stellt `npm --prefix frontend ci` exakt den
  Lockfile-Stand wieder her.

## Wichtige Befehle

```bash
make check
ruff check backend
black --check backend
mypy backend/app
pytest backend
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run build
npx --yes markdownlint-cli2 "**/*.md" "#frontend/node_modules"
docker compose build
bash -n dev
bash -n resume
```

Weitere Leitlinien:

- [Coding Rules](../CODING_RULES.md)
- [Contributing](../CONTRIBUTING.md)
- [Sprint 2C](../SPRINT.md)
- [Sprint 2D](sprint-2d-summary.md)
- [Architektur](architecture.md)
- [Import Engine](import.md)
- [Architecture Decision Records](adr/)
