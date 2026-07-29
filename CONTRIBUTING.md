# Contributing

## Voraussetzungen

- Git
- Python 3.12 oder neuer und `pip`
- Node.js mit `npm` für das Frontend und die Markdown-Prüfung
- Codex für den projektbezogenen Entwicklungsworkflow
- Docker Engine mit Docker Compose v2 für Container-Builds und den lokalen
  Stack

## Repository einrichten

```bash
git clone <repository-url>
cd kraken-tax-companion
make install
```

`make install` installiert `backend[dev]` editierbar und führt im Frontend
`npm ci` anhand des Lockfiles aus. Verwende keine echten Zugangsdaten in der
lokalen Konfiguration.

## Entwicklungsworkflow

Starte aus einem beliebigen Verzeichnis:

```bash
/pfad/zu/kraken-tax-companion/dev
```

Im Repository genügt `./dev`. Das Menü startet Codex neu, öffnet die
Codex-Sitzungsauswahl oder eine Login-Shell. `./resume` ist ein Alias für
denselben Workflow und delegiert vollständig an `./dev`. Codex läuft mit
`--no-alt-screen` im normalen Terminal. Dadurch bleiben der übliche
Terminal-Scrollback, das Scrollen mit dem Mausrad sowie das Markieren und
Kopieren von Text erhalten. Vorhandene Sitzungen werden über die interaktive
Sitzungsauswahl von `codex resume` fortgesetzt. Details stehen im
[Developer Guide](docs/DEVELOPER_GUIDE.md).

## Qualitätsprüfungen

Alle primären Prüfungen:

```bash
make check
```

Die tatsächlich konfigurierten Teilprüfungen sind:

```bash
ruff check backend
black --check backend
mypy backend/app
pytest backend
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run build
npx --yes markdownlint-cli2 "**/*.md" "#frontend/node_modules"
```

MyPy läuft durch `backend/pyproject.toml` im Strict-Modus. Pytest erzwingt dort
100 Prozent Coverage für `app`; ein separates Coverage-Kommando ist daher
nicht nötig. Das Frontend definiert derzeit kein Testskript.

Container werden ohne Änderung des Laufzeitverhaltens gebaut:

```bash
docker compose build
```

## Datenbankmigrationen

Alembic ist der einzige Weg für Schemaänderungen. Befehle werden aus dem
Backend-Verzeichnis ausgeführt:

```bash
cd backend
alembic upgrade head
alembic revision --autogenerate -m "describe change"
```

Prüfe generierte Migrationen manuell und ergänze Migrationstests. Ändere ein
veröffentlichtes Migrationsskript nicht rückwirkend.

## Code- und Datenregeln

- Lies [CODING_RULES.md](CODING_RULES.md) vor der Implementierung.
- Geld, Kurse und Mengen verwenden ausschließlich `Decimal`; binäre Floats
  sind in Finanz- und Steuerlogik verboten.
- Zeitangaben sind timezone-aware und auf UTC normalisiert.
- Tests, Fixtures und Beispiele enthalten keine echten Nutzer- oder
  Steuerdaten.
- Steuerlich relevante Änderungen benötigen nachvollziehbare Annahmen,
  deterministische Tests, Herkunftsnachweise und eine explizite Review-Notiz.

## Commits und Pull Requests

- Erstelle einen fokussierten Branch und halte Änderungen auf ein kohärentes
  Anliegen begrenzt.
- Verwende Conventional Commits.
- Aktualisiere Tests und Dokumentation gemeinsam mit dem Verhalten.
- Führe alle relevanten Prüfungen lokal aus.
- Beschreibe im Pull Request Motivation, Umfang, Risiken, steuerliche
  Auswirkungen, Migrationen und ausgeführte Prüfungen.
- Überschreibe keine Historie gemeinsam genutzter Branches und committe weder
  Secrets noch generierte lokale Daten.

Wesentliche Architekturentscheidungen benötigen einen ADR unter `docs/adr/`.
Neue ADRs erhalten die nächste freie Nummer und dokumentieren Kontext,
Entscheidung und Konsequenzen. Architektur oder öffentliche Schnittstellen
sollen vor der Implementierung abgestimmt werden.
