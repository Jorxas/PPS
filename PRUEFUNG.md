# Prüfstatus der öffentlichen Ableitung

Stand: 19. November 2026.

158 Tests bestanden, drei optionale Integrationstests übersprungen. Die Tests verwenden lokale Fixtures, einen lokal installierten FFprobe und den vorhandenen Python-Interpreter. Zwei Modelltests und ein CPU-Modelltest benötigen separat installierte Modelle. Keine Sprachsynthese oder Anbieter-API wurde für diese Veröffentlichung ausgeführt. Eine Abkündigungswarnung der FastAPI/Starlette-Testclient-Abhängigkeit bleibt sichtbar.

## Browser-Demo

Die öffentliche Demo führt die echte Weboberfläche mit erfundenen Fällen aus. `node tools/demo-pruefen.cjs` prüft die gebaute Demo unter dem Pfad `/PPS/` in Chrome: Start mit den Beispielfällen, ladbare Testsignale, Dauerschätzung, die ehrliche Meldung statt einer Synthese, Bewertung, Stimmen, Stimmenvorbereitung, Verlauf, Einstellungen sowie 390 Pixel Breite ohne horizontalen Überlauf. Jede Laufzeitfehlermeldung und jede Anfrage an einen fremden Server lässt die Prüfung scheitern; zusätzlich werden die gebauten Dateien auf private Pfade, Adressen und Schlüsselmuster durchsucht. Die Demo lädt keine externen Schriften oder Tracker. Stand dieser Browserprüfung: 22. November 2026.

## Reproduzierbarkeit

Die Startbefehle stehen in README.md. Test- und Browserprotokolle bleiben lokal; sie enthalten maschinenspezifische temporäre Pfade. Persönliche Logs werden deshalb nicht als Prüfbelege veröffentlicht.

## Grenzen

Ein lokal bestandener Test mit Fixtures ist kein Nachweis einer produktiven Cloud-Freigabe, einer echten Modellausführung oder eines Kundenbetriebs. Die Browser-Demo führt keine Synthese aus. Ihre Zahlen, Zeiten, Wortgrenzen und Bewertungen sind erfundene Beispielwerte, die Audios Testsignale.

Die öffentliche Kopie wurde vor dem ersten Push anhand ihrer Git-Dateiliste auf private Benutzerpfade, persönliche Hosts, E-Mail-Adressen und bekannte Schlüsselpräfixe geprüft. Ein solcher Scan ergänzt die begrenzte Dateiauswahl und ersetzt keine unabhängige Sicherheitsprüfung.
