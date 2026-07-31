# ADR 0011: EUR-Bewertung und erste Weboberfläche

- Status: Accepted
- Date: 2026-07-30

## Kontext

Sprint 2D erzeugt bewertungsbedürftige Fachereignisse, aber keine Kurse.
Nachweise müssen konsistent, reproduzierbar und von späterer Steuerlogik
getrennt bleiben.

## Entscheidung

Native EUR-Werte bleiben Kraken-Evidenz. Sonstige Werte verwenden hinter einem
providerneutralen Port zunächst CoinGecko und den ungewichteten Durchschnitt
stündlicher Punkte eines abgeschlossenen UTC-Tags. Mindestens 20 Punkte sind
erforderlich. Manuelle Tageskurse sind begründet, auditierbar und versioniert.

Bewertungsruns, Tagespreise und Entscheidungen sind unveränderliche Nachweise
mit Methoden-, Mapping- und Providervertragsversion, Provenienz und ohne frühe
Rundung. Die erste React-UI bildet den vertikalen Produktschnitt ab und zeigt
nur Backend-Ergebnisse.

Providerantworten werden nicht vollständig archiviert. Eine eigenständige
unveränderliche Evidenz speichert das Anfragefenster, Status, Asset-ID,
kanonischen Hash und normalisierte Decimal-/UTC-Beobachtungen. Tagespreise und
Entscheidungen verweisen darauf.

Nachweise werden nicht aktualisiert. Identische Evidenz wird als Duplikat
behandelt; abweichende Evidenz oder eine kontrollierte neue Methodenversion
erzeugt einen neuen Satz mit Rückverweis. Der effektive Superseded-Zustand
wird aus dieser Nachfolgekette abgeleitet. Dadurch bleibt auch der vorherige
Status als historische Tatsache unverändert.

Dies unterstützt die Nachvollziehbarkeit und konsistente Kursquellenwahl nach
dem BMF-Schreiben vom 6. März 2025, ersetzt aber keine individuelle
steuerrechtliche Prüfung.

## Konsequenzen

Das Steuerjournal, FIFO, Haltefristen, Gewinnermittlung und Berichte bleiben
Sprint 3B vorbehalten. Dessen Navigation kann die bestehende App-Struktur
ergänzen, ohne den Sprint-3A-Ablauf neu zu entwickeln.
