// Browserprüfung der gebauten Demo in beiden Sprachen (Deutsch Standard, Französisch über ?lang=fr oder den Schalter DE | FR).
// Vorher: node tools/demo-bauen.mjs; einmalig: npm ci in tools/frontend.
// Benötigt einen installierten Chrome oder Edge (Umgebungsvariable BROWSER_PFAD). Die Demo wird wie auf GitHub Pages
// unter dem Pfad /PPS/ ausgeliefert; fremde Netzwerkanfragen zählen als Fehler.
// Optional: --bild=<datei.png> schreibt die Aufnahme der Synthese-Ansicht (README-Vorschau, deutsch).
const fs = require("node:fs"), path = require("node:path"), http = require("node:http"), assert = require("node:assert/strict");
const puppeteer = require("./frontend/node_modules/puppeteer-core");
const dist = path.resolve(__dirname, "../dist");
const bildArgument = process.argv.find((a) => a.startsWith("--bild="))?.slice(7);
const typen = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };

// Datenschutzprüfung der gebauten Dateien: keine privaten Pfade, Adressen oder Schlüsselmuster.
const muster = [/C:\\Users\\/i, /C:\/Users\//i, /\b[A-Za-z0-9._%+-]+@(?:gmail|outlook|hotmail|mni\.thm)\.[A-Za-z.]+/i, /\bghp_[A-Za-z0-9]{20,}/, /\bsk-(?:ant-)?[A-Za-z0-9_-]{30,}/, /tail[0-9a-f]+\.ts\.net/i, /-----BEGIN [^-]*PRIVATE KEY-----/];
function privatsphaere(ordner) {
  let zahl = 0;
  for (const e of fs.readdirSync(ordner, { withFileTypes: true })) {
    const p = path.join(ordner, e.name);
    if (e.isDirectory()) { zahl += privatsphaere(p); continue; }
    zahl++;
    assert(!/\.(?:pdf|eml|db|sqlite|zip|wav|mp3|mp4)$/i.test(e.name), p + ": unzulässiger Dateityp");
    assert(!/swagger/i.test(e.name), p + ": Swagger gehört nicht zur Demo");
    const text = fs.readFileSync(p, "utf8");
    for (const m of muster) assert(!m.test(text), p + ": mögliches privates Muster");
  }
  return zahl;
}

const server = http.createServer((anfrage, antwort) => {
  let pfad = decodeURIComponent(new URL(anfrage.url, "http://x").pathname);
  if (!pfad.startsWith("/PPS/")) { antwort.statusCode = 404; return antwort.end("nicht gefunden"); }
  pfad = pfad.replace(/^\/PPS/, "") || "/";
  if (pfad.endsWith("/")) pfad += "index.html";
  const datei = path.join(dist, pfad);
  if (!datei.startsWith(dist) || !fs.existsSync(datei)) { antwort.statusCode = 404; return antwort.end("nicht gefunden"); }
  antwort.setHeader("Content-Type", typen[path.extname(datei)] || "application/octet-stream");
  antwort.end(fs.readFileSync(datei));
});
const browserPfad = process.env.BROWSER_PFAD || ["C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe", "/usr/bin/google-chrome", "/usr/bin/chromium"].find((f) => fs.existsSync(f));
if (!browserPfad) throw new Error("BROWSER_PFAD auf einen installierten Chrome oder Edge setzen.");
const warte = (ms) => new Promise((r) => setTimeout(r, ms));

// Erwartete Texte je Sprache. Die Demo startet auf Deutsch; Französisch ist die ursprüngliche Sprache der Oberfläche.
// "fremd" findet Wörter der jeweils anderen Sprache in der sichtbaren Oberfläche.
const T = {
  de: {
    titel: "PPS · Sprachsynthese", rahmen: "Testsignale", stimmenBereit: "2 Ausschnitte verfügbar", ergebnis: "Bereit zum Anhören",
    weiter: " Das ist ein weiterer Satz.", dauer: "Wörter/Min.", zaehler: "Wörter", ehrlich: "keine Sprach-Engine",
    mara: "1 Ausschnitt verfügbar", bestaetigen: "bestätigen", fehlgeschlagen: "Fehlgeschlagen", abgebrochen: "Abgebrochen",
    speicher: "Grafikspeicher", ressourcen: "RAM verfügbar", engines: "Vorbereitung unvollständig", docs: "API-Dokumentation",
    fremd: /\b(voix|extraits?|moteurs?|réglages|historique|synthèse|enregistr\w+|choisir|écout\w+|fichiers?|chargement|ajout\w+|annul\w+|démo|référence|dispon\w+|prépar\w+|échec)\b/i,
  },
  fr: {
    titel: "PPS · Synthèse vocale", rahmen: "signaux de test", stimmenBereit: "2 extraits disponibles", ergebnis: "Prêt à écouter",
    weiter: " Ceci est une phrase de plus.", dauer: "mots/min", zaehler: "mots", ehrlich: "aucun moteur vocal",
    mara: "1 extrait disponible", bestaetigen: "Confirmer", fehlgeschlagen: "Échec", abgebrochen: "Annulé",
    speicher: "Mémoire graphique", ressourcen: "RAM disponible", engines: "préparation incomplète", docs: "documentation",
    fremd: /\b(Stimmen?|Ausschnitt\w*|Einstellungen|Verlauf|Sprachsynthese|Sprach-Engine|Dateien|Datei|Ihre\w*|Wörter|erzeugen|Anhören|Bereit|Engines?|Zeichen|Meine)\b/,
  },
};

(async () => {
  assert(fs.existsSync(path.join(dist, "index.html")), "dist fehlt: zuerst node tools/demo-bauen.mjs ausführen");
  const dateien = privatsphaere(dist);
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const basis = `http://127.0.0.1:${server.address().port}/PPS/`;
  const browser = await puppeteer.launch({ executablePath: browserPfad, headless: true });
  const fehler = [], extern = [];
  try {
    // Eine frische Seite je Durchlauf: Die Demo setzt sich beim Neuladen zurück.
    const neueSeite = async (adresse) => {
      const seite = await browser.newPage();
      seite.on("pageerror", (e) => fehler.push("Seitenfehler: " + e.message));
      seite.on("console", (m) => { if (m.type() === "error") fehler.push("Konsole: " + m.text()); });
      // Abgebrochene Ladevorgänge der Testsignale (Element wurde ersetzt) sind kein Fehler; alles andere schon.
      seite.on("requestfailed", (r) => { if (!(r.url().startsWith("blob:") && r.failure()?.errorText === "net::ERR_ABORTED")) fehler.push("Anfrage gescheitert: " + r.url() + " " + r.failure()?.errorText); });
      seite.on("response", (r) => { if (r.status() >= 400) fehler.push("HTTP " + r.status() + ": " + r.url()); });
      seite.on("request", (r) => {
        const u = new URL(r.url());
        if (u.protocol === "http:" || u.protocol === "https:") { if (u.hostname !== "127.0.0.1") extern.push(r.url()); else if (!u.pathname.startsWith("/PPS/")) extern.push(r.url()); }
        else if (!["blob:", "data:", "about:"].includes(u.protocol)) extern.push(r.url());
      });
      await seite.setViewport({ width: 1440, height: 1560 });
      await seite.goto(adresse, { waitUntil: "networkidle0" });
      await warte(500);
      return seite;
    };
    const lesen = (seite) => ({
      text: (sel) => seite.$eval(sel, (e) => e.textContent),
      anzahl: (sel) => seite.$$eval(sel, (e) => e.length),
    });
    // Sichtbarer Text samt Platzhaltern, Beschriftungen und Titeln: darf keine Wörter der anderen Sprache enthalten.
    const rein = async (seite, sprache, wo) => {
      const alles = await seite.evaluate(() => [document.body.innerText, ...[...document.querySelectorAll("[placeholder],[aria-label],[title]")].map((e) => [e.placeholder, e.getAttribute("aria-label"), e.title].filter(Boolean).join(" "))].join("\n"));
      const treffer = alles.match(new RegExp(T[sprache].fremd.source, T[sprache].fremd.flags + "g"));
      assert.equal(treffer, null, `${wo}: Wörter der anderen Sprache in der Oberfläche (${sprache}): ${treffer?.slice(0, 5).join(", ")}`);
    };
    const handy = async (seite, sprache) => {
      await seite.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });
      for (const ansicht of ["synthesis", "voices", "history", "settings"]) {
        await seite.click(`[data-view="${ansicht}"]`);
        await warte(300);
        const breite = await seite.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
        assert(breite[0] <= breite[1], `kein horizontaler Überlauf (${sprache}, ${ansicht}: ${breite[0]} > ${breite[1]})`);
      }
      await seite.setViewport({ width: 1440, height: 1560 });
    };

    // Ganzer Ablauf in einer Sprache (frische Seite): Start, Dauerschätzung, Synthese-Hinweis, Bewertung, Stimmen, Verlauf, Einstellungen, Handy.
    async function ablauf(sprache, adresse, bild) {
      const t = T[sprache];
      const seite = await neueSeite(adresse);
      const { text, anzahl } = lesen(seite);
      assert.equal(await seite.$eval("html", (e) => e.lang), sprache, "html lang");
      assert((await seite.title()).startsWith(t.titel), "Titel der echten Oberfläche: " + (await seite.title()));
      assert.equal(await anzahl("[data-demo-rahmen]"), 1, "genau ein Rahmenhinweis");
      assert.equal(await anzahl("[data-demo-lang]"), 2, "Sprachschalter DE | FR im Hinweis");
      assert.equal(await seite.$eval(`[data-demo-lang="${sprache}"]`, (b) => b.getAttribute("aria-pressed")), "true", "aktive Sprache markiert");
      assert((await text("[data-demo-rahmen]")).includes(t.rahmen), "Hinweis: Testsignale statt Sprache");
      assert.equal(await anzahl("#voice option"), 3, "drei Stimmen");
      assert((await text("#voice")).includes("Alex Beispiel"), "erfundene Stimme");
      assert.equal(await text("#voice-count"), "3", "Zähler der Stimmen");
      assert.equal(await anzahl(".error"), 0, "keine Fehlermeldung beim Start");
      assert.equal(await anzahl("#chatterbox-result audio, #cosyvoice-result audio"), 2, "Vergleich mit zwei Audios");
      assert((await text("#voice-readiness")).includes(t.stimmenBereit), "Referenzen der Stimme: " + (await text("#voice-readiness")));
      assert.equal(await seite.$eval("#language", (s) => s.value), sprache, "Textsprache folgt der Oberfläche");
      const audio = await seite.$eval("#chatterbox-result audio", async (a) => {
        await new Promise((r, f) => { a.addEventListener("loadedmetadata", r, { once: true }); a.addEventListener("error", () => f(new Error("Audio nicht ladbar")), { once: true }); if (a.readyState >= 1) r(); });
        return { quelle: a.src.slice(0, 5), dauer: a.duration };
      });
      assert.equal(audio.quelle, "blob:", "Audio kommt aus einem erzeugten Testsignal");
      assert(Math.abs(audio.dauer - 5.6) < 0.1, "Dauer des Testsignals: " + audio.dauer);
      assert((await text("#cosyvoice-result")).includes("asr_unverified"), "Revision der zweiten Ausgabe offen");
      assert((await text("#chatterbox-result")).includes(t.ergebnis), "Ergebnis fertig");
      await rein(seite, sprache, "Synthese-Ansicht");
      if (bild) await seite.screenshot({ path: path.resolve(bild) });

      // Text eingeben: Dauerschätzung (nachgebaute Rechnung des Dienstes) mit Dezimalkomma in beiden Sprachen
      assert((await text("#duration-estimate")).includes(t.dauer), "Dauerschätzung für den Beispieltext: " + (await text("#duration-estimate")));
      assert(/\d,\d/.test(await text("#duration-estimate")), "Dezimalkomma in der Schätzung");
      await seite.type("#text", t.weiter);
      await warte(700);
      assert((await text("#text-count")).includes(t.zaehler), "Zähler");
      // Synthese: ehrlicher Hinweis statt erfundenem Ergebnis
      await seite.click("#generate");
      await warte(400);
      assert((await text("#message")).includes(t.ehrlich), "Synthese: ehrlicher Hinweis: " + (await text("#message")));
      assert(await seite.$eval("#generate", (b) => !b.disabled), "Schaltfläche wieder frei");
      // Der Vergleich der Engines ist wegen unbestätigter Transkription bei Mara gesperrt
      await seite.select("#voice", "voix-mara");
      await warte(400);
      assert(await seite.$eval("#compare", (b) => b.disabled), "Vergleich bei unbestätigter Transkription gesperrt");
      assert((await text("#voice-readiness")).includes(t.mara), "Referenz von Mara");
      await seite.select("#voice", "voix-alex");
      await warte(400);

      // Bewertung der zweiten Ausgabe speichern
      await seite.evaluate(() => document.querySelector("#cosyvoice-result details").open = true);
      await seite.select("#cosyvoice-result .review-form select", "4");
      await seite.click("#cosyvoice-result .review-form button");
      await warte(300);
      assert.equal(await anzahl("#cosyvoice-result .review-form .error"), 0, "Bewertung ohne Fehler gespeichert");

      // Meine Stimmen: zwei Karten, vorbereiteter Fall von Jonas, Import deaktiviert
      await seite.click('[data-view="voices"]');
      await warte(400);
      assert.equal(await anzahl(".voice-card:not([hidden])"), 2, "Karten von Alex und Mara");
      assert((await text("#voice-preparations")).includes("Jonas Probe"), "vorbereiteter Fall von Jonas");
      assert((await text("#voice-preparations")).includes(t.bestaetigen), "Bestätigung möglich");
      assert.equal(await anzahl("#voice-preparations audio"), 2, "Testsignale der Kandidaten");
      await seite.evaluate(() => (document.getElementById("voice-import").open = true));
      assert(await seite.$eval("#media-upload", (b) => b.disabled), "Import deaktiviert");
      assert.equal(await anzahl("[data-demo-import]"), 2, "Hinweise zum deaktivierten Import");
      await rein(seite, sprache, "Stimmen-Ansicht");
      await seite.evaluate((w) => [...document.querySelectorAll("#voice-preparations button")].find((b) => b.textContent.includes(w))?.click(), t.bestaetigen);
      await warte(600);
      assert((await text("#message")).includes("Jonas Probe"), "Stimme bestätigt: " + (await text("#message")));
      assert.equal(await seite.$eval("#voice", (s) => s.selectedOptions[0].textContent), "Jonas Probe", "neue Stimme gewählt");
      assert((await text("#voice-readiness")).includes(t.mara), "neue Referenz sichtbar");

      // Verlauf: fertige, fehlgeschlagene und abgebrochene Aufträge
      await seite.click('[data-view="history"]');
      await warte(300);
      assert.equal(await anzahl(".history-item"), 5, "fünf Aufträge");
      const verlauf = await text("#history");
      assert(verlauf.includes(t.fehlgeschlagen) && verlauf.includes(t.abgebrochen) && verlauf.includes(t.ergebnis), "Zustände im Verlauf");
      await rein(seite, sprache, "Verlauf");
      await seite.evaluate((w) => [...document.querySelectorAll(".history-item")].find((e) => e.textContent.includes(w)).querySelector("button").click(), t.fehlgeschlagen);
      await warte(500);
      assert((await text("#chatterbox-result")).includes(t.speicher), "Fehlermeldung des gescheiterten Auftrags");

      // Einstellungen: Beispielwerte gekennzeichnet, Dokumentationslink ohne Swagger
      await seite.click('[data-view="settings"]');
      await warte(300);
      assert((await text("#resources")).includes(t.ressourcen), "Dienstzustand");
      assert((await text("#engines")).includes(t.engines), "Engines ehrlich nicht installiert");
      assert.equal(await anzahl("[data-demo-dienst]"), 1, "Hinweis in den Einstellungen");
      await rein(seite, sprache, "Einstellungen");
      await seite.click("[data-demo-docs]");
      await warte(200);
      assert((await text("[data-demo-hinweis]")).includes(t.docs), "Hinweis statt Swagger");
      assert(seite.url().startsWith(basis), "keine Navigation zu einer fremden Seite");

      await handy(seite, sprache);
      await seite.close();
    }
    await ablauf("de", basis, bildArgument);
    await ablauf("fr", basis + "?lang=fr");

    // Umschalten ohne Neuladen: Deutsch (Standard) -> Französisch -> Deutsch, mit Wirkung auf feste und erzeugte Texte.
    {
      const seite = await neueSeite(basis + "#voices");
      const { text, anzahl } = lesen(seite);
      assert.equal(await seite.$eval("html", (e) => e.lang), "de", "Standardsprache Deutsch");
      assert.equal(await text("#page-title"), "Meine Stimmen");
      await rein(seite, "de", "Start auf Deutsch");
      await seite.click('[data-demo-lang="fr"]');
      await warte(900);
      assert.equal(await seite.$eval("html", (e) => e.lang), "fr", "html lang nach dem Wechsel");
      assert.equal(new URL(seite.url()).searchParams.get("lang"), "fr", "?lang=fr in der Adresse");
      assert.equal(new URL(seite.url()).hash, "#voices", "Ansicht bleibt erhalten");
      assert.equal(await text("#page-title"), "Mes voix");
      assert((await text("#voice-preparations")).includes("Votre voix est presque prête"), "Vorbereitung auf Französisch");
      assert((await text(".voice-card")).includes("Alex Beispiel") || (await text(".voice-card")).length > 0, "Karte bleibt");
      await rein(seite, "fr", "nach dem Wechsel");
      await seite.click('[data-view="synthesis"]');
      await warte(300);
      assert((await text("#chatterbox-result")).includes("Prêt à écouter"), "Ergebnis auf Französisch");
      assert((await seite.$eval("#text", (e) => e.value)).startsWith("Bonjour et bienvenue"), "Beispieltext auf Französisch");
      await handy(seite, "fr");
      await seite.click('[data-demo-lang="de"]');
      await warte(900);
      assert.equal(await seite.$eval("html", (e) => e.lang), "de", "zurück auf Deutsch");
      assert.equal(new URL(seite.url()).searchParams.get("lang"), null, "Deutsch ist die Voreinstellung ohne Parameter");
      assert((await text("#chatterbox-result")).includes("Bereit zum Anhören"), "Ergebnis wieder auf Deutsch");
      assert((await seite.$eval("#text", (e) => e.value)).startsWith("Guten Tag und herzlich willkommen"), "Beispieltext wieder auf Deutsch");
      await rein(seite, "de", "zurück auf Deutsch");
      assert.equal(await anzahl(".error"), 0, "keine Fehlermeldung beim Wechseln");
      await handy(seite, "de");
      await seite.close();
    }
  } finally { await browser.close(); server.close(); }
  assert.deepEqual(fehler, [], "keine Laufzeitfehler");
  assert.deepEqual(extern, [], "keine externen Anfragen");
  console.log(`Browserprüfung der Demo bestanden (${dateien} Dateien ohne private Muster): Deutsch (Standard) und Französisch, je Start, Testsignale, Dauerschätzung, ehrliche Synthese-Meldung, Bewertung, Stimmen, Vorbereitung, Verlauf, Einstellungen, Handy 390 px; Umschalten DE, FR, DE ohne Neuladen; keine Fehler, keine externen Anfragen.`);
})().catch((e) => { console.error(e); process.exitCode = 1; server.close(); });
