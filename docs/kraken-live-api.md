# Kraken-Live-Ledger-Vorschau

## Inkrementeller manueller Sync

`GET /api/kraken-sync` zeigt letzten Erfolg, aktiven Lauf und das nächste
halboffene UTC-Abruffenster. `POST /api/kraken-sync` liest ausschließlich
`/0/private/Ledgers`, importiert neue Ledger-IDs idempotent und transformiert
sie atomar. `/api/kraken-sync-runs` liefert die Audit-Historie.

Das Lookback-Fenster überlappt absichtlich. Bekannte `ledger_id`-Werte werden
geprüft und niemals überschrieben. Nur `COMPLETED` schreibt den impliziten
Checkpoint fort. Kraken-Sync ist nicht Bewertung, Steuerberechnung,
Reviewentscheidung oder Export und startet keinen dieser Abläufe.

Wegen der offsetbasierten Provider-Pagination wird jedes fixierte Fenster
zweimal vollständig gelesen. Nur identische ID-Digests und kanonische
Fingerprints werden übernommen. Ein abweichender Kontrollabruf schlägt sicher
fehl und wird mit dem Lookback erneut versucht.

Der erste Live-API-Schritt ist ein Dry-Run. Er liest das Spot-Ledger vollständig
seitenweise, erzeugt eine Diagnose und persistiert weder `ImportSession` noch
Rohdatensatz, Transformation, Bewertung oder Steuerberechnung. Erst ein
erfolgreicher CSV–API-Abgleich, ein unveränderter Digest und eine ausdrückliche
Bestätigung schalten den ausschließlich lesenden Live-Import frei.

## Berechtigung und Secrets

Der Kraken-Schlüssel benötigt nur `Data – Query ledger entries`
(`query-ledger`). Handels-, Einzahlungs- und Auszahlungsrechte sind weder nötig
noch erwünscht. Schlüssel und Base64-kodiertes Secret liegen ausschließlich in
der Serverumgebung. Sie erscheinen nicht in OpenAPI, Antworten, Datenbank oder
Diagnosedateien.

