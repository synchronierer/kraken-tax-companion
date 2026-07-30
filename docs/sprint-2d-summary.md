# Sprint 2D – Zusammenfassung

Sprint 2D führt einen atomaren, versionierten Transformationslauf zwischen
unveränderlicher Raw-Evidenz und providerneutralen wirtschaftlichen Fakten
ein. Jeder geprüfte RawImportRecord erhält genau eine maschinenlesbare
Entscheidung.

Explizite Asset-Aliase und konservative Pair-Auflösung verhindern
stillschweigende Kraken-Heuristiken. Positive Earn-/Staking-Rewards werden mit
Brutto, Gebühr und Netto als Erwerbe gespeichert; interne Umbuchungen erzeugen
keine neuen Wirtschaftsgüter. Trade-Ausführungen bleiben je `txid` getrennt
und erzeugen Erwerbe, bei Kryptohingabe Veräußerungen sowie getrennte
Gebührenereignisse.

Stable Keys unterbinden Duplikate über überlappende Exporte. Abweichender
Payload wird als Konflikt statt als Überschreibung behandelt. Eine
Provenienztabelle verknüpft jedes Ergebnis mit allen beteiligten
RawImportRecords, ImportSessions und dem Transformationslauf.

Bewertungsanforderungen merken `DAILY_AVERAGE` und EUR als späteren Auftrag
vor, rufen aber keine Kurse ab. Direkte EUR-Gegenleistungen bleiben als native
Werte erhalten. Steuerjournal, FIFO und Gewinnberechnung sind ausdrücklich
nicht enthalten.

Die Modellierung orientiert sich als Tatsachen- und Hinweismodell am
BMF-Schreiben vom 6. März 2025, insbesondere an den Randnummern 48, 48a, 53
bis 63 und 87 bis 91. Sie ist keine individuelle Steuerberatung.
