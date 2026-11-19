// DOM interaction tests. No browser, model loading or production writes.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { JSDOM } = require("../tools/frontend/node_modules/jsdom");
const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "pps/web.html"), "utf8");
const source = (name) =>
  fs.readFileSync(path.join(root, "pps/static", name), "utf8");
const ref = {
  id: "ref-a",
  filename: "extrait.wav",
  transcription: "Bonjour.",
  transcription_verified: true,
  pcm: { duration_seconds: 5.3 },
};
const voice = {
  id: "voice-a",
  name: "Ma voix",
  language: "fr",
  version: 1,
  references: [ref],
  active_reference_id: ref.id,
  engine_reference_ids: {},
};
const job = (engine = "chatterbox") => ({
  id: "job-" + engine,
  status: "completed",
  audio_id: "audio-" + engine,
  created_at: 1700000000,
  request: {
    engine,
    voice_id: voice.id,
    reference_id: ref.id,
    text: "Mon texte de test.",
    options: {},
    language: "fr",
    seed: 1986,
  },
  metrics: {},
});
const candidate = {
  id: "candidate-a",
  start_seconds: 0,
  end_seconds: 5.3,
  metrics: { score: 82, speaker_status: "unknown" },
  source_sha256: "test",
  track_index: 1,
  filename: "source.mp4",
};
const analysis = {
  id: "analysis-a",
  media_id: "media-a",
  voice_id: voice.id,
  status: "completed",
  track_index: 1,
  phase: "finished",
  processed_seconds: 20,
  total_seconds: 20,
  progress: 1,
  candidates: [candidate],
};
const media = {
  id: "media-a",
  voice_id: voice.id,
  status: "ready",
  filename: "source.mp4",
  size_bytes: 1234,
  received_bytes: 1234,
  probe: { duration_seconds: 20, audio_tracks: [{ index: 1, tags: {} }] },
};
const preparation = {
  id: "preparation-a",
  voice_id: voice.id,
  media_ids: [media.id],
  track_indices: { [media.id]: 1 },
  missing_track_media_ids: [],
  analysis_ids: [analysis.id],
  status: "needs_confirmation",
  progress: 1,
  files_completed: 1,
  files_total: 1,
  selection: {
    analysis_id: analysis.id,
    candidate_id: candidate.id,
    filename: media.filename,
    start_seconds: 0,
    end_seconds: 5.3,
    score: 82,
    issues: [],
  },
  transcription: { text: "Bonjour.", language: "fr", verified: false },
  suggestions: [],
  error: null,
};
preparation.suggestions = [preparation.selection];
async function settle() {
  for (let n = 0; n < 8; n++) await new Promise((r) => setImmediate(r));
}
async function until(condition) {
  const end = Date.now() + 3000;
  while (!condition()) {
    if (Date.now() > end) throw Error("Timed out waiting for UI");
    await new Promise((r) => setTimeout(r, 5));
  }
  await settle();
}
async function setup({
  voices = [voice],
  comparisons = [],
  mediaData = { media: [], analyses: [] },
  preparations = [],
  respond,
  hash = "",
} = {}) {
  const dom = new JSDOM(html, {
      url: "http://localhost:8765/" + hash,
      runScripts: "outside-only",
      pretendToBeVisual: true,
    }),
    w = dom.window,
    requests = [];
  w.HTMLElement.prototype.scrollIntoView = function () {};
  w.fetch = async (url, options = {}) => {
    const method = options.method || "GET",
      p = url.replace("/v1", ""),
      body =
        typeof options.body === "string"
          ? JSON.parse(options.body)
          : options.body;
    requests.push({ p, method, body });
    let data;
    if (respond) data = await respond(p, method, body);
    if (data === undefined) {
      if (p === "/health")
        data = {
          resources: {
            ram_available_gib: 10,
            disk_free_gib: 100,
            gpu: { free_gib: 7 },
          },
        };
      else if (p === "/engines")
        data = {
          engines: [],
          resource_thresholds: { min_ram_gib: 6, min_vram_gib: 5 },
        };
      else if (p === "/voices") data = { voices };
      else if (p === "/jobs")
        data = { jobs: comparisons.flatMap((c) => c.jobs) };
      else if (p === "/comparisons") data = { comparisons };
      else if (p === "/reference-trials") data = { trials: [] };
      else if (p === "/media") data = mediaData;
      else if (p === "/voice-preparations") data = { preparations };
      else if (p.match(/^\/voices\/[^/]+$/))
        data = voices.find((v) => p.endsWith(v.id));
      else if (p.endsWith("/alignment")) data = { words: [] };
      else if (p.endsWith("/metadata"))
        data = {
          id: p.split("/")[2],
          voice_id: voice.id,
          reference_id: ref.id,
          text: "Mon texte de test.",
          pcm: { duration_seconds: 5, sha256: "audio-hash" },
          metrics: {},
        };
      else if (p.endsWith("/review"))
        data = {
          effective_alignment: {
            words: [],
            status: "asr_unverified",
            mainclip_compatible: false,
          },
          alignment_revision: 1,
        };
    }
    if (data === undefined)
      throw new Error("Unexpected request " + method + " " + p);
    return { ok: true, json: async () => structuredClone(data) };
  };
  try {
    for (const name of ["app.js", "media.js", "preparation.js"])
      vm.runInContext(source(name), dom.getInternalVMContext(), {
        filename: name,
      });
    await settle();
  } catch (e) {
    w.close();
    throw e;
  }
  assert.equal(
    w.document.querySelectorAll(".error").length,
    0,
    "No startup errors",
  );
  return {
    w,
    d: w.document,
    requests,
    close: async () => {
      await settle();
      w.close();
    },
  };
}
test("Studio opens on synthesis, navigation keeps text and supports deep links", async () => {
  const app = await setup();
  try {
    const { w, d } = app;
    assert.equal(d.getElementById("view-synthesis").hidden, false);
    assert.equal(d.getElementById("view-voices").hidden, true);
    const text = d.getElementById("text");
    text.value = "Bonjour";
    text.dispatchEvent(new w.Event("input"));
    assert.match(d.getElementById("text-count").textContent, /7 \/ 50 000/);
    d.querySelector('[data-view="voices"]').click();
    assert.equal(d.getElementById("page-title").textContent, "Mes voix");
    assert.equal(w.location.hash, "#voices");
    d.querySelector('[data-view="synthesis"]').click();
    assert.equal(text.value, "Bonjour");
    d.querySelector("[data-add-voice]").click();
    assert.equal(d.getElementById("voice-import").open, true);
    assert.equal(d.activeElement.id, "media-name");
  } finally {
    await app.close();
  }
  const linked = await setup({ hash: "#history" });
  try {
    assert.equal(linked.d.getElementById("view-history").hidden, false);
  } finally {
    await linked.close();
  }
});
test("No voices or no saved excerpt gives an actionable empty state", async () => {
  for (const voices of [
    [],
    [{ ...voice, references: [], active_reference_id: null }],
  ]) {
    const app = await setup({ voices });
    try {
      assert.equal(app.d.getElementById("generate").disabled, true);
      assert.equal(app.d.getElementById("compare").disabled, true);
      assert.match(
        app.d.getElementById("voice-readiness").textContent,
        /Ajoutez|Préparez cette voix/,
      );
    } finally {
      await app.close();
    }
  }
});
test("Chatterbox and CosyVoice select real engines, transcription gate remains enforced", async () => {
  const app = await setup({
    voices: [
      { ...voice, references: [{ ...ref, transcription_verified: false }] },
    ],
  });
  try {
    const { d, w } = app;
    assert.equal(d.getElementById("generate").disabled, false);
    assert.equal(d.getElementById("compare").disabled, true);
    const cosy = d.querySelector('[name="engine-choice"][value="cosyvoice"]');
    cosy.checked = true;
    cosy.dispatchEvent(new w.Event("change"));
    assert.equal(d.getElementById("engine").value, "cosyvoice");
    assert.equal(d.getElementById("generate").disabled, true);
    assert.match(
      d.getElementById("voice-readiness").textContent,
      /mots exacts/,
    );
    w.selectEngine("chatterbox");
    assert.equal(
      d.querySelector('[name="engine-choice"][value="chatterbox"]').checked,
      true,
    );
  } finally {
    await app.close();
  }
});
test("Previous comparison restores playable WAVs, downloads and collapsed reviews", async () => {
  const app = await setup({
    comparisons: [{ status: "completed", jobs: [job(), job("cosyvoice")] }],
  });
  try {
    for (const engine of ["chatterbox", "cosyvoice"]) {
      const card = app.d.getElementById(engine + "-result");
      assert.match(
        card.querySelector("audio").src,
        new RegExp("/v1/audio/audio-" + engine),
      );
      assert.equal(card.querySelectorAll("a.link").length, 2);
      assert.equal(card.querySelector(".technical-details").open, false);
      assert.equal(card.querySelector("details").open, false);
      assert.equal(card.querySelector(".badge").textContent, "Prêt à écouter");
    }
    app.w.navigate("history");
    app.d.querySelector("#history button").click();
    await settle();
    assert.equal(app.d.getElementById("view-synthesis").hidden, false);
  } finally {
    await app.close();
  }
});
test("Generate submits selected engine and reference; comparison preserves per-engine references", async () => {
  const app = await setup({
    respond: (p, method) => {
      if (p === "/syntheses" && method === "POST")
        return { job_id: "job-cosyvoice" };
      if (p === "/comparisons" && method === "POST")
        return { job_ids: ["job-chatterbox", "job-cosyvoice"] };
      if (p === "/jobs/job-cosyvoice") return job("cosyvoice");
      if (p === "/jobs/job-chatterbox") return job();
    },
  });
  try {
    const { w, d, requests } = app;
    d.getElementById("text").value = "Le nouveau texte";
    w.selectEngine("cosyvoice");
    d.getElementById("generate").click();
    await settle();
    const single = requests.find(
      (r) => r.p === "/syntheses" && r.method === "POST",
    );
    assert.equal(single.body.engine, "cosyvoice");
    assert.equal(single.body.reference_id, ref.id);
    assert.equal(single.body.text, "Le nouveau texte");
    assert.equal(d.getElementById("generate").disabled, false);
    d.getElementById("compare").click();
    await settle();
    const compare = requests.find(
      (r) => r.p === "/comparisons" && r.method === "POST",
    );
    assert.deepEqual(compare.body.engine_reference_ids, {
      chatterbox: ref.id,
      cosyvoice: ref.id,
    });
    assert.equal(d.getElementById("cancel").hidden, true);
  } finally {
    await app.close();
  }
});
test("Source import requires consent, records provenance and automatically prepares the voice", async () => {
  const app = await setup({
    respond: (p, method) =>
      p === "/media/uploads" && method === "POST"
        ? { id: "new-media", voice_id: voice.id, status: "ready" }
        : p === "/voice-preparations" && method === "POST"
          ? { ...preparation, status: "analyzing" }
          : undefined,
  });
  try {
    const { w, d, requests } = app;
    const fakeFile = {
      name: "mon-audio.wav",
      size: 20,
      lastModified: 1,
      slice: () => ({ arrayBuffer: async () => new ArrayBuffer(20) }),
    };
    Object.defineProperty(d.getElementById("media-files"), "files", {
      value: [fakeFile],
    });
    const form = d.getElementById("media-import-form");
    form.dispatchEvent(new w.Event("submit", { cancelable: true }));
    await settle();
    assert.equal(requests.filter((r) => r.method === "POST").length, 0);
    d.getElementById("media-origin").value = "permission";
    d.getElementById("media-authorized").checked = true;
    d.getElementById("media-name").value = "Narratrice";
    d.getElementById("media-language").value = "en";
    d.getElementById("media-source").value =
      "Entretien enregistré avec son accord.";
    w.TextEncoder = TextEncoder;
    Object.defineProperty(w.crypto, "subtle", {
      value: require("node:crypto").webcrypto.subtle,
    });
    form.dispatchEvent(new w.Event("submit", { cancelable: true }));
    await settle();
    await until(
      () =>
        requests.some((r) => r.p === "/media/uploads" && r.method === "POST") &&
        !d.getElementById("media-upload").disabled,
    );
    const upload = requests.find(
      (r) => r.p === "/media/uploads" && r.method === "POST",
    );
    assert.equal(upload.body.authorized, true);
    assert.equal(upload.body.language, "en");
    assert.match(upload.body.declaration, /obtenu l’accord/);
    assert.match(upload.body.declaration, /Entretien enregistré/);
    assert.doesNotMatch(upload.body.declaration, /Ma propre voix/);
    assert.equal(
      requests.filter(
        (r) => r.p === "/voice-preparations" && r.method === "POST",
      ).length,
      1,
    );
    assert.deepEqual(
      requests.find((r) => r.p === "/voice-preparations" && r.method === "POST")
        .body.media_ids,
      ["new-media"],
    );
    assert.equal(
      requests.filter(
        (r) =>
          r.method === "POST" &&
          (r.p.endsWith("/analyses") || r.p.endsWith("/transcribe")),
      ).length,
      0,
    );
  } finally {
    await app.close();
  }
});
test("Only latest analysis is foregrounded; excerpt verification remains explicit", async () => {
  const app = await setup({
    mediaData: {
      media: [media],
      analyses: [{ ...analysis, id: "analysis-old" }, analysis],
    },
  });
  try {
    const { d, w, requests } = app;
    w.navigate("voices");
    const section = d.querySelector(".media-file");
    assert.equal(
      section.querySelectorAll(":scope > .analysis-block").length,
      1,
    );
    assert.equal(section.querySelector(":scope > select").hidden, true);
    const card = section.querySelector(
        ":scope > .analysis-block .candidate-card",
      ),
      setup = card.querySelector(".candidate-setup");
    assert.equal(setup.open, false);
    assert.match(
      setup.querySelector("summary").textContent,
      /Choisir cet extrait/,
    );
    [...setup.querySelectorAll("button")]
      .find((b) => b.textContent === "Enregistrer cet extrait")
      .click();
    await settle();
    assert.match(setup.textContent, /confirmez/);
    assert.equal(
      requests.filter((r) => r.p.endsWith("/reference") && r.method === "POST")
        .length,
      0,
    );
  } finally {
    await app.close();
  }
});
test("Existing voice import needs no new name; files filter by voice", async () => {
  const other = { ...voice, id: "voice-b", name: "Autre voix" };
  const app = await setup({
    voices: [voice, other],
    mediaData: {
      media: [media, { ...media, id: "media-b", voice_id: other.id }],
      analyses: [],
    },
  });
  try {
    const { d, w } = app;
    w.addVoice(voice.id);
    assert.equal(d.getElementById("media-name").disabled, true);
    assert.equal(d.getElementById("media-name").required, false);
    d.getElementById("media-filter").value = voice.id;
    d.getElementById("media-filter").dispatchEvent(new w.Event("change"));
    assert.equal(d.querySelector('[data-media="media-a"]').hidden, false);
    assert.equal(d.querySelector('[data-media="media-b"]').hidden, true);
    d.getElementById("media-filter").value = "";
    d.getElementById("media-filter").dispatchEvent(new w.Event("change"));
    assert.equal(d.querySelector('[data-media="media-b"]').hidden, false);
    w.addVoice();
    assert.equal(d.getElementById("media-name").required, true);
    assert.equal(d.getElementById("media-name").disabled, false);
  } finally {
    await app.close();
  }
});
test("Automatically prepared reference needs just one explicit confirmation and keeps corrections during polling", async () => {
  const p = structuredClone(preparation);
  const app = await setup({
    preparations: [p],
    respond: (url, method) => {
      if (
        url === "/voice-preparations/" + p.id + "/confirm" &&
        method === "POST"
      ) {
        p.status = "ready";
        return voice;
      }
    },
  });
  try {
    const { d, w, requests } = app;
    w.navigate("voices");
    const card = d.querySelector(".preparation-card"),
      words = card.querySelector("textarea");
    assert.equal(card.querySelectorAll("input[type=checkbox]").length, 0);
    assert.equal(card.querySelectorAll(":scope > audio").length, 1);
    words.value = "Les mots corrigés.";
    d.dispatchEvent(new w.Event("pps-preparation-refresh"));
    await settle();
    assert.equal(card.querySelector("textarea"), words);
    assert.equal(words.value, "Les mots corrigés.");
    const confirm = [...card.querySelectorAll("button")].find(
      (b) => b.textContent === "Confirmer cette voix et ces mots",
    );
    confirm.click();
    await settle();
    const sent = requests.find(
      (r) => r.p.endsWith("/confirm") && r.method === "POST",
    );
    assert.equal(sent.body.transcription, "Les mots corrigés.");
    assert.equal(sent.body.speaker_confirmed, true);
    assert.equal(sent.body.transcription_verified, true);
    assert.equal(d.getElementById("view-synthesis").hidden, false);
    assert.equal(d.getElementById("voice").value, voice.id);
    assert.match(d.getElementById("message").textContent, /tous vos textes/);
    assert.equal(
      requests.filter((r) => r.p === "/syntheses" && r.method === "POST")
        .length,
      0,
    );
  } finally {
    await app.close();
  }
});
test("Voice preparation resumes from saved state after page reload; multiple tracks require an explicit choice", async () => {
  const p = {
    ...structuredClone(preparation),
    status: "needs_track",
    analysis_ids: [],
    selection: null,
    transcription: null,
    missing_track_media_ids: [media.id],
    track_indices: {},
  };
  const app = await setup({
    preparations: [p],
    mediaData: {
      media: [
        {
          ...media,
          probe: {
            ...media.probe,
            audio_tracks: [
              { index: 0, tags: {} },
              { index: 1, tags: { language: "fr" } },
            ],
          },
        },
      ],
      analyses: [],
    },
    respond: (url, method) =>
      url === "/voice-preparations" && method === "POST"
        ? { ...p, status: "analyzing" }
        : undefined,
  });
  try {
    const { d, w, requests } = app;
    w.navigate("voices");
    const card = d.querySelector(".preparation-card");
    assert.ok(card);
    assert.equal(card.querySelector("select").value, "");
    const continueButton = [...card.querySelectorAll("button")].find(
      (b) => b.textContent === "Continuer la préparation",
    );
    continueButton.click();
    await settle();
    assert.equal(requests.filter((r) => r.method === "POST").length, 0);
    card.querySelector("select").value = "0";
    continueButton.click();
    await settle();
    const sent = requests.find(
      (r) => r.p === "/voice-preparations" && r.method === "POST",
    );
    assert.deepEqual(sent.body.track_indices, { [media.id]: 0 });
  } finally {
    await app.close();
  }
});
