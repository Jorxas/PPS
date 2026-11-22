# Architektur

Ein unabhängiger Dienst verwaltet Referenzstimmen, Syntheseaufträge, Ressourcen, Wortausrichtung und Qualitätsprüfung. Die Sprachmodelle werden integriert; sie wurden nicht in diesem Projekt trainiert.

## Verantwortungsgrenzen

Die lokale Anwendung enthält den eigentlichen Dienst beziehungsweise Motor. Die öffentliche Browser-Demo zeigt dieselbe Weboberfläche ohne Dienst: `demo/demo-start.js` ersetzt die Antworten von `/v1/*` durch erfundene Fälle. Ihre Daten stammen nicht aus einer produktiven Installation.

## Prüfbarkeit

Tests verwenden isolierte temporäre Daten. Anbieteraufrufe und Kontozugriffe gehören nicht zur öffentlichen Demo. Ein bestandener Vertragstest mit lokalen Fixtures ist kein Nachweis einer produktiven Anbieterfreigabe.
