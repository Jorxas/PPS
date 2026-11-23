// Baut die öffentliche Demo nach dist/ (Aufruf im Repository-Wurzelverzeichnis: node tools/demo-bauen.mjs).
// Die echte Oberfläche (pps/web.html und pps/static/*) wird unverändert kopiert. Nur die absoluten Pfade der HTML-Datei
// werden relativ gemacht, damit sie unter /PPS/ auf GitHub Pages funktioniert, und demo/demo-start.js wird nach i18n.js und vor
// app.js geladen. Swagger-UI (pps/static/swagger-*) gehört nicht zur Demo.
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const wurzel = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(wurzel, "dist");
const statisch = ["app.css", "i18n.js", "app.js", "media.js", "preparation.js"];

let html = await readFile(path.join(wurzel, "pps", "web.html"), "utf8");
function ersetzen(vorher, nachher) {
  if (!html.includes(vorher)) throw new Error("pps/web.html hat sich geändert, Muster nicht gefunden: " + vorher);
  html = html.replace(vorher, nachher);
}
ersetzen('<link rel="stylesheet" href="/static/app.css" />',
  '<link rel="icon" href="./favicon.svg" type="image/svg+xml" />\n    <link rel="stylesheet" href="./static/app.css" />');
// Die Demo lädt nichts von fremden Servern; blob: gilt nur für die erzeugten Testsignale.
ersetzen('<meta charset="utf-8" />',
  `<meta charset="utf-8" />\n    <meta http-equiv="Content-Security-Policy" content="default-src 'self'; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'" />`);
ersetzen('<script src="/static/i18n.js"></script>', '<script src="./static/i18n.js"></script>');
ersetzen('<script src="/static/app.js"></script>', '<script src="./demo-start.js"></script>\n    <script src="./static/app.js"></script>');
ersetzen('<script src="/static/media.js"></script>', '<script src="./static/media.js"></script>');
ersetzen('<script src="/static/preparation.js"></script>', '<script src="./static/preparation.js"></script>');
if (/(?:href|src)="\/(?!\/)/.test(html.replace(/href="\/docs"/, ""))) throw new Error("Es bleiben absolute Pfade in der HTML-Datei.");

await rm(dist, { recursive: true, force: true });
await mkdir(path.join(dist, "static"), { recursive: true });
await writeFile(path.join(dist, "index.html"), html);
for (const datei of statisch) await cp(path.join(wurzel, "pps", "static", datei), path.join(dist, "static", datei));
await cp(path.join(wurzel, "demo", "demo-start.js"), path.join(dist, "demo-start.js"));
await cp(path.join(wurzel, "demo", "favicon.svg"), path.join(dist, "favicon.svg"));
await writeFile(path.join(dist, ".nojekyll"), "");
console.log("Demo gebaut in dist/: echte PPS-Oberfläche mit erfundenen Fällen, ohne Swagger und ohne Modelle.");
