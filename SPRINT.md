# Sprint 2B – Generische Import Engine

## Ziel

Eine quellenneutrale, deterministische und auditierbare Import Engine übernimmt
validierte Rohdaten idempotent. Sie schafft die Grundlage für spätere Adapter
und fachliche Transformationen, ohne Kraken- oder Steuerlogik vorwegzunehmen.

## Ausgangslage

Domain-Entitäten sowie Repository- und Unit-of-Work-Abstraktionen bestehen
bereits. Der Sprint führt diese Grenzen für Importvorgänge zusammen und
bewahrt externe Eingaben als unveränderte, nachvollziehbare Evidenz.

## Scope

- unveränderlicher `ImportContext` mit Quelle, Version, Akteur,
  Korrelations-ID, UTC-Eingangszeit und zugehöriger `ImportSession`;
- deterministischer Import-Hash aus kanonischer Eingabe;
- Idempotenz anhand von Quelle und Hash;
- generische Eingabevalidierung;
- getrennte technische Importfehler und fachliche Transformationsfehler;
- orchestration durch einen quellenneutralen `ImportService`;
- explizites Zustandsmodell von `ImportSession`;
- unveränderliche Speicherung als `RawImportRecord`;
- Nutzung der bestehenden Repository- und Unit-of-Work-Abstraktionen.

## Nicht-Ziele

- Kraken-spezifischer CSV-Adapter;
- FIFO-Berechnung;
- Steuerjournal;
- Verkaufsempfehlungen;
- automatische Verkäufe;
- öffentliche Import-API oder Import-UI.

## Fachliche Anforderungen

1. Jeder Import ist über Kontext, Session und Korrelations-ID
   nachvollziehbar.
2. Inhaltlich gleiche Eingaben derselben Quelle erzeugen keine doppelten
   Rohdatensätze.
3. Rohdaten bleiben unverändert und sind von späteren fachlichen Ergebnissen
   getrennt.
4. Technische Importfehler werden getrennt von fachlichen
   Transformationsfehlern klassifiziert.
5. Erfolgreiche, übersprungene und fehlgeschlagene Vorgänge besitzen einen
   eindeutigen, prüfbaren Zustand.

## Technische Anforderungen

- Der Hash wird deterministisch aus einer eindeutig definierten kanonischen
  Repräsentation gebildet.
- Eingaben werden vor Hashing und Persistenz auf Format, Kodierung und
  Grundstruktur validiert.
- `ImportService` hängt nur von expliziten Ports, Uhr, ID-Erzeugung und
  Unit-of-Work-Factory ab.
- Erlaubte Übergänge der `ImportSession` sind zentral definiert; terminale
  Zustände lassen keine weiteren Übergänge zu.
- `RawImportRecord` enthält Quelle, Hash, Originalinhalt, Session-Referenz und
  einen timezone-aware UTC-Zeitpunkt.
- Prüfung und Datenbank-Constraint schützen gemeinsam vor Duplikaten.
- Ein erfolgreicher Import wird atomar persistiert. Fehler rollen den Versuch
  zurück; technische Fehlernachweise werden in einer getrennten Transaktion
  gesichert.
- Domain und Ports bleiben frei von SQLAlchemy.

## Akzeptanzkriterien

- Unterschiedliche Schlüsselreihenfolgen derselben kanonischen Eingabe liefern
  denselben Hash.
- Inhaltliche Änderungen liefern einen anderen Hash.
- Ein wiederholter Import derselben Quelle und desselben Inhalts wird
  übersprungen und erzeugt keinen weiteren `RawImportRecord`.
- Ungültige Eingaben werden vor der Rohdatenpersistenz abgewiesen.
- Jeder erlaubte und unerlaubte Session-Übergang verhält sich deterministisch.
- Erfolgs-, Duplikat- und Fehlerpfade aktualisieren Zähler und Endzustand
  korrekt.
- Repository- und Unit-of-Work-Grenzen werden eingehalten.

## Testanforderungen

- Unit-Tests für Kontextinvarianten, Validierung, Hashing und Zustandsmodell;
- Service-Tests für Erfolg, Idempotenz, technische Fehler und Rollback;
- Persistenztests für exakte Decimal- und UTC-Roundtrips sowie Constraints;
- Migrationstest von leerer Datenbank bis Alembic `head`;
- Architekturtests gegen SQLAlchemy-Abhängigkeiten in Domain und Application;
- 100 Prozent Coverage gemäß vorhandener Pytest-Konfiguration.

## Dokumentationsanforderungen

- Architektur, Importpipeline und Fehlergrenzen dokumentieren;
- Hash-Kanonisierung und Idempotenzschlüssel eindeutig festhalten;
- Zustandsübergänge und Transaktionsverhalten beschreiben;
- wesentliche Architekturentscheidungen als ADR erfassen;
- Sprint-Ergebnis und verbleibende Nicht-Ziele aktualisieren.

## Definition of Done

- Alle Anforderungen und Akzeptanzkriterien sind implementiert und getestet.
- Ruff, Black, MyPy strict und Pytest mit Coverage bestehen.
- Migrationen laufen vorwärts auf einer leeren Datenbank.
- Dokumentation und ADRs entsprechen dem implementierten Verhalten.
- Es gibt keine Kraken-spezifische, FIFO-, Steuerjournal-, Empfehlungs- oder
  Verkaufslogik.
- Der Review bestätigt Architekturgrenzen, Datenschutz und
  Reproduzierbarkeit.
