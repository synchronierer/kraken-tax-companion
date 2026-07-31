# Sprint 3A – EUR-Bewertungsengine und erste funktionsfähige Web-UI

## Ziel und sichtbares Nutzerergebnis

Kraken-CSV-Evidenz wird importiert, in fachliche Vorgänge transformiert und
mit nachvollziehbaren EUR-Kursen bewertet. Die deutsche Weboberfläche zeigt
den gesamten Ablauf ohne Mockdaten.

## Ausgangslage und Scope

Sprint 2D liefert unveränderliche Rohdaten, Fachereignisse, Provenienz und
Bewertungsanforderungen. Sprint 3A ergänzt Kursquellen, die providerneutrale
EUR-Bewertung, manuelle Tageskurse, REST-API, Übersicht, Importe, Vorgänge,
Bewertungen, Prüffälle und Systemstatus.

## Bewertungsmethoden und Kursprovider

- `NATIVE_EUR` übernimmt eindeutige EUR-Gegenleistungen ohne Kursabruf.
- `DAILY_AVERAGE_HOURLY` mittelt gültige Stundenwerte eines abgeschlossenen
  UTC-Tags; mindestens 20 Werte sind für automatische Auflösung erforderlich.
- `MANUAL_DAILY_PRICE` ist ein versionierter, begründeter Nachweis.
- CoinGecko ist der erste Adapter hinter einem austauschbaren Provider-Port.

## Manuelle Kurse, API und UI

Einzel- und CSV-Erfassung stehen neben Import-, Transformations-, Bewertungs-,
Listen-, Review-, Dashboard- und Systemendpunkten. Navigation und Grundlayout
sind dauerhaft für Sprint 3B angelegt.

## Nicht-Ziele und Abgrenzung zu Sprint 3B

Sprint 3A enthält weder FIFO noch Haltefrist, Gewinnermittlung, Steuerjournal,
Jahresabschluss, CSV-Steuerexport oder PDF-Bericht. Sprint 3B ergänzt das
reviewbare Steuerjournal, konfigurierbare Verbrauchsfolge, Haltefrist und
Gewinnermittlung, Exporte sowie Jahresfreigabe.

## Akzeptanzkriterien, Tests und Dokumentation

Decimal und UTC bleiben durchgängig erhalten, Kursnachweise sind unveränderlich
und versioniert, Secrets verlassen das Backend nicht, Review ersetzt
Vermutungen. Domain-, Provider-, API-, Migrations- und UI-Verhalten werden
synthetisch getestet. Architektur, Bewertung, UI, Import und ADR 0011
dokumentieren den Vertrag.

## Definition of Done

Der Sprint ist erst umgesetzt, wenn Backend-, Frontend-, Alembic-, Compose-,
Docker-, Shell-, Markdown- und Smoke-Prüfungen erfolgreich abgeschlossen sind.
Der Prüfstatus steht in `docs/sprint-3a-summary.md`.

## Abschlussstatus

**Status: umgesetzt und technisch freigegeben am 1. August 2026.**

Die vollständige Hostvalidierung endete mit
`SPRINT_3A_HOST_VALIDATION_OK`. Sämtliche Backend-, Frontend-, SQLite-,
PostgreSQL-, Compose-, Docker-, Shell-, Markdown- und Smoke-Prüfungen waren
erfolgreich. Damit ist die Definition of Done erfüllt. Der getrennte
npm-Audit-Folgepunkt ist ein Wartungsthema und blockiert den fachlichen und
technischen Abschluss von Sprint 3A nicht.
