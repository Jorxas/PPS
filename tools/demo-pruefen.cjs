// Browserprüfung der gebauten Demo (vorher: node tools/demo-bauen.mjs; einmalig: npm ci in tools/frontend).
// Benötigt einen installierten Chrome oder Edge (Umgebungsvariable BROWSER_PFAD). Die Demo wird wie auf GitHub Pages
// unter dem Pfad /PPS/ ausgeliefert; fremde Netzwerkanfragen zählen als Fehler.
// Optional: --bild=<datei.png> schreibt die Aufnahme der Synthese-Ansicht (README-Vorschau).
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

(async () => {
  assert(fs.existsSync(path.join(dist, "index.html")), "dist fehlt: zuerst node tools/demo-bauen.mjs ausführen");
  const dateien = privatsphaere(dist);
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const basis = `http://127.0.0.1:${server.address().port}/PPS/`;
  const browser = await puppeteer.launch({ executablePath: browserPfad, headless: true });
  const fehler = [], extern = [];
  try {
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
    await seite.goto(basis, { waitUntil: "networkidle0" });
    await warte(500);
    const text = (sel) => seite.$eval(sel, (e) => e.textContent);
    const anzahl = (sel) => seite.$$eval(sel, (e) => e.length);

    // Start: echte Oberfläche, Rahmenhinweis, drei Stimmen, Vergleich aus dem Verlauf wiederhergestellt
    assert((await seite.title()).startsWith("PPS · Synthèse vocale"), "Titel der echten Oberfläche");
    assert.equal(await anzahl("[data-demo-rahmen]"), 1, "genau ein Rahmenhinweis");
    assert((await text("[data-demo-rahmen]")).includes("signaux de test"), "Hinweis: Testsignale statt Sprache");
    assert.equal(await anzahl("#voice option"), 3, "drei Stimmen");
    assert((await text("#voice")).includes("Alex Beispiel"), "erfundene Stimme");
    assert.equal(await text("#voice-count"), "3", "Zähler der Stimmen");
    assert.equal(await anzahl(".error"), 0, "keine Fehlermeldung beim Start");
    assert.equal(await anzahl("#chatterbox-result audio, #cosyvoice-result audio"), 2, "Vergleich mit zwei Audios");
    assert((await text("#voice-readiness")).includes("2 extraits disponibles"), "Referenzen der Stimme: " + (await text("#voice-readiness")));
    const audio = await seite.$eval("#chatterbox-result audio", async (a) => {
      await new Promise((r, f) => { a.addEventListener("loadedmetadata", r, { once: true }); a.addEventListener("error", () => f(new Error("Audio nicht ladbar")), { once: true }); if (a.readyState >= 1) r(); });
      return { quelle: a.src.slice(0, 5), dauer: a.duration };
    });
    assert.equal(audio.quelle, "blob:", "Audio kommt aus einem erzeugten Testsignal");
    assert(Math.abs(audio.dauer - 5.6) < 0.1, "Dauer des Testsignals: " + audio.dauer);
    assert((await text("#cosyvoice-result")).includes("asr_unverified"), "Revision der zweiten Ausgabe offen");
    assert((await text("#chatterbox-result")).includes("Prêt à écouter"), "Ergebnis fertig");
    if (bildArgument) await seite.screenshot({ path: path.resolve(bildArgument) });

    // Text eingeben: Dauerschätzung (nachgebaute Rechnung des Dienstes)
    assert((await text("#duration-estimate")).includes("mots/min"), "Dauerschätzung für den Beispieltext");
    await seite.type("#text", " Ceci est une phrase de plus.");
    await warte(700);
    assert((await text("#text-count")).includes("mots"), "Zähler");
    // Synthese: ehrlicher Hinweis statt erfundenem Ergebnis
    await seite.click("#generate");
    await warte(400);
    assert((await text("#message")).includes("aucun moteur vocal"), "Synthese: ehrlicher Hinweis");
    assert(await seite.$eval("#generate", (b) => !b.disabled), "Schaltfläche wieder frei");
    // Vergleich der Moteurs ist wegen unbestätigter Transkription bei Mara gesperrt
    await seite.select("#voice", "voix-mara");
    await warte(400);
    assert(await seite.$eval("#compare", (b) => b.disabled), "Vergleich bei unbestätigter Transkription gesperrt");
    assert((await text("#voice-readiness")).includes("1 extrait disponible"), "Referenz von Mara");
    await seite.select("#voice", "voix-alex");
    await warte(400);

    // Bewertung der zweiten Ausgabe speichern
    await seite.evaluate(() => document.querySelector("#cosyvoice-result details").open = true);
    await seite.select("#cosyvoice-result .review-form select", "4");
    await seite.click("#cosyvoice-result .review-form button");
    await warte(300);
    assert.equal(await anzahl("#cosyvoice-result .review-form .error"), 0, "Bewertung ohne Fehler gespeichert");

    // Mes voix: zwei Karten, vorbereiteter Fall von Jonas, Import deaktiviert
    await seite.click('[data-view="voices"]');
    await warte(400);
    assert.equal(await anzahl(".voice-card:not([hidden])"), 2, "Karten von Alex und Mara");
    assert((await text("#voice-preparations")).includes("Jonas Probe"), "vorbereiteter Fall von Jonas");
    assert((await text("#voice-preparations")).includes("Confirmer cette voix"), "Bestätigung möglich");
    assert.equal(await anzahl("#voice-preparations audio"), 2, "Testsignale der Kandidaten");
    await seite.evaluate(() => (document.getElementById("voice-import").open = true));
    assert(await seite.$eval("#media-upload", (b) => b.disabled), "Import deaktiviert");
    assert.equal(await anzahl("[data-demo-import]"), 2, "Hinweise zum deaktivierten Import");
    await seite.evaluate(() => [...document.querySelectorAll("#voice-preparations button")].find((b) => b.textContent.includes("Confirmer"))?.click());
    await warte(600);
    assert((await text("#message")).includes("Jonas Probe"), "Voix bestätigt: " + (await text("#message")));
    assert.equal(await seite.$eval("#voice", (s) => s.selectedOptions[0].textContent), "Jonas Probe", "neue Stimme gewählt");
    assert((await text("#voice-readiness")).includes("1 extrait disponible"), "neue Referenz sichtbar");

    // Historique: fertige, fehlgeschlagene und abgebrochene Aufträge
    await seite.click('[data-view="history"]');
    await warte(300);
    assert.equal(await anzahl(".history-item"), 5, "fünf Aufträge");
    const verlauf = await text("#history");
    assert(verlauf.includes("Échec") && verlauf.includes("Annulé") && verlauf.includes("Prêt à écouter"), "Zustände im Verlauf");
    await seite.evaluate(() => [...document.querySelectorAll(".history-item")].find((e) => e.textContent.includes("Échec")).querySelector("button").click());
    await warte(500);
    assert((await text("#chatterbox-result")).includes("Mémoire graphique"), "Fehlermeldung des gescheiterten Auftrags");

    // Réglages: Beispielwerte gekennzeichnet, Dokumentationslink ohne Swagger
    await seite.click('[data-view="settings"]');
    await warte(300);
    assert((await text("#resources")).includes("RAM disponible"), "Dienstzustand");
    assert((await text("#engines")).includes("préparation incomplète"), "Motoren ehrlich nicht installiert");
    assert.equal(await anzahl("[data-demo-dienst]"), 1, "Hinweis in den Einstellungen");
    await seite.click("[data-demo-docs]");
    await warte(200);
    assert((await text("[data-demo-hinweis]")).includes("documentation"), "Hinweis statt Swagger");
    assert(seite.url().startsWith(basis), "keine Navigation zu einer fremden Seite");

    // Handy: kein horizontaler Überlauf in keiner Ansicht
    await seite.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });
    for (const ansicht of ["synthesis", "voices", "history", "settings"]) {
      await seite.click(`[data-view="${ansicht}"]`);
      await warte(300);
      const breite = await seite.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
      assert(breite[0] <= breite[1], `kein horizontaler Überlauf (${ansicht}: ${breite[0]} > ${breite[1]})`);
    }
  } finally { await browser.close(); server.close(); }
  assert.deepEqual(fehler, [], "keine Laufzeitfehler");
  assert.deepEqual(extern, [], "keine externen Anfragen");
  console.log(`Browserprüfung der Demo bestanden (${dateien} Dateien ohne private Muster): Start, Testsignale, Dauerschätzung, ehrliche Synthese-Meldung, Bewertung, Stimmen, Vorbereitung, Verlauf, Einstellungen, Handy 390 px; keine Fehler, keine externen Anfragen.`);
})().catch((e) => { console.error(e); process.exitCode = 1; server.close(); });
