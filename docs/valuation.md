# EUR-Bewertung

Die Bewertungsengine trennt Tatsachen, Kursnachweise und Entscheidungen vom
späteren Steuerjournal. Sie erteilt keine individuelle Steuerberatung.

Native EUR-Gegenleistungen werden unverändert übernommen. Andere Ereignisse
verwenden das arithmetische Mittel aller gültigen stündlichen CoinGecko-Punkte
des abgeschlossenen UTC-Tags. Unter 20 Punkten entsteht ein Prüffall. Mengen,
Kurse und Ergebnisse bleiben `Decimal`; Centdarstellungen verwenden erst an
der Anzeigegrenze `ROUND_HALF_UP`.

Auch native EUR-Tatsachen durchlaufen die vollständige Bewertungskette. Ein
Trade gegen EUR erzeugt ein `ValuationRequirement` mit `DIRECT_EUR`; erst der
`ValuationRun` erzeugt daraus die unveränderliche Entscheidung
`NATIVE_EUR`. Für den Kauf von 0,5 BTC zu insgesamt 20.000 EUR bleiben damit
Stückpreis 40.000 EUR/BTC und Gesamtwert 20.000 EUR exakt nachvollziehbar.
Direkte EUR-Gebühren werden nach demselben Vertrag mit ihrem EUR-Nennwert
bewertet. Native Entscheidungen benötigen weder Tagespreis noch
Provider-Evidenz und lösen niemals einen Providerabruf aus.

Der Provider-Port kennt nur normalisierte Beobachtungen. Das explizite
CoinGecko-Register `coingecko-asset-map-v2` enthält:

- `ADA -> cardano`
- `ATOM -> cosmos`
- `BTC -> bitcoin`
- `DOT -> polkadot`
- `EIGEN -> eigenlayer`
- `ETH -> ethereum`
- `GRT -> the-graph`
- `KAVA -> kava`
- `XTZ -> tezos`

Das Register ist eine versionierte Allowlist. Es gibt weder eine automatische
Symbolsuche noch eine unscharfe Auswahl oder einen Marktkapitalisierungs-
Fallback. Unbekannte Assets führen weiterhin zum Reviewgrund
`valuation_asset_mapping_missing`. Provider-Evidenz wird normalisiert, gehasht
und ohne geheime Header gespeichert.

Die IDs werden gegen den öffentlichen CoinGecko-Endpunkt `/coins/list`
geprüft. Anzeigenamen und Projekt- oder Markennamen können sich ändern;
maßgeblich bleiben die explizite Provider-ID und das zugehörige Symbol. Für
den aktuellen Stand sind das `cosmos / atom / Cosmos Hub` und
`eigenlayer / eigen / EigenCloud (prev. EigenLayer)`.

`provider_evidence` bewahrt Provider- und Vertragskennung, explizite
Provider-Asset-ID, Zielwährung, UTC-Anfragefenster, HTTP-Status, Abrufzeit,
kanonischen Hash und normalisierte Beobachtungen als Zeit- und Decimalstrings.
Tageskurs und Bewertungsentscheidung referenzieren diesen unveränderlichen
Nachweis eindeutig. Weder Header noch API-Schlüssel werden persistiert.

Manuelle Tageskurse erfordern Asset, UTC-Datum, positiven EUR-Kurs, Quelle,
Begründung und Akteur. Korrekturen erzeugen eine neue Version und löschen
automatische Evidenz nicht. Diese Nachweisstrategie berücksichtigt sachlich
die Dokumentations- und Konsistenzanforderungen des BMF-Schreibens vom
6. März 2025.

Identische manuelle Evidenz wird als Duplikat protokolliert. Eine Korrektur
erzeugt eine neue Tagespreisversion mit `supersedes_id`; der historische Satz
wird nicht geändert. Bewertungsentscheidungen werden bei einer neuen
Methodenversion ebenso fortgeschrieben. Listen und Details leiten den
effektiven Status `superseded` aus der unveränderlichen Nachfolgeverknüpfung
ab. Manuelle Tagespreise haben für dasselbe Asset und denselben UTC-Tag
Vorrang, ohne vorhandene automatische Evidenz zu löschen.

CoinGecko-Abrufe werden in höchstens 90 Tage große Fenster geteilt. Der
persistente Tagespreis-Cache verhindert unnötige Wiederholungen. Bewusst
erneut geladene, abweichende Evidenz wird als neue Version und Konflikt
auditiert; identische Evidenz erzeugt keinen zweiten Nachweis.
Die Domain definiert ausschließlich die providerneutrale Berechnungs- und
Methodenversion. Providername, Asset-Mappingversion und die konkrete
Provider-Vertragsversion liegen im Infrastructure-Adapter und werden erst mit
der unveränderlichen ProviderEvidence persistiert. Damit kennt der Core weder
CoinGecko noch dessen IDs oder HTTP-Vertrag.

