# FIFO-Loszuordnung

Die Sprint-3B-Engine arbeitet providerneutral auf bewerteten Erwerbs- und
Veräußerungsereignissen. Sie ist eine steuerliche Arbeitsdokumentation und
ersetzt keine individuelle rechtliche Prüfung.

## Reportingzeitraum und Inventar

Der Reportingzeitraum verwendet UTC-Kalendertage und ist an beiden Grenzen
inklusiv. Kryptolose, die vor `period_start` erworben wurden, bleiben für
Veräußerungen im Zeitraum verfügbar; ihr Anschaffungsjournaleintrag gehört
jedoch nicht in das gewählte Steuerjahr. Erwerbe nach `period_end` und
Veräußerungen außerhalb des Zeitraums werden vom fachlichen FIFO-Lauf selbst
ausgeschlossen. EUR- und USD-Zuflüsse sind keine Krypto-Inventarlosen und
werden daher nicht als FIFO-Bestand oder fehlende Kryptobewertung behandelt.

## Reihenfolge und Präzision

Lose werden zuerst nach dem UTC-Anschaffungszeitpunkt und anschließend nach
ihrer technischen UUID sortiert. Die Engine entnimmt das älteste verfügbare
Los zuerst, unterstützt Teilentnahmen und verteilt eine Veräußerung über
mehrere Lose. Mengen und Geldwerte bleiben vollständig `Decimal`.

Endliche Dezimaloperationen (Addition, Subtraktion, Multiplikation und
Summierung) verwenden einen lokalen, aus Koeffizienten und Exponenten der
Operanden abgeleiteten Präzisionskontext. Damit bleibt beispielsweise die
Addition einer Erwerbsgebühr von null zum langen Netto-Anschaffungswert
verlustfrei. Der globale Decimal-Kontext wird weder gelesen noch verändert;
eine Cent- oder Anzeigequantisierung findet im Rechenkern nicht statt.

Der Anschaffungswert zuzüglich Erwerbsgebühr bildet die Kostenbasis. Bei einer
Teilmenge werden Kosten, Erlös und Veräußerungsgebühr proportional zugeordnet.
Der jeweils letzte Teil erhält die nicht vorab gerundete Restdifferenz. Dadurch
entsprechen die Summen exakt den Ausgangswerten.

Die proportionale Division kann im Gegensatz zu endlichen Operationen ein
nicht endendes Dezimalergebnis erzeugen. Ihr bestehender Vertrag
`proportional-last-remainder-v1` wird durch die Präzisionskorrektur nicht
umdefiniert: Die letzte Zuordnung erhält weiterhin den Rest. Vor dem ersten
realen Lauf mit Veräußerungen ist die bislang implizite Divisionspräzision als
eigene Regelentscheidung (Skala, Rundungsmodus und Restzuweisung) zu prüfen.
Die bestehende Division läuft bis dahin in einer lokalen Kopie des aktiven
Kontexts, sodass sie dessen bisherigen Zahlenvertrag wahrt, aber weder globale
Flags noch globale Präzision verändert.
Da Sprint-3B-Erwerbsläufe ohne Veräußerungen keine proportionale Division
ausführen, blockiert diese offene Regelentscheidung deren exakte Erfassung
nicht. Die Gebührenregelversion bleibt bis zu einer solchen fachlich
entschiedenen Änderung unverändert.

## Reviews und Versionierung

Eine Veräußerung ohne ausreichenden belegten Bestand wird nur bis zur
verfügbaren Menge zugeordnet und erhält `tax_insufficient_inventory`. Fehlende
EUR-Bewertungen und Kryptogebühren ohne nachgewiesenen Bestandsabgang werden
ebenfalls sichtbar geprüft. Es gibt keine negativen Restmengen und keine
Überallokation.

Der Snapshot umfasst Ereignis-, Bewertungs-, Mengen-, Wert-, Gebühren- und
Typdaten. Zeitraum, Snapshot und Regel-Fingerprint bilden den
Idempotenzschlüssel. Neue Evidenz oder Regelversionen erzeugen einen neuen,
auf den Vorgänger verweisenden Lauf; historische Nachweise werden nicht
überschrieben.

Manuelle Entscheidungen über Staking-Plattformgebühren verändern weder die
Nettomenge noch den Netto-Anschaffungswert eines InventoryLots. Sie gehören
nicht zu den proportionalen FIFO-Gebühren. Der Snapshot enthält die effektive
`TaxReviewDecision`; eine neue Version erzeugt einen neuen Rechenstand, ohne
FIFO-Regel `fifo-utc-stable-v1` oder Gebührenregel
`proportional-last-remainder-v1` zu ändern.
