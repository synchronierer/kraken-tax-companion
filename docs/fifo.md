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

Der Anschaffungswert zuzüglich Erwerbsgebühr bildet die Kostenbasis. Bei einer
Teilmenge werden Kosten, Erlös und Veräußerungsgebühr proportional zugeordnet.
Der jeweils letzte Teil erhält die nicht vorab gerundete Restdifferenz. Dadurch
entsprechen die Summen exakt den Ausgangswerten.

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
