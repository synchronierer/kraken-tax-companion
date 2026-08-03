# Steuerjournal

Das Steuerjournal ist eine reproduzierbare Arbeitsdokumentation auf Basis der
fachlichen Primärereignisse, Bewertungsentscheidungen und FIFO-Zuordnungen.
Es enthält keine Aussage, die eine individuelle Steuerberatung ersetzt.

Journalarten sind Anschaffung, Earn-Zufluss, Veräußerung, Tausch, Gebühr,
realisierter Gewinn, realisierter Verlust, Review und Korrektur. Ein Eintrag
referenziert den Rechenlauf, das Quellobjekt, optional die
Bewertungsentscheidung, die Loszuordnung und einen supersedierten Vorgänger.

Jahreswerte werden aus den Detailzeilen reproduziert. Gewinne und Verluste
werden getrennt ausgewiesen und anschließend saldiert. Offene Bewertungen,
Reviewfälle, unvollständige Veräußerungen und Restbestände bleiben sichtbar.
Die Regelkennungen sind zentral versioniert und werden mit jedem Lauf und
Nachweis gespeichert.

Für Staking-Rewards konsumiert das Journal ab `eur-valuation-v2` ausdrücklich
den Bruttoertrag. Der InventoryLot verwendet dagegen nur Nettomenge und
Netto-Anschaffungswert. Die einbehaltene Plattformgebühr bleibt ein getrennt
ausgewiesener `werbungskosten_candidate` und erzeugt einen Prüffall, bis ihre
steuerliche Behandlung bestätigt ist. Eine historische v1-Entscheidung ohne
Bruttokomponente wird nicht als vermeintlicher Bruttoertrag übernommen,
sondern als Reviewzeile ausgewiesen.

Diese Änderung ist durch `private-assets-reward-fee-review-v2`,
`tax-journal-reward-gross-v2` und `tax-export-reward-components-v2`
versioniert. Frühere Rechenläufe behalten ihre gespeicherten v1-Regelstände.
