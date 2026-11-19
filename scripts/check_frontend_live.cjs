// Read-only integration with PPS. Never submit an upload, review or generation.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const {
  JSDOM,
  VirtualConsole,
} = require("../tools/frontend/node_modules/jsdom");
const base = "http://127.0.0.1:8765";
const root = path.resolve(__dirname, "..");
(async () => {
  const healthBefore = await (await fetch(base + "/v1/health")).json();
  const files = await Promise.all(
    [
      "/",
      "/static/app.css",
      "/static/app.js",
      "/static/media.js",
      "/static/preparation.js",
    ].map(async (url) => {
      const r = await fetch(base + url);
      assert.equal(r.status, 200, url);
      return r.text();
    }),
  );
  const errors = [],
    requests = [];
  let pending = 0;
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (e) => errors.push(e.message));
  const dom = new JSDOM(files[0], {
      url: base + "/",
      runScripts: "outside-only",
      pretendToBeVisual: true,
      virtualConsole,
    }),
    w = dom.window;
  w.HTMLElement.prototype.scrollIntoView = function () {};
  w.fetch = async (url, options = {}) => {
    assert.equal(
      options.method || "GET",
      "GET",
      "Production check must remain read-only",
    );
    requests.push(url);
    pending++;
    try {
      const r = await fetch(new URL(url, base), {
        signal: AbortSignal.timeout(10000),
      });
      const data = await r.json();
      return { ok: r.ok, json: async () => data };
    } finally {
      pending--;
    }
  };
  try {
    const style = w.document.createElement("style");
    style.textContent = files[1];
    w.document.head.append(style);
    for (const [i, name] of [
      [2, "app.js"],
      [3, "media.js"],
      [4, "preparation.js"],
    ])
      vm.runInContext(files[i], dom.getInternalVMContext(), { filename: name });
    const end = Date.now() + 20000;
    let idle = 0;
    while (idle < 8) {
      assert.ok(Date.now() < end, "Frontend startup timeout");
      await new Promise((r) => setTimeout(r, 40));
      idle = pending === 0 ? idle + 1 : 0;
    }
    const d = w.document;
    assert.deepEqual(errors, []);
    assert.equal(
      d.querySelectorAll(".error").length,
      0,
      [...d.querySelectorAll(".error")].map((x) => x.textContent).join("\n"),
    );
    const ids = [...d.querySelectorAll("[id]")].map((x) => x.id);
    assert.equal(new Set(ids).size, ids.length, "Unique DOM IDs");
    assert.equal(d.getElementById("view-synthesis").hidden, false);
    assert.equal(d.getElementById("view-voices").hidden, true);
    assert.ok(d.getElementById("voice").options.length > 0);
    assert.ok(d.querySelectorAll(".voice-card").length > 0);
    const players = ["chatterbox", "cosyvoice"].map((k) =>
      d.getElementById(k + "-result").querySelector("audio"),
    );
    assert.ok(
      players.every(Boolean),
      "Existing comparison restores both audio players",
    );
    const reference = d
      .getElementById("reference-result")
      .querySelector("audio");
    assert.ok(reference, "Original reference player");
    // Fetch only the small byte range needed to check the WAV headers.
    for (const player of [...players, reference]) {
      const r = await fetch(player.src, { headers: { Range: "bytes=0-43" } });
      assert.ok([200, 206].includes(r.status));
      const reader = r.body.getReader(),
        first = await reader.read();
      await reader.cancel();
      assert.equal(Buffer.from(first.value).subarray(0, 4).toString(), "RIFF");
    }
    w.navigate("voices");
    assert.equal(d.getElementById("view-voices").hidden, false);
    assert.ok(
      d.querySelector(".candidate-setup"),
      "Saved media analysis remains accessible",
    );
    w.navigate("synthesis");
    const healthAfter = await (await fetch(base + "/v1/health")).json();
    const report = {
      checked_at: new Date().toISOString(),
      mode: "read-only DOM and local HTTP, no rendered browser",
      api_health: healthAfter.status,
      engine_before: healthBefore.active_engine,
      engine_after: healthAfter.active_engine,
      voices: d.querySelectorAll(".voice-card").length,
      source_files: d.querySelectorAll(".media-file").length,
      result_audio_urls: players.map((p) => p.src),
      reference_url: reference.src,
      css_rules: style.sheet.cssRules.length,
      requests,
      errors,
    };
    fs.writeFileSync(
      path.join(root, "reports/frontend-live-check.json"),
      JSON.stringify(report, null, 2) + "\n",
    );
    console.log(
      JSON.stringify({
        health: report.api_health,
        voices: report.voices,
        sources: report.source_files,
        audio_players: 3,
        errors: errors.length,
        production_mutations: 0,
      }),
    );
  } finally {
    w.close();
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
