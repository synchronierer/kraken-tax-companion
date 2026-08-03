# Weboberfläche

Die React-Anwendung ist eine reine Präsentationsschicht und verwendet
ausschließlich die REST-API. Sie enthält keine fachlichen Berechnungen und
keine Mockdaten.

Die dauerhafte Navigation umfasst Übersicht, Importe, Vorgänge, Bewertungen,
Prüffälle und System. Upload, optionale Direkttransformation, Bewertungslauf,
manuelle Kurse sowie reale Tabellen machen den vertikalen Ablauf bedienbar.
Lade-, Leer-, Fehler- und Erfolgszustände sind sichtbar; Tabellen können auf
kleineren Ansichten horizontal scrollen.

Die Grundstruktur bleibt für Steuerjournal, Abschluss und Exporte aus Sprint
3B bestehen.

## Netzwerkzugriff

Die produktive Basis ist Same-Origin: Eine Seite unter
`http://SERVER-IP:5173` lädt API-Daten über `http://SERVER-IP:5173/api/...`.
Frontend-Nginx reicht `/api/` unter Erhalt des Präfixes an `backend:8000`
weiter. `/backend-health` leitet auf den Backend-Healthcheck. Es wird keine
Server-IP eingebrannt; `VITE_API_BASE_URL` ist leer und nur für bewusste
Entwicklungsabweichungen vorgesehen.

Der Systemstatus wird über `GET /api/system/status` geladen. Listen besitzen
korrespondierende Detailendpunkte für Importe, Transformationen, Ereignisse,
Preise, Bewertungen und Prüffälle; Bewertungsdetails führen Raw-, Import-,
Transformations-, Requirement-, Evidenz- und Auditbezüge zusammen.

Die Tabellen öffnen echte Detailantworten für Importe, Transformationen,
Erwerbe, Veräußerungen, Trades, Gebühren, Preisnachweise, Bewertungen und
Prüffälle. Rohdaten erscheinen nur in der bewusst angeforderten
Importdetailantwort und sind dort standardmäßig nicht enthalten. Neben dem
Einzelformular kann die Bewertungsseite eine manuelle Kurs-CSV hochladen.

`npm --prefix frontend test` führt reproduzierbare Node-Tests aus. Dabei wird
der echte TypeScript-API-Client mit dem bereits von Vite verwendeten
`esbuild` geladen. Getestet werden Same-Origin-Pfade, HTTP- gegenüber
Netzwerkfehlern sowie frameworkarme Anzeige-, Validierungs-, Review-,
Pagination- und Secretfilterregeln. Dies sind Integrations- und
Vertragstests, keine erfundenen Browsertests.

## Sprint-3B-Seiten

Die Navigation enthält zusätzlich Steuerübersicht, FIFO-Zuordnungen, Bestände,
Steuerjournal und Exporte. Tabellen öffnen echte Backenddetails. Exporte werden
für einen abgeschlossenen Lauf erzeugt und über den Same-Origin-Pfad geladen.
Gewinn, Verlust, Gebühren, Bestand und Haltedauer werden im Browser weder neu
berechnet noch gerundet.

## Kraken API

Die Seite „Kraken API“ bietet Verbindungsstatus, Ledger-CSV-Auswahl,
reproduzierbaren CSV–API-Abgleich und den bestätigten Import. Der Browser erhält
keine Schlüssel. Der Importknopf wird erst nach einem importbereiten Vergleich
und einer ausdrücklichen Digest-Bestätigung aktiv. Der Digest wird beim Import
serverseitig durch einen neuen Live-Abruf geprüft; ein veralteter Vergleich
führt zu HTTP 409. Standardmäßig startet der Import keine Transformation und
niemals Bewertung oder FIFO.