Die Mappingversion ist vom CoinGecko-Antwortvertrag getrennt. Die Erweiterung
auf `coingecko-asset-map-v2` ändert daher nicht rückwirkend den Adaptervertrag
`market-chart-range-v1` und hat keine Auswirkung auf bereits gespeicherte
Bewertungsentscheidungen. Neue ProviderEvidence speichert stets die konkret
verwendete `provider_asset_id`.

Sprint 3B konsumiert ausschließlich effektive, aufgelöste
`ValuationDecision`-Nachweise. Native EUR-, manuelle und automatische Werte
werden gleichartig referenziert; Provider-Evidenz wird im Steuerjournal nicht
dupliziert. Eine korrigierte Bewertung verändert den fachlichen Snapshot und
darf einen supersedierenden Steuerberechnungslauf auslösen.

## Staking-Reward-Vertrag v2

`eur-valuation-v1` bleibt der historische Vertrag: `quantity` und
`eur_value` bilden dort ausschließlich Nettomenge und Netto-Anschaffungswert
ab. Diese Semantik wird nicht rückwirkend geändert. Der aktive Vertrag
`eur-valuation-v2` trennt für Staking-Rewards mit demselben Tagespreis:

- `gross_income_eur = gross_quantity * unit_price_eur`,
- `fee_value_eur = fee_quantity * unit_price_eur`,
- `net_acquisition_value_eur = net_quantity * unit_price_eur`.

Dabei bleibt `quantity` gleich `net_quantity` und `eur_value` gleich
`net_acquisition_value_eur`. Steuer- und Exportlogik darf `eur_value` deshalb
nicht ungeprüft als Rewardertrag verwenden. Intern wird nicht gerundet;
`ROUND_HALF_UP` gilt weiterhin ausschließlich für finale Centdarstellungen.

Die Mengeninvariante lautet `gross_quantity = net_quantity + fee_quantity`.
Widersprüche, eine negative oder zu große Gebühr sowie ein abweichendes
Gebührenasset führen vor dem Preisabruf zu einem Review. Altbestände ohne
Bruttomenge verwenden dokumentiert Brutto gleich Netto und Gebühr null. Ein
DailyPrice und eine ProviderEvidence werden je Asset und UTC-Tag nur einmal
verwendet.

Eine einbehaltene Rewardgebühr wird als
`werbungskosten_candidate` mit `review_required` dokumentiert. Das ist keine
endgültige Aussage über ihre steuerliche Abziehbarkeit. Es entsteht weder ein
erfundenes FeeEvent noch eine fiktive Veräußerung. Migration 0008 ergänzt die
nullable Komponentenfelder; bestehende v1-Entscheidungen werden nicht
aufgefüllt oder verändert.

Ein erneuter Lauf derselben Methodenversion erzeugt keine zweite wirksame
Entscheidung. `refresh_prices=true` kann neue Preise nur für noch offene
Anforderungen laden; eine bereits entschiedene Anforderung benötigt für eine
kontrollierte Neubewertung eine neue Methodenversion. Diese erzeugt eine neue
Decision mit `supersedes_id`, ohne die alte Zeile zu überschreiben.

## Exakte Decimal-Arithmetik

Reward-v2-Produkte und Run-Summen verlassen sich nicht auf die globale
Decimal-Standardpräzision von 28 Stellen. Getrennte Multiplikationen eines
langpräzisen Tageskurses konnten andernfalls in der letzten Stelle gegen die
strikte Invariante `Brutto = Netto + Gebühr` verstoßen. Der Core verwendet
deshalb ausschließlich lokale `decimal.localcontext()`-Blöcke. Die benötigte
Multiplikationspräzision folgt aus der Summe der Koeffizientenziffern; für
Summen werden die Operanden auf den kleinsten Exponenten ausgerichtet und um
die maximal mögliche Übertragsbreite ergänzt. Subtraktionen verwenden eine
kontextfreie Vorzeichenkopie und anschließend dieselbe exakte Summenfunktion;
damit kann auch das Negieren eines langpräzisen Operanden keine Stelle
verlieren.

Es gibt weder Floats noch Toleranzvergleich, fachliche Quantisierung oder
Cent-Rundung. Mengen-, Komponenten- und aggregierte Run-Invarianten bleiben
strikt. `ROUND_HALF_UP_DISPLAY_ONLY` gilt unverändert nur an der
Darstellungsgrenze. `eur-valuation-v1` behält seinen historischen
Berechnungsvertrag.

SQLite persistiert `ExactDecimal` als verlustfreien String. PostgreSQL nutzt
weiterhin `NUMERIC(38,18)`: Werte mit mehr als 18 Nachkommastellen, darunter
der reproduzierte CoinGecko-Kurs und seine Reward-Produkte, passen nicht
verlustfrei in diesen Vertrag. Diese getrennte Persistenzfrage erfordert vor
einem PostgreSQL-Produktiveinsatz eine eigene Schemaentscheidung; der
vorliegende reine Arithmetikfix ändert keine Migration oder Spalte.
