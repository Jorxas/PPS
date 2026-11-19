# Prüfstatus der öffentlichen Ableitung

Stand: 19. November 2026.

158 Tests bestanden, drei optionale Integrationstests übersprungen. Die Tests verwenden lokale Fixtures, einen lokal installierten FFprobe und den vorhandenen Python-Interpreter. Zwei Modelltests und ein CPU-Modelltest benötigen separat installierte Modelle. Keine Sprachsynthese oder Anbieter-API wurde für diese Veröffentlichung ausgeführt. Eine Abkündigungswarnung der FastAPI/Starlette-Testclient-Abhängigkeit bleibt sichtbar.

## Browser-Demo

Die öffentliche Projektseite wurde bei 390 und 1440 Pixeln in Chrome geprüft: Code- und Demoverweise, Größenanpassung, interaktive Eingaben, JavaScript-Fehler, Content-Security-Policy und ausgehende Netzwerkanfragen. Die Prüfung blockiert fremde Netzwerkanfragen. Die Seite lädt keine externen Schriften oder Tracker.

## Reproduzierbarkeit

Die Startbefehle stehen in README.md. Test- und Browserprotokolle bleiben lokal; sie enthalten maschinenspezifische temporäre Pfade. Persönliche Logs werden deshalb nicht als Prüfbelege veröffentlicht.

## Grenzen

Ein lokal bestandener Test mit Fixtures ist kein Nachweis einer produktiven Cloud-Freigabe, einer echten Modellausführung oder eines Kundenbetriebs. Die Browser-Demo kennzeichnet gespeicherte beziehungsweise simulierte Ergebnisse.

Die öffentliche Kopie wurde vor dem ersten Push anhand ihrer Git-Dateiliste auf private Benutzerpfade, persönliche Hosts, E-Mail-Adressen und bekannte Schlüsselpräfixe geprüft. Ein solcher Scan ergänzt die begrenzte Dateiauswahl und ersetzt keine unabhängige Sicherheitsprüfung.
