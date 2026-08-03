# ADR 0012: FIFO, Steuerjournal und Exporte

## Status

Angenommen für Sprint 3B; technische Freigabe steht aus.

## Entscheidung

FIFO und Journalerzeugung sind providerneutrale Core-Funktionen. Sie
verarbeiten ausschließlich bewertete Domainobjekte. API, SQLAlchemy und
Dateisystemadapter orchestrieren beziehungsweise persistieren, enthalten aber
keine Steuerformeln. Das Frontend zeigt nur Backendresultate.

Die Reihenfolge ist UTC-Zeitpunkt plus UUID. Teilwerte werden ohne frühe
Rundung proportional verteilt; die letzte Zuordnung erhält den exakten Rest.
FIFO-, Gebühren-, Klassifikations-, Journal- und Exportvertrag besitzen
zentrale Versionen. Zeitraum, Datensnapshot und Regel-Fingerprint sichern
Idempotenz. Änderungen erzeugen supersedierende Läufe und keine Mutation der
Detailnachweise.

Fehlende Evidenz, Bestand oder eindeutige Behandlung wird als Review
dokumentiert. Die Anwendung erzeugt eine Arbeitsdokumentation, keine
verbindliche steuerliche Einzelfallprüfung.

CSV und PDF entstehen reproduzierbar im Backend. Artefakte verwenden ein
konfiguriertes Wurzelverzeichnis, sichere technische Namen und persistierte
Hashes. Downloads erfolgen nur über bekannte IDs.

## Folgen

Migration 0006 ergänzt Läufe, Inventarlots, Zuordnungen, Berechnungen,
Journal, Reviews und Exportartefakte. SQLite und PostgreSQL bleiben
gleichwertige Zielsysteme. Neue steuerliche Auslegungen werden als neue
Regelversion eingeführt und können kontrolliert neu gerechnet werden.
