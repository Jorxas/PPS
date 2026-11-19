# PPS – lokales Sprachstudio

Ein unabhängiger Dienst verwaltet Referenzstimmen, Syntheseaufträge, Ressourcen, Wortausrichtung und Qualitätsprüfung. Die Sprachmodelle werden integriert; sie wurden nicht in diesem Projekt trainiert.

**Technik:** Python, FastAPI, SQLite, Chatterbox, CosyVoice.

[Interaktive Demo](https://jorxas.github.io/PPS/) · [Portfolio](https://jorxas.github.io/Portfolio/) · [Architektur](ARCHITEKTUR.md) · [Prüfstatus](PRUEFUNG.md)

## Ausprobieren

Die vollständige Projektseite mit Demo liegt in `site/index.html`. Sie arbeitet mit erfundenen Daten. Externe Dienste, Anbieter-Konten und Hintergrundaufträge werden dort nicht ausgeführt. Die Oberfläche kennzeichnet die jeweiligen Simulationen.

## Demo lokal öffnen

~~~sh
python -m http.server 8080 --bind 127.0.0.1 --directory site
~~~

Danach http://127.0.0.1:8080 öffnen. Die Demo benötigt weder Konten noch Modellgewichte.

## Lokal prüfen

~~~sh
python -m pip install -r requirements/api.txt
python -m pytest -q
~~~

Die genauen geprüften Befehle und Grenzen stehen in [PRUEFUNG.md](PRUEFUNG.md). Produktive Schlüssel, persönliche Daten und Modelle gehören ausschließlich in die lokale Installation.

## Herkunft

Dies ist eine für die Veröffentlichung geprüfte Ableitung eines bestehenden lokalen Projekts. [HERKUNFT.md](HERKUNFT.md) trennt den ursprünglichen Entwicklungsstand von den jetzt ausgeführten Veröffentlichungsarbeiten. Die ursprünglichen Oberflächen und technischen Bezeichner sind teilweise französisch oder englisch. Neue Präsentation und Dokumentation sind deutsch.

## Rechte und Abhängigkeiten

Für eigenen Quellcode wird hier keine zusätzliche Nutzungslizenz eingeräumt. Die Lizenzen der genannten Bibliotheken und Modelle gelten unabhängig davon. Fremde Modelle, Stimmen, Medien und Zugangsdaten werden nicht mitgeliefert. Siehe [DRITTANBIETER.md](DRITTANBIETER.md).

## Lokale Werkzeuge und Modelle

FFprobe wird über `PPS_FFPROBE`, den Suchpfad oder `tools/media/ffprobe.exe` gefunden. Modellquellen und Interpreter stehen in `config.toml`; an die eigene Installation anpassen. Modellgewichte und die optionalen Analysemodelle (VAD/YAMNet) werden nicht mitgeliefert. Die zugehörigen CPU-Integrationstests werden ohne diese Modelle ausdrücklich übersprungen. Für den API-Dienst: `python -m pps`. Die Oberfläche ist ohne installierte Modelle sichtbar; eine echte Synthese ist dann nicht verfügbar.

## Modellinstallation

`requirements/api.txt` beschreibt den schlanken API-Dienst. Die getrennten Modellumgebungen und Quellpakete werden zusätzlich benötigt; `scripts/setup_engines.py` dokumentiert die lokalen Installationsschritte. Vor Ausführung die Pfade und Lizenzbedingungen prüfen. Der Dienst lädt während einer API-Anfrage keine Modelle aus dem Internet nach. Die für Medienanalyse optional benötigten VAD/YAMNet-Ressourcen sind externe Modelle und separat zu installieren.
