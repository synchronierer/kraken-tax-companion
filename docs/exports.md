# Steuerliche Exporte

Sprint 3B erzeugt semikolongetrennte UTF-8-CSV-Dateien für Steuerjournal,
FIFO-Zuordnungen, Bestände, Bewertungsnachweise, Reviewfälle und
Jahreszusammenfassung. Spaltenreihenfolge und Sortierung sind stabil;
Zeitpunkte werden als ISO-Werte und Decimalwerte ohne Floatkonvertierung
geschrieben.

Der PDF-Bericht wird ohne Browser- oder Clouddienst serverseitig erzeugt. Er
enthält Zeitraum, Erstellungszeit, Regelversionen, Zusammenfassung, Gewinne,
Verluste, Earn-Zuflüsse, Gebühren, Reviews, Bestände, FIFO- und
Methodenhinweise sowie den Hinweis auf die Arbeitsdokumentation.

Dateien liegen ausschließlich in `APP_EXPORT_DIRECTORY`. Zufällige technische
Dateinamen werden als Artefakte mit Medientyp, Größe und SHA-256 persistiert.
Der Download akzeptiert nur bekannte Artefakt-IDs. Freie Pfade und
Path-Traversal werden abgewiesen.

## API

`POST /api/exports` erzeugt ein Format für einen abgeschlossenen
Steuerberechnungslauf. `GET /api/exports` und `GET /api/exports/{id}` zeigen
Metadaten. `GET /api/exports/{id}/download` liefert ausschließlich das
registrierte Artefakt.
