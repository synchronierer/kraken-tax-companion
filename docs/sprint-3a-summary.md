# Sprint 3A – Abschlusszusammenfassung

Sprint 3A wurde am 1. August 2026 technisch freigegeben. Der verbindliche
Hostlauf endete mit `SPRINT_3A_HOST_VALIDATION_OK`; alle vereinbarten
Qualitätsgates und der vollständige vertikale Produktschnitt sind bestanden.

## Technisches und sichtbares Ergebnis

Die Anwendung verarbeitet Kraken-Ledger- und Trades-CSV ohne Mockdaten vom
Import über die versionierte Transformation bis zur EUR-Bewertung. Die erste
deutsche Weboberfläche umfasst Übersicht, Importe, Vorgänge, Bewertungen,
Prüffälle und Systemstatus. Reale Listen und Details zeigen fachliche Daten,
Status, Auditverlauf und vorhandene Provenienzglieder.

Das Produktionsfrontend verwendet relative `/api`-Pfade. Frontend-Nginx
leitet diese innerhalb des Docker-Netzwerks an das Backend weiter. Dieser
Same-Origin-Vertrag benötigt weder eine fest eingebaute Server-IP noch den
Docker-Servicenamen im Browser und funktioniert deshalb auch von anderen
Rechnern im lokalen Netzwerk.

## Bewertung und Nachweise

Die providerneutrale Bewertungsdomain unterstützt:

- `NATIVE_EUR` für eindeutig aus der Kraken-Ausführung bekannte EUR-Werte,
  ohne Providerabruf;
- `DAILY_AVERAGE_HOURLY` als Decimal-Mittel gültiger Stundenwerte eines
  abgeschlossenen UTC-Tags mit mindestens 20 Beobachtungen;
- `MANUAL_DAILY_PRICE` für positive, begründete und versionierte manuelle
  Tageskurse sowie atomar validierte Kurs-CSV.

Native EUR-Erwerbe, -Veräußerungen und -Gebühren durchlaufen dieselbe
Requirement-, Run- und Decision-Kette wie andere Bewertungen. Stückpreis und
Gesamtwert bleiben exakt; es gibt weder Floatarithmetik noch frühe Rundung.
Automatische Bewertungen verwenden den austauschbaren Provider-Port und den
CoinGecko-Adapter. Provider-Evidenz bewahrt normalisierte Beobachtungen,
Vertragsversion, Anfragefenster, Antwortstatus und kanonischen Hash, aber keine
Schlüssel oder geheimen Header.

## Idempotenz, Versionierung und Provenienz

Der Importvertrag erkennt identische Dateien. `transform=true` verwendet für
dieselbe ursprüngliche ImportSession und Transformationsvertragsversion einen
bereits erfolgreichen `TransformationRun` wieder. Run-ID, Domainobjekte,
ValuationRequirements und Auditnachweise werden nicht dupliziert. Eine neue
Version darf kontrolliert einen neuen Lauf erzeugen; ein fehlgeschlagener Lauf
gilt nicht als wiederverwendbarer Erfolg.

Preisnachweise und Bewertungsentscheidungen sind unveränderlich. Identische
Evidenz erzeugt kein Duplikat. Abweichende Evidenz, begründete Korrekturen oder
neue Methoden- und Vertragsversionen erzeugen eine neue Version mit
`supersedes_id`; historische Werte bleiben erhalten. Manuelle Kurse löschen
automatische Kurse nicht.

Detailantworten bilden ausschließlich den tatsächlich vorhandenen Pfad ab:

```text
ImportSession
→ RawImportRecord
→ TransformationRun
→ Domainobjekt
→ ValuationRequirement
→ ProviderEvidence oder manueller Nachweis
→ DailyPrice
→ ValuationDecision
→ AuditEvents
```

Optionale, nicht vorhandene Glieder bleiben `null` beziehungsweise leer und
werden nicht erfunden.

## Persistenz und Migrationen

Migration 0005 persistiert Bewertungsruns, Provider-Evidenz, Tagespreise,
Entscheidungen, Versionen, Superseding-Bezüge und fachliche Constraints.
`UtcDateTime` verwendet auf PostgreSQL `TIMESTAMP WITH TIME ZONE` und bewahrt
auf beiden Datenbanken den aware-UTC-Vertrag. Strukturierte Daten verwenden
PostgreSQL `JSONB` und SQLite `JSON`.

Upgrade bis Head, Alembic Check, Downgrade von 0005 auf 0004, erneutes Upgrade
und ein weiterer Check sind sowohl für SQLite als auch PostgreSQL erfolgreich
und driftfrei abgeschlossen.

## Verbindlich bestätigte Qualität

- Ruff: bestanden
- Black: bestanden
- MyPy strict: bestanden
- Backend: 158 Tests bestanden
- Backend-Coverage: 100,00 Prozent bei 2570 Statements
- Frontend: 7 Tests bestanden
- ESLint: bestanden
- Typecheck: bestanden
- Frontend-Produktionsbuild: bestanden
- Markdownlint: bestanden
- SQLite-Migrationszyklus: bestanden und driftfrei
- PostgreSQL-Migrationszyklus: bestanden und driftfrei
- Docker-Compose-Smoke-Test: bestanden
- Import, Transformation und Transformationsidempotenz: bestanden
- manuelle Bewertung, Providerbewertung und `NATIVE_EUR`: bestanden
- vollständige Hostvalidierung: `SPRINT_3A_HOST_VALIDATION_OK`

## Nicht blockierender Wartungspunkt

`npm audit` meldete zwei Funde mit Schweregrad High. Es wird ausdrücklich kein
blindes `npm audit fix --force` ausgeführt. Die Advisory-IDs, direkte oder
transitive Herkunft, Produktions- oder Entwicklungsrelevanz und verfügbare
kompatible Aktualisierungen werden in einem getrennten Wartungsschritt
analysiert. Dieser Folgepunkt ändert den bestätigten Sprint-3A-Abschluss nicht.

## Abgrenzung zu Sprint 3B

Sprint 3A enthält weder FIFO beziehungsweise eine andere Verbrauchsfolge noch
Haltefrist, Gewinnermittlung, Steuerjournal, Steuerexport oder PDF-Bericht.
Sprint 3B baut auf der vorhandenen Navigation, den unveränderlichen
Bewertungsnachweisen und der vollständigen Provenienz auf.
