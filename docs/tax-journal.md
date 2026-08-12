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

Alle endlichen Summen und Differenzen des Journals, der Bestände und der
Jahresübersicht werden mit operandengesteuerter lokaler Decimal-Präzision
gebildet. Lange EUR-Werte bleiben dadurch auch beim Addieren von null und beim
Aggregieren vieler Rewards unverändert. Es gibt weder Floatkonvertierung noch
fachliche Rundung oder Toleranzvergleich.

Für Staking-Rewards konsumiert das Journal ab `eur-valuation-v2` ausdrücklich
den Bruttoertrag. Der InventoryLot verwendet dagegen nur Nettomenge und
Netto-Anschaffungswert. Die einbehaltene Plattformgebühr bleibt ein getrennt
ausgewiesener `werbungskosten_candidate` und erzeugt einen Prüffall, bis ihre
steuerliche Behandlung bestätigt ist. Eine historische v1-Entscheidung ohne
Bruttokomponente wird nicht als vermeintlicher Bruttoertrag übernommen,
sondern als Reviewzeile ausgewiesen.

Ein Lauf mit solchen Gebührenkandidaten endet fachlich erfolgreich als
`completed_with_review`: Inventar und Journal sind reproduzierbar erzeugt,
die steuerliche Anerkennung der Gebühr bleibt aber offen. Die Kennzahl
`provisional_net_staking_income` ist ausdrücklich nur Bruttoertrag abzüglich
der noch zu prüfenden Gebührenkandidaten und keine endgültige steuerliche
Feststellung. Reviews werden ausschließlich durch nachvollziehbare fachliche
Aktionen aufgelöst; direkte Datenbankänderungen sind kein zulässiger Weg.

Diese Änderung ist durch `private-assets-reward-fee-review-v2`,
`tax-journal-reward-gross-v2` und `tax-export-reward-components-v2`
versioniert. Frühere Rechenläufe behalten ihre gespeicherten v1-Regelstände.

## Revisionssichere Entscheidung über Gebührenkandidaten

Ein Kandidat ist keine automatisch anerkannte Werbungskostenposition. Der
Benutzer kann ihn ausdrücklich berücksichtigen oder nicht berücksichtigen und
muss eine Begründung angeben. Offenlassen erzeugt keinen Datensatz. Jede
Entscheidung ist als persistierter Datensatz immutable: Der SQLAlchemy-
`before_update`-/`reject_update`-Schutz verhindert Mutation. Änderungen
erzeugen ausschließlich eine neue Version mit Verweis auf die vorige.
Sammelentscheidungen bestehen aus einzelnen revisionsfähigen Entscheidungen
mit gemeinsamer `batch_id`.

`INCLUDE_AS_WERBUNGSKOSTEN` erzeugt im nächsten, bewusst gestarteten Taxlauf
zusätzlich zum Brutto-`EARN_INFLOW` einen aufgelösten `FEE`-Journaleintrag.
`EXCLUDE_FROM_WERBUNGSKOSTEN` erzeugt weder Gebühren- noch Reviewzeile. Ohne
Entscheidung bleibt die Reviewzeile bestehen. Nettobestand und
Anschaffungskosten werden in allen Fällen nicht verändert.

Die neuen Regeln heißen `private-assets-reward-fee-decision-v3`,
`tax-journal-reward-fee-decision-v3` und
`tax-export-review-decisions-v3`. `reviewed_net_staking_income` ist eine
Arbeitsberechnung aus Bruttoertrag minus manuell berücksichtigten Kandidaten,
keine automatische steuerrechtliche Feststellung.
