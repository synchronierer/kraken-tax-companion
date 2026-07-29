# Sprint 2A – Zusammenfassung

Sprint 2A – Domain Foundation ist vollständig abgeschlossen, geprüft und auf
`origin/main` veröffentlicht.

## Umgesetzte Grundlagen

- Frameworkfreie Domain-Entitäten:
  - `EarnLot`
  - `Sale`
  - `AuditEvent`
  - `ImportSession`
  - `PriceSnapshot`
  - `Configuration`
  - `RawImportRecord`
- Trennung von Raw-, Domain-, Tax- und Presentation-Layer
- Zentrale `IdGenerator`-Abstraktion mit UUIDv4-Fallback
- Ausschließliche Verwendung von `Decimal` für Mengen und Preise
- Ausschließlich timezone-aware UTC-Zeitstempel
- Verlustfreie Decimal-Speicherung unter SQLite
- PostgreSQL-kompatible `NUMERIC(38,18)`-Strategie
- Imperative SQLAlchemy-Mappings ohne Framework-Abhängigkeit in der Domain
- Immutable-Guards für revisionsrelevante Datensätze
- Repository-Interfaces
- Unit-of-Work-Schnittstelle
- Service-Abhängigkeiten und Dependency-Injection-Grundlage
- Erste fachliche Alembic-Migration mit sieben Tabellen
- Automatische Migration beim Backend-Containerstart
- Unit-, Persistenz- und Migrationstests
- Aktualisierte Architektur-, API- und Tax-Dokumentation
- ADR 0002 bis ADR 0004
- Erweiterte `.gitignore`-Absicherung

## Bewusst nicht enthalten

- Keine Kraken-API oder Kraken-spezifischen Datenmodelle
- Keine API-Keys
- Keine Steuerberechnung
- Keine FIFO-Logik
- Keine Verkaufs- oder Empfehlungslogik
- Keine Kursabfrage
- Keine Seeder oder Beispieldaten

## Qualitätsergebnis

- Ruff bestanden
- Black bestanden
- MyPy strict bestanden
- 18 PyTests bestanden
- 100 Prozent Backend-Testabdeckung
- Alembic Upgrade, Downgrade und Driftcheck bestanden
- ESLint bestanden
- TypeScript-Prüfung bestanden
- Frontend-Produktionsbuild bestanden
- Markdown-Lint bestanden
- Beide Docker-Images erfolgreich gebaut
- Backend-Healthcheck bestanden
- Frontend antwortet mit HTTP 200
- Arbeitsbaum war nach Abschluss sauber und mit `origin/main` synchron

## Commits

- `45186fc` – `feat(domain): establish auditable domain foundation`
- `b3e019b` – `chore: harden local artifact exclusions`

## Grundlage für Sprint 2B

Für Sprint 2B sind insbesondere folgende Erweiterungspunkte vorbereitet:

- Konkrete Repository-Adapter
- SQLAlchemy Unit of Work
- Import-Use-Case und Transaktionsgrenzen
- Kanonische Hashbildung und Idempotenz
- Importfehler- und Korrekturmodell
- Dokumentierte Import-API-Verträge

Kraken-Anbindung, Steuerlogik, FIFO und Empfehlungen bleiben weiterhin außerhalb
des Umfangs, solange ein nachfolgender Sprint sie nicht ausdrücklich vorsieht.
