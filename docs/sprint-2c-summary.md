# Sprint 2C – Zusammenfassung

Sprint 2C ergänzt einen abgegrenzten Kraken-CSV-Adapter für Ledger History und
Trades History. Er erkennt Exporte über normalisierte Header, verarbeitet
UTF-8, BOM, LF/CRLF und korrekt gequotete Felder und sammelt Fehler vor jeder
Persistenz.

Originalwerte, Originalheader, unbekannte Zusatzfelder, Quelldateizeilen und
Kraken-IDs bleiben erhalten. Die typisierte Ansicht verwendet aware UTC-Zeit
und `Decimal`. Externe IDs lauten `kraken:ledger:<txid>` beziehungsweise
`kraken:trade:<txid>`.

Ledger und Trades nutzen getrennte Quellen. Dateiname, BOM und physische
Zeilenenden verändern bei gleichen Records den Hash nicht. Doppelte `txid`
innerhalb einer Datei werden abgewiesen; gleiche `ordertxid` sind zulässig.

Der Adapter erzeugt keine Steuerobjekte, normalisiert keine Assets und führt
keine Exporte zusammen. Dateiübergreifende Deduplizierung und steuerliche
Transformation sind Sprint 2D vorbehalten. Migration 0003 reicht aus; es wurde
keine Migration ergänzt.
