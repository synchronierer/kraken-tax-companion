# Steuerliche Exporte

## PDF-Steuerbericht v2

Der serverseitig mit ReportLab erzeugte Bericht `tax-report-pdf-v2` ist ein
menschenlesbarer Begleitbericht zu den maschinenlesbaren CSV-Nachweisen. Er
enthält TaxRun-Metadaten, centgerundete Hauptkennzahlen, Asset- und
Bestandsaggregate, dokumentierte manuelle Gebührenentscheidungen,
Regelversionen und die drei zentralen exakten Decimalwerte. Die Aggregation
erfolgt vor dem Renderer mit exakter Decimalarithmetik; der Renderer übernimmt
nur Layout und Anzeigeformatierung.

`ExportRun.format_version` ist unabhängig von
`TaxCalculationRun.export_format_version`. Der bestehende TaxRun-Vertrag
`tax-export-review-decisions-v3` bleibt unverändert. PDF v1 und PDF v2 können
für denselben TaxRun revisionssicher nebeneinander bestehen. Die CSV-Formate
bleiben vorerst jeweils auf ihrer v1-Formatversion.

Der Prüffall-CSV-Export enthält ab Format
`tax-export-review-decisions-v3` neben dem historischen `TaxReviewCase` die
Bewertungs-ID, den Gebührenwert, offen/entschieden, die effektive Entscheidung,
Begründung, Akteur, Zeitpunkt, Version und Batch-ID. Jahres-CSV und PDF trennen
Kandidaten, manuell berücksichtigt, manuell nicht berücksichtigt und offen.
Der geprüfte Netto-Arbeitswert ist eine dokumentierte Nutzerentscheidung und
keine steuerrechtliche Automatik.

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