Grundlage sind die offiziellen Kraken-Verträge für
[Ledger-Abfragen](https://docs.kraken.com/api-reference/account-data/get-ledgers-info)
und
[Spot-REST-Authentifizierung](https://docs.kraken.com/exchange/guides/rest/authentication).

Die Variablen `APP_KRAKEN_API_KEY`, `APP_KRAKEN_API_SECRET`,
`APP_KRAKEN_API_BASE_URL`, `APP_KRAKEN_API_TIMEOUT` und
`APP_KRAKEN_API_MAX_RETRIES` konfigurieren den Adapter. Ohne Schlüssel ist er
deaktiviert. Produktionsstandard ist `https://api.kraken.com`; HTTP ist nur für
einen lokalen Testserver auf `localhost` beziehungsweise `127.0.0.1` zulässig.

## Diagnosevertrag

`GET /api/kraken/connection` prüft Erreichbarkeit, Authentifizierung und
Ledger-Leserecht, ohne Schlüsselmerkmale auszugeben. Eine Verbindungskontrolle
ist eine echte private Ledger-Abfrage, jedoch ohne Persistenz.

`POST /api/kraken/ledger-preview` akzeptiert optionale UTC-Zeitgrenzen, Asset,
Ledger-Typ und höchstens 100 reduzierte Diagnosezeilen. Standardmäßig enthält
die Antwort keine Beträge oder Kontostände. Der SHA-256-Digest wird nur aus den
lexikografisch sortierten, unveränderten Ledger-IDs mit Zeilenumbruch als
Trennzeichen gebildet. Dadurch ist er reproduzierbar, enthält aber keine
Kontowerte.

`ready_for_import` wird nur bei vollständiger Pagination, plausibler Gesamtzahl
und ohne fehlerhafte oder widersprüchlich doppelte Pflichtdatensätze gesetzt.
Unbekannte Typen bleiben als Diagnose erhalten. Vor einem späteren Live-Import
müssen Anzahl, Zeitraum und Digest mit einem Kraken-CSV-Export desselben
Zeitraums verglichen werden.

## Zeitraum und kanonischer Datensatz

Intern gilt stets der halboffene UTC-Zeitraum `[start, end)`. Kraken behandelt
`start` dagegen exklusiv und `end` inklusiv. Der Adapter fordert deshalb eine
sichere Obermenge an und filtert anschließend jeden Eintrag lokal mit
`start <= occurred_at < end`. Zeitpunkte und Mikrosekunden bleiben erhalten;
Diagnosezahlen und Digest beziehen sich ausschließlich auf diese lokale Menge.

CSV und Live-API werden auf `CanonicalKrakenLedgerRecord` abgebildet. Die
unveränderte Ledger-ID, Rohasset, normalisiertes Basisasset, Produktausprägung,
Decimalwerte und Quellenprovenienz bleiben getrennt. Das CSV-Walletfeld wird
nicht auf API-Datensätze übertragen oder gedeutet. Varianten wie `.S`, `.B`,
`.F`, `.M` und numerische Produktausprägungen werden erhalten, ohne daraus eine
unbelegte Walletklassifikation abzuleiten.

Die versionierte Assetnormalisierung unterscheidet drei erfolgreiche Wege:
explizite Kraken-Aliase wie `XXBT -> BTC`, Identitätsnormalisierung für jeden
syntaktisch eindeutigen Basiscode wie `ADA -> ADA` oder `EIGEN -> EIGEN` und
generisch erkannte Produktcodes. Bei `KAVA21.S` bleiben Basisasset `KAVA`,
Produktmarker `21`, Produktsuffix `S` und Rohcode getrennt erhalten. Ein neues,
aber aus Großbuchstaben und Ziffern bestehendes Asset blockiert den Import
nicht. Als unbekannt gelten nur leere, syntaktisch ungültige oder nicht
eindeutig zerlegbare Codes. Produktsuffixe sind keine Walletbezeichnungen.

Der Vertrag wird zentral durch `normalize_kraken_asset` implementiert und gilt
unverändert für CSV, Live-API, technische Raw-Provenienz und die aktive
Domaintransformation. `kraken-domain-v2` bevorzugt die beim Import gespeicherte
kanonische Identität; bei älteren RawImportRecords verwendet es dieselbe
Funktion als Fallback. `kraken-domain-v1` und seine früheren
Reviewentscheidungen bleiben auditierbare Historie. Bereits vorhandene
identische v1-Domainobjekte werden von v2 wiederverwendet, nicht dupliziert.

Die CSV enthält Sekunden-, die API Mikrosekundenpräzision. Beim Vergleich wird
nur der API-Zeitpunkt kontrolliert auf volle Sekunden abgebildet. Eine
Abweichung um mindestens eine Sekunde ist ein Konflikt. Die nachgewiesene
Quellenabbildung `staking` zu `earn/reward` gilt nur bei identischer Ledger-ID,
Referenz, Asset, Betrag, Gebühr und Sekunde; unbekannte Abbildungen verhindern
die Importfreigabe.

## Vergleich und bestätigter Import

`POST /api/kraken/ledger-compare` nimmt die aktuelle Kraken-Ledger-CSV als
Multipart-Datei sowie Start, Ende und optionale Filter entgegen. Der Endpunkt
persistiert nichts. Er vergleicht primär über Ledger-ID, weist fehlende IDs,
Duplikate, Feldkonflikte und Präzisionsunterschiede getrennt aus und setzt
`ready_for_import` nur bei identischen ID-Digests und fachlich konsistenten
Datensätzen.

`POST /api/kraken/ledger-import` nimmt keine CSV entgegen. Er verlangt
`explicit_confirmation=true` und den zuvor bestätigten Digest, ruft Kraken
erneut ab und vergleicht den neuen Digest vor jeder Speicherung. Bei Änderung
antwortet die API mit `409 kraken_ledger_changed`; es wird nichts persistiert.
Ein erfolgreicher Import nutzt die bestehende atomare Importengine. Die
quellenübergreifende Identität lautet
`kraken:spot_ledger:<unveränderte-ledger-id>`. Dadurch erzeugen CSV nach API und
API nach CSV keinen zweiten Primärdatensatz. Abweichender Inhalt unter derselben
ID wird nicht überschrieben, sondern als Importkonflikt abgebrochen.

Transformation ist mit `transform=true` ausdrücklich zuschaltbar und ansonsten
aus. Bewertung und FIFO starten niemals automatisch. Auditmetadaten enthalten
nur Zeitraum, Filter, Digest, Version und Zähler, keine Header, Schlüssel,
Secrets oder Nonces.

## Lokaler Aufruf

```bash
BACKEND_URL=http://127.0.0.1:8000 \
START=2025-01-01T00:00:00Z END=2026-01-01T00:00:00Z \
scripts/kraken-ledger-preview.sh
```

Das Skript ruft nur das lokale Backend auf und liest selbst keine Secrets.
Eine Datei wird nur bei explizitem, bereits vorhandenem `OUTPUT_DIR` mit
restriktiven Dateirechten geschrieben.

Für den reproduzierbaren Vergleich:

```bash
BACKEND_URL=http://127.0.0.1:8000 \
CSV_FILE=/sicherer/pfad/ledger.csv \
START=2026-07-01T00:00:00Z END=2026-08-01T00:00:00Z \
scripts/kraken-ledger-compare.sh
```

Die UI unter `/kraken-api` zeigt Verbindung, Vergleich und Digest. Der
Importknopf bleibt bei fehlender Bestätigung, veraltetem Vergleich,
unvollständiger Pagination oder Konflikten deaktiviert. Schlüssel werden weder
im Browser eingegeben noch angezeigt.

Nach einer optionalen `kraken-domain-v2`-Transformation bewertet
`eur-valuation-v2` Staking-Rewards getrennt als Bruttozufluss, einbehaltene
Plattformgebühr und Netto-Anschaffung. Der kanonische Kraken-Datensatz bleibt
unverändert; es wird kein zusätzliches FeeEvent oder Ledger-Ereignis erfunden.
Bewertung und FIFO werden durch den Live-Import weiterhin nicht automatisch
gestartet.

## Sprint 4A.1: Ledger-Pacing und Rate-Limit-Retries

Private Ledger-Requests desselben Clients starten standardmäßig mit mindestens
neun Sekunden Abstand. Der erste Request wartet nicht. Eine monotone Uhr misst
den Abstand; Pagination und Retries durchlaufen dieselbe Drosselung.
Uhr und Sleeper sind für Tests injizierbar. Andere Endpunkte erhalten kein
Ledger-Pacing.

Ein inkrementeller Sync erzeugt genau einen Leseclient und verwendet ihn für
beide vollständigen Konsistenzabrufe. Damit bleibt der Abstand auch zwischen
Preview und Confirmation erhalten. Provideridentität, Lookback, atomarer Import
und die ausschließliche Verwendung von COMPLETED als Checkpoint bleiben erhalten.
FAILED bleibt Audit-Historie und verhindert ohne PROCESSING keinen späteren
manuellen Sync.

Konfiguration in Settings, Compose und `.env.example`:

| Variable | Default | Zulässiger Bereich |
| --- | --- | --- |
| `APP_KRAKEN_LEDGER_MIN_INTERVAL_SECONDS` | 9 | größer 0 bis 300 Sekunden |
| `APP_KRAKEN_RATE_LIMIT_RETRY_BASE_SECONDS` | 30 | 30 bis 3600 Sekunden |

HTTP 429 und als `kraken_rate_limited` klassifizierte Kraken-JSON-Fehler warten
vor Wiederholung mindestens Basis × 2 hoch Retry-Index: standardmäßig 30 und
60 Sekunden. `APP_KRAKEN_API_MAX_RETRIES` begrenzt weiterhin die Wiederholungen.
HTTP `Retry-After` wird als Sekundenwert oder HTTP-Datum berücksichtigt; es kann
den sicheren Backoff verlängern, aber nicht verkürzen. Ungültige Werte werden
ignoriert. Nach Ausschöpfung bleibt der Fehlercode `kraken_rate_limited` erhalten.

Timeouts, Netzwerkfehler und HTTP 5xx behalten den kurzen temporären Backoff
(1/2 Sekunden, bei weiteren Versuchen maximal 4 Sekunden). Zusätzlich gilt für
Ledger-Requests weiterhin der Mindestabstand zwischen Requeststarts.

Die gemeldete reale 429-Antwort motiviert diese Absicherung: ungepacete
Account-History-Pagination und zwei Konsistenzdurchläufe können Bursts erzeugen.
Die genaue Belegung des Kraken-Limitzählers beim Fehler ist nicht bekannt.
Das Pacing gilt pro Client, nicht pro API-Key über mehrere Prozesse hinweg.
Parallele Zugriffe anderer Clients können daher weiterhin 429 auslösen.
Kleinere konfigurierte Intervalle reduzieren den Schutz für Standard-Konten.
Lange Historien benötigen mit zwei Durchläufen entsprechend mehr Zeit;
HTTP-/Proxy-Timeouts und die konfigurierte Stale-Schwelle müssen dazu passen.
Dieses Hardening führt keinen realen Sync und keine Migration aus.

## Sprint 4A.2: Ungeklärte finanzielle Ledgerbewegungen

Bekannte finanzielle Ledgerarten ohne sicheren Domain-Mappingpfad erzeugen
`REVIEW_REQUIRED` und ein `TransformationIssue` pro geprüftem Record.
Der Review-Zähler steigt entsprechend; der Run endet mit
`COMPLETED_WITH_REVIEW`. Das betrifft den Fallback für `transfer`,
`withdrawal`, `deposit`, `credit`, `adjustment`, `dividend`, `margin`,
`rollover`, `sale`, `settled`, `nft_rebate` und Ledger-`trade`.
Der Reason Code lautet jeweils `ledger_<type>_requires_review`, beispielsweise
`ledger_transfer_requires_review`.

Decision, Issue und Ergebnisproblem erklären übereinstimmend:
„Provider record is financially relevant but cannot be mapped conservatively
without additional context.“

Die Review-Klassifikation erzeugt keine Domainobjekte und keinen internen
Movement-Zähler. Ein Transfer belegt allein weder Kauf, Verkauf noch
Eigenübertragung; auch ein Fiat-Withdrawal mit Gebühr erzeugt weder
Krypto-Disposal noch FeeEvent. Zeitliche Nähe oder ein Delisting begründen keine
automatische Verknüpfung zweier Transfers.

Bestehende sichere Reward-, interne Earn-/Staking- und Exchange-Mappingpfade
bleiben erhalten. Strukturell fremde Quellen bleiben `UNSUPPORTED` mit
`source_unsupported`; unbekannte Ledgerarten außerhalb des unterstützten
Typvertrags behalten `ledger_type_unsupported`.

Bei einer später ausdrücklich gestarteten erneuten Transformation werden
identische Reward-Projektionen wiederverwendet. Die ungeklärten Bewegungen
erhalten neue Review-Evidenz im jeweiligen Run; historische Entscheidungen
werden nicht überschrieben. Diese Änderung startet weder eine erneute
Transformation noch Sync, Bewertung, Steuerberechnung oder Export.
