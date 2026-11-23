const $ = (id) => document.getElementById(id),
  names = { chatterbox: "Chatterbox", cosyvoice: "CosyVoice" };
let active = [],
  timer = null,
  generating = false,
  selectedVoice = null,
  referenceRequest = 0;
const statusKeys = [
  "queued",
  "loading",
  "running",
  "synthesizing",
  "exporting",
  "aligning",
  "completed",
  "failed",
  "cancelled",
  "paused",
];
function statusLabel(value) {
  return tx("st." + (statusKeys.includes(value) ? value : "running"));
}
const pageViews = ["synthesis", "voices", "history", "settings"];
let currentView = "synthesis";
function navigate(view, focus = true) {
  if (!pageViews.includes(view)) view = "synthesis";
  currentView = view;
  for (const section of document.querySelectorAll(".view"))
    section.hidden = section.id !== "view-" + view;
  for (const item of document.querySelectorAll("[data-view]")) {
    const on = item.dataset.view === view;
    item.classList.toggle("selected", on);
    if (on) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  }
  $("page-title").textContent = tx("page." + view + ".t");
  $("page-description").textContent = tx("page." + view + ".d");
  document.title = "PPS · " + tx("page." + view + ".t");
  if (window.location.hash !== "#" + view)
    window.history.replaceState(null, "", "#" + view);
  if (focus) $("page-title").focus();
}
function addVoice(voiceId = "") {
  navigate("voices");
  $("voice-import").open = true;
  $("media-voice").value = voiceId;
  $("media-name").disabled = !!voiceId;
  $("media-name").required = !voiceId;
  (voiceId ? $("media-files") : $("media-name")).focus();
  $("voice-import").scrollIntoView({ behavior: "smooth", block: "start" });
}
function selectEngine(engine) {
  $("engine").value = engine;
  for (const radio of document.querySelectorAll('[name="engine-choice"]'))
    radio.checked = radio.value === engine;
  updateReadiness();
  previewSelectedReference();
}
function currentReference(engine) {
  return selectedVoice?.references.find(
    (r) =>
      r.id ===
      ($(engine + "-reference").value ||
        selectedVoice.engine_reference_ids?.[engine] ||
        selectedVoice.active_reference_id),
  );
}
function updateReadiness() {
  const reference = currentReference($("engine").value),
    cosy = currentReference("cosyvoice");
  const usable =
    !!reference &&
    ($("engine").value !== "cosyvoice" ||
      (reference.transcription?.trim() &&
        reference.transcription_verified !== false));
  $("generate").disabled = generating || !usable;
  $("compare").disabled =
    generating ||
    !currentReference("chatterbox") ||
    !cosy?.transcription?.trim() ||
    cosy.transcription_verified === false;
  $("voice-readiness").textContent = !selectedVoice
    ? tx("ready.none")
    : !reference
      ? tx("ready.prepare")
      : !usable
        ? tx("ready.cosy")
        : tx("ready.refs_" + plural(selectedVoice.references.length), {
            n: selectedVoice.references.length,
          });
}
function setVoiceState(voice) {
  selectedVoice = voice;
  updateReadiness();
  previewSelectedReference();
}
function previewSelectedReference() {
  const r = currentReference($("engine").value);
  if (selectedVoice && r)
    showReference(selectedVoice.id, r.id).catch((e) => report("message", e));
  else {
    referenceRequest++;
    $("reference-result").dataset.reference = "";
    $("reference-result").replaceChildren(
      elem("p", tx("ref.add"), "muted"),
    );
  }
}
document
  .querySelectorAll("[data-view]")
  .forEach((b) => (b.onclick = () => navigate(b.dataset.view)));
document
  .querySelectorAll("[data-add-voice]")
  .forEach((b) => (b.onclick = () => addVoice()));
document
  .querySelectorAll('[name="engine-choice"]')
  .forEach((r) => (r.onchange = () => selectEngine(r.value)));
let durationTimer = null;
function updateDurationEstimate() {
  const txt = $("text").value;
  const words = txt.trim() ? txt.trim().split(/\s+/).length : 0;
  $("text-count").textContent = tx("text.count_" + plural(words), {
    chars: numl($("text").value.length, 0),
    max: numl(50000, 0),
    words: numl(words, 0),
  });

  if (!txt.trim()) {
    $("duration-estimate").textContent = tx("dur.zero", { v: fixed(0, 1) });
    if ($("duration-alert")) $("duration-alert").style.display = "none";
    return;
  }

  clearTimeout(durationTimer);
  durationTimer = setTimeout(async () => {
    try {
      const targetSec = parseFloat($("target-duration")?.value) || null;
      const res = await api("/duration-estimate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: txt,
          language: $("language")?.value || getLang(),
          engine: $("engine")?.value || "chatterbox",
          target_duration_seconds: targetSec,
        }),
      });

      $("duration-estimate").textContent = tx("dur.est", {
        est: numl(res.estimated_duration_seconds),
        min: numl(res.estimated_duration_min_seconds),
        max: numl(res.estimated_duration_max_seconds),
        wpm: numl(res.effective_wpm),
      });

      const alertBox = $("duration-alert");
      if (alertBox) {
        if (res.duration_status === "too_short" || res.duration_status === "too_long") {
          alertBox.style.display = "block";
          alertBox.className = "status warning";
          alertBox.textContent = res.advice?.message || tx("dur.gap");
        } else if (res.duration_status === "compatible") {
          alertBox.style.display = "block";
          alertBox.className = "status success";
          alertBox.textContent = res.advice?.message || tx("dur.ok");
        } else {
          alertBox.style.display = "none";
        }
      }
    } catch (_) {}
  }, 300);
}

$("text").addEventListener("input", updateDurationEstimate);
if ($("target-duration")) $("target-duration").addEventListener("input", updateDurationEstimate);
if ($("language")) $("language").addEventListener("change", updateDurationEstimate);
window.addEventListener("hashchange", () =>
  navigate(window.location.hash.slice(1)),
);
navigate(window.location.hash.slice(1), false);
function elem(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
async function api(path, options = {}) {
  const r = await fetch("/v1" + path, options);
  const d = await r.json();
  if (!r.ok)
    throw Error(
      tx("err.fmt", {
        code: d.error?.code || r.status,
        msg: d.error?.message || JSON.stringify(d),
      }),
    );
  return d;
}
function report(id, error) {
  $(id).replaceChildren(elem("div", error.message, "error"));
}
function opts() {
  return {
    chatterbox: {
      exaggeration: Number($("exaggeration").value),
      cfg_weight: Number($("cfg").value),
    },
    cosyvoice: { speed: Number($("speed").value) },
  };
}
async function refresh() {
  try {
    const [health, engines, voices] = await Promise.all([
      api("/health"),
      api("/engines"),
      api("/voices"),
    ]);
    const s = health.resources;
    const unit = tx("unit.gib");
    $("resources").textContent = tx("res.line", {
      ram: fixed(s.ram_available_gib, 2),
      vram: s.gpu ? fixed(s.gpu.free_gib, 2) + " " + unit : tx("na"),
      disk: fixed(s.disk_free_gib, 1),
      u: unit,
    });
    $("resources").append(
      elem(
        "p",
        tx("res.min", {
          ram: numl(engines.resource_thresholds.min_ram_gib),
          vram: numl(engines.resource_thresholds.min_vram_gib),
          u: unit,
        }),
      ),
    );
    $("engines").replaceChildren(
      ...engines.engines.map((e) =>
        elem(
          "div",
          tx("eng.line", {
            name: e.name,
            state: tx(e.ready_to_attempt_load ? "eng.ready" : "eng.incomplete"),
          }),
        ),
      ),
    );
    const previous = $("voice").value;
    $("voice").replaceChildren(
      ...(voices.voices.length
        ? voices.voices.map((v) => {
            const o = elem("option", v.name);
            o.value = v.id;
            return o;
          })
        : [elem("option", tx("voice.none"))]),
    );
    if (!voices.voices.length) $("voice").firstElementChild.value = "";
    if (voices.voices.some((v) => v.id === previous))
      $("voice").value = previous;
    document.dispatchEvent(new Event("pps-voices"));
    await loadHistory();
    await restoreComparison();
  } catch (e) {
    report("resources", e);
    report(
      "message",
      Error(tx("svc.down")),
    );
  }
}
$("import-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = e.target.querySelector("button");
  button.disabled = true;
  $("import-message").textContent = tx("imp.preparing");
  try {
    const v = await api("/voices", {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: new FormData(e.target),
    });
    $("import-message").textContent = tx("imp.saved", { name: v.name });
    await refresh();
    $("voice").value = v.id;
    $("voice").dispatchEvent(new Event("change"));
    navigate("synthesis");
  } catch (e) {
    report("import-message", e);
  } finally {
    button.disabled = false;
  }
});
async function submit(comparison) {
  if (!$("voice").value || !$("text").value.trim()) {
    report(
      "message",
      Error(tx("sub.need")),
    );
    return;
  }
  const targetSec = parseFloat($("target-duration")?.value) || null;
  const alignMode = $("alignment-mode")?.value || "full";
  const o = opts(),
    body = {
      voice_id: $("voice").value,
      text: $("text").value,
      language: $("language").value,
      seed: 1986,
      target_duration_seconds: targetSec,
      alignment_mode: alignMode,
    };
  if (comparison) {
    body.engine_options = o;
    body.engine_reference_ids = Object.fromEntries(
      Object.keys(names)
        .filter((k) => $(k + "-reference").value)
        .map((k) => [k, $(k + "-reference").value]),
    );
  } else
    Object.assign(body, {
      reference_id: $($("engine").value + "-reference").value || null,
      engine: $("engine").value,
      options: o[$("engine").value],
    });
  busy(true);
  try {
    const d = await api(comparison ? "/comparisons" : "/syntheses", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify(body),
    });
    active = d.job_ids || [d.job_id];
    if (comparison)
      for (const k of Object.keys(names))
        $("" + k + "-result").replaceChildren(
          elem("h3", names[k]),
          elem("span", statusLabel("queued"), "badge"),
        );
    $("message").textContent = tx("sub.preparing");
    await poll();
  } catch (e) {
    report("message", e);
    busy(false);
  }
}
function busy(on) {
  generating = on;
  $("cancel").hidden = !on;
  $("generate").textContent = on ? tx("generating") : tx("generate") + " ↗";
  updateReadiness();
}
async function showReference(voiceId, referenceId) {
  const request = ++referenceRequest;
  const voice = await api("/voices/" + voiceId),
    r = voice.references.find(
      (x) => x.id === (referenceId || voice.active_reference_id),
    );
  if (request !== referenceRequest) return;
  if (!r) return;
  const c = $("reference-result"),
    signature = r.id + ":" + voice.version;
  if (c.dataset.reference === signature) return;
  c.dataset.reference = "";
  const player = elem("audio");
  player.controls = true;
  player.preload = "metadata";
  player.src = "/v1/voices/" + voiceId + "/references/" + r.id + "/audio";
  c.replaceChildren(
    elem("h3", voice.name),
    player,
    elem("p", r.transcription),
    elem("p", tx("sec.n", { v: fixed(r.pcm.duration_seconds, 1) }), "metrics"),
  );
  const edit = elem("details");
  edit.append(
    elem("summary", tx("tr.sum")),
  );
  const transcript = elem("textarea");
  transcript.value = r.transcription;
  transcript.maxLength = 5000;
  transcript.setAttribute("aria-label", tx("tr.aria"));
  const verified = elem("input");
  verified.type = "checkbox";
  verified.checked = r.transcription_verified !== false && !!r.transcription;
  const label = elem("label");
  label.append(
    verified,
    document.createTextNode(tx("tr.verified")),
  );
  const save = elem("button", tx("tr.save")),
    message = elem("p", "", "status");
  save.type = "button";
  save.onclick = async () => {
    save.disabled = true;
    try {
      await api(
        "/voices/" + voiceId + "/references/" + r.id + "/transcription",
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            transcription: transcript.value,
            verified: verified.checked,
          }),
        },
      );
      c.dataset.reference = "";
      await showReference(voiceId, r.id);
      document.dispatchEvent(new Event("pps-voices"));
    } catch (error) {
      message.textContent = error.message;
      message.className = "error";
    } finally {
      save.disabled = false;
    }
  };
  edit.append(transcript, label, save, message);
  c.append(edit);
  const advanced = elem("details");
  advanced.append(elem("summary", tx("ref.details")));
  advanced.append(
    elem("p", tx("ref.id", { id: r.id, v: voice.version }), "metrics"),
  );
  const alignment = await api(
    "/voices/" + voiceId + "/references/" + r.id + "/alignment",
  );
  if (request !== referenceRequest) return;
  if (alignment.words.length) wordPlayer(advanced, player, alignment);
  if (r.media_metrics) {
    const m = r.media_metrics;
    advanced.append(
      elem(
        "p",
        tx("ref.score", {
          score: fixed(m.score, 1),
          cons:
            m.consistency_with_saved_voice_cosine == null
              ? tx("ref.cons.none")
              : tx("ref.cons.val", {
                  v: fixed(m.consistency_with_saved_voice_cosine, 3),
                }),
        }),
        "metrics",
      ),
    );
  }
  c.append(advanced);
  c.dataset.reference = signature;
}
function wordPlayer(container, player, alignment, edit = false) {
  const words = alignment.words || [],
    strip = elem("div");
  strip.className = "word-strip";
  const buttons = words.map((w, i) => {
    const b = elem("button", w.text || w.word, "secondary word");
    b.type = "button";
    b.title = `${fixed(w.start, 3)}–${fixed(w.end, 3)} s · ${w.method || tx("w.unknown")} · ${(w.uncertainty || []).join(", ")}`;
    b.setAttribute(
      "aria-label",
      tx("w.aria", {
        w: w.text || w.word,
        s: fixed(w.start, 3),
        e: fixed(w.end, 3),
      }),
    );
    b.onclick = () => {
      player.currentTime = w.start;
      player.play().catch(() => {});
    };
    strip.append(b);
    return b;
  });
  const clock = elem("p", fixed(0, 3) + " s", "metrics");
  container.append(strip, clock);
  player.addEventListener("timeupdate", () => {
    clock.textContent = fixed(player.currentTime, 3) + " s";
    words.forEach((w, i) => {
      const on = player.currentTime >= w.start && player.currentTime < w.end;
      buttons[i].classList.toggle("speaking", on);
      buttons[i].setAttribute("aria-current", on ? "true" : "false");
    });
  });
  const edits = elem("details");
  edits.append(
    elem("summary", tx("w.sum")),
  );
  const table = elem("table"),
    head = elem("tr");
  for (const title of [
    tx("w.h.word"),
    tx("w.h.auto"),
    tx("w.h.eff"),
    tx("w.h.src"),
  ])
    head.append(elem("th", title));
  table.append(head);
  const inputs = [];
  words.forEach((w, i) => {
    const tr = elem("tr"),
      automatic = w.automatic || w,
      cell = elem("td");
    tr.append(
      elem("td", w.text || w.word),
      elem("td", `${fixed(automatic.start, 3)} → ${fixed(automatic.end, 3)}`),
    );
    const start = elem("input"),
      end = elem("input");
    for (const [input, value, label] of [
      [start, w.start, tx("w.start")],
      [end, w.end, tx("w.end")],
    ]) {
      input.type = "number";
      input.min = "0";
      input.max = String(player.duration || 999);
      input.step = ".001";
      input.value = value.toFixed(3);
      input.disabled = !edit;
      input.setAttribute(
        "aria-label",
        tx("w.of", { l: label, w: w.text || w.word }),
      );
      cell.append(input);
    }
    tr.append(cell);
    tr.append(
      elem(
        "td",
        `${w.method || alignment.timestamp_provenance} · ${w.confidence_status || tx("w.uncertain")} · ${(w.uncertainty || []).join(", ")}`,
      ),
    );
    table.append(tr);
    inputs.push({ index: i, start, end, automatic });
  });
  edits.append(table);
  container.append(edits);
  return inputs;
}
async function reviewPanel(c, player, m, currentJob) {
  const r = await api("/audio/" + m.id + "/review"),
    alignment = r.effective_alignment;
  c.append(
    elem(
      "p",
      tx("rv.align", {
        status: alignment.status,
        mc: tx(alignment.mainclip_compatible ? "rv.mc.ok" : "rv.mc.blocked"),
      }),
      "metrics",
    ),
  );
  const inputs = wordPlayer(c, player, alignment, true),
    form = elem("form");
  form.className = "review-form";
  form.append(elem("h4", tx("rv.title")));
  const fields = {
      fidelity: tx("rv.f.fidelity"),
      naturalness: tx("rv.f.naturalness"),
      pronunciation: tx("rv.f.pronunciation"),
      expressiveness: tx("rv.f.expressiveness"),
      artifacts: tx("rv.f.artifacts"),
      audio_quality: tx("rv.f.audio_quality"),
    },
    selectors = {};
  for (const [key, label] of Object.entries(fields)) {
    const id = m.id + "-" + key,
      l = elem("label", label);
    l.htmlFor = id;
    const select = elem("select");
    select.id = id;
    const empty = elem("option", tx("rv.none"));
    empty.value = "";
    select.append(empty);
    for (let v = 1; v <= 5; v++) {
      const option = elem("option", v + " / 5");
      option.value = v;
      select.append(option);
    }
    select.value = r.latest?.ratings?.[key] ?? "";
    selectors[key] = select;
    l.append(select);
    form.append(l);
  }
  const notes = elem("textarea");
  notes.placeholder = tx("rv.notes.ph");
  notes.setAttribute("aria-label", tx("rv.notes.aria"));
  notes.maxLength = 5000;
  notes.value = r.latest?.notes || "";
  const confirmed = elem("input");
  confirmed.type = "checkbox";
  confirmed.checked = r.latest?.acoustically_confirmed || false;
  const confirmation = elem("label");
  confirmation.className = "consent";
  confirmation.append(
    confirmed,
    document.createTextNode(tx("rv.confirm")),
  );
  const save = elem("button", tx("rv.save")),
    status = elem("p", "", "status");
  save.type = "submit";
  form.append(notes, confirmation, save, status);
  c.append(form);
  form.onsubmit = async (e) => {
    e.preventDefault();
    save.disabled = true;
    try {
      const corrections = inputs
        .filter(
          (x) =>
            Math.abs(Number(x.start.value) - x.automatic.start) > 0.0005 ||
            Math.abs(Number(x.end.value) - x.automatic.end) > 0.0005,
        )
        .map((x) => ({
          word_index: x.index,
          start: Number(x.start.value),
          end: Number(x.end.value),
        }));
      await api("/audio/" + m.id + "/review", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          audio_sha256: m.pcm.sha256,
          alignment_revision: r.alignment_revision,
          ratings: Object.fromEntries(
            Object.entries(selectors).map(([k, v]) => [
              k,
              v.value ? Number(v.value) : null,
            ]),
          ),
          corrections,
          acoustically_confirmed: confirmed.checked,
          notes: notes.value,
        }),
      });
      c.dataset.jobSignature = "";
      const job = await api("/jobs/" + currentJob.id);
      await showJob(job);
    } catch (error) {
      status.textContent = error.message;
      status.className = "error";
    } finally {
      save.disabled = false;
    }
  };
}
async function showJob(j) {
  const c = $(j.request.engine + "-result"),
    signature = j.id + ":" + j.status + ":" + j.audio_id;
  if (c.dataset.jobSignature === signature) return;
  c.dataset.jobSignature = signature;
  c.replaceChildren(
    elem("h3", names[j.request.engine]),
    elem("span", statusLabel(j.status), "badge"),
  );
  if (j.error) c.append(elem("p", j.error.message, "error"));
  if (!j.audio_id) return;
  try {
    const m = await api("/audio/" + j.audio_id + "/metadata"),
      a = elem("audio");
    a.controls = true;
    a.preload = "metadata";
    a.src = "/v1/audio/" + j.audio_id;
    c.append(elem("p", m.text), a);
    const links = elem("div");
    links.className = "row";
    for (const f of ["wav", "mp3"]) {
      const l = elem("a", tx("dl", { f: f.toUpperCase() }), "link");
      l.href = "/v1/audio/" + j.audio_id + "?format=" + f + "&download=true";
      links.append(l);
    }
    c.append(
      links,
      elem("p", tx("sec.n", { v: fixed(m.pcm.duration_seconds, 1) }), "metrics"),
    );
    const review = elem("details");
    review.append(elem("summary", tx("job.review")));
    c.append(review);
    await reviewPanel(review, a, m, j);
    const details = elem("details");
    details.className = "technical-details";
    details.append(elem("summary", tx("job.tech")));
    const x = j.metrics || m.metrics;
    const durMeta = x.duration_meta || {};
    let durLine = tx("job.l1", {
      a: fixed(m.pcm.duration_seconds, 2),
      tts: fixed(x.synthesis_seconds || 0, 2),
      rtf: fixed(x.real_time_factor || 0, 2),
      load: fixed(x.load_seconds || 0, 2),
      q: fixed(x.queue_seconds || 0, 2),
      al: fixed(x.alignment_seconds || 0, 2),
    });
    if (durMeta.effective_words_per_minute) {
      durLine +=
        "\n" +
        tx("job.l2", {
          wpm: numl(durMeta.effective_words_per_minute),
          n: numl(durMeta.spoken_word_count, 0),
          seg: numl(x.segments_count || 1, 0),
        });
    }
    if (durMeta.target_duration_seconds) {
      durLine += tx("job.target", {
        t: numl(durMeta.target_duration_seconds),
        sign: durMeta.duration_difference_seconds > 0 ? "+" : "",
        d: numl(durMeta.duration_difference_seconds),
        status: K_status(durMeta.duration_status),
      });
    }
    durLine +=
      "\n" +
      tx("job.l3", {
        ram: fixed((x.worker_peak_rss_bytes_observed || 0) / 1024 ** 3, 2),
        vram: fixed((x.cuda_peak_allocated_bytes || 0) / 1024 ** 3, 2),
        u: tx("unit.gib"),
        cache: tx(x.synthesis_cache_hit ? "yes" : "no"),
      });
    details.append(
      elem(
        "p",
        durLine,
        "metrics metrics-block",
      ),
    );

    const defaults =
      j.request.engine === "chatterbox"
        ? { exaggeration: 0.5, cfg_weight: 0.5, temperature: 0.8 }
        : { speed: 1 };
    details.append(
      elem(
        "p",
        tx("job.params", {
          p: JSON.stringify(
            m.effective_engine_options || { ...defaults, ...j.request.options },
          ),
          seed: j.request.seed,
          lang: langName(j.request.language),
        }),
        "metrics",
      ),
    );
    if (m.audio_quality) {
      const q = m.audio_quality;
      details.append(
        elem(
          "p",
          tx("job.quality", {
            tp: numl(q.true_peak_dbfs),
            rails: numl(q.rails.rail_sample_count, 0),
            clip: tx(q.rails.clipping_evidence ? "clip.yes" : "clip.no"),
          }),
          "metrics",
        ),
      );
    }
    if (m.alignment_recheck_seconds)
      details.append(
        elem(
          "p",
          tx("job.recheck", {
            s: fixed(m.alignment_recheck_seconds, 2),
            rev: m.alignment_revision,
          }),
          "metrics",
        ),
      );
    details.append(elem("pre", JSON.stringify(m, null, 2)));
    c.append(details);
  } catch (e) {
    c.dataset.jobSignature = "";
    c.append(elem("p", e.message, "error"));
  }
}
async function poll() {
  clearTimeout(timer);
  try {
    const jobs = await Promise.all(active.map((id) => api("/jobs/" + id)));
    for (const j of jobs) await showJob(j);
    if (
      jobs.every((j) => ["completed", "failed", "cancelled"].includes(j.status))
    ) {
      $("message").textContent = jobs.every((j) => j.status === "completed")
        ? tx("poll.ok")
        : tx("poll.partial");
      busy(false);
      active = [];
      await loadHistory();
      return;
    }
    timer = setTimeout(poll, 1500);
  } catch (e) {
    report("message", e);
    busy(false);
  }
}
async function restoreComparison() {
  if (
    active.length ||
    $("chatterbox-result").dataset.jobSignature ||
    $("cosyvoice-result").dataset.jobSignature
  )
    return;
  const requested = new URLSearchParams(window.location.search).get(
    "comparison",
  );
  let c;
  if (requested) c = await api("/comparisons/" + encodeURIComponent(requested));
  else {
    const d = await api("/comparisons");
    c = d.comparisons.filter((x) => x.status === "completed").at(-1);
  }
  if (c) for (const j of c.jobs) await showJob(j);
}
async function loadHistory() {
  const d = await api("/jobs");
  const done = d.jobs
    .filter((j) => ["completed", "failed", "cancelled"].includes(j.status))
    .reverse()
    .slice(0, 20);
  $("history").replaceChildren(
    ...(done.length
      ? done.map((j) => {
          const row = elem("div");
          row.className = "history-item";
          const description = elem("div");
          description.append(
            elem(
              "strong",
              names[j.request.engine] + " · " + statusLabel(j.status),
            ),
            elem("p", j.request.text.slice(0, 120)),
            elem(
              "small",
              dateTime(j.created_at),
            ),
          );
          const b = elem("button", tx("history.play"), "secondary");
          b.onclick = async () => {
            navigate("synthesis");
            try {
              await showJob(j);
              $(j.request.engine + "-result").scrollIntoView({
                behavior: "smooth",
                block: "center",
              });
            } catch (e) {
              report("message", e);
            }
          };
          row.append(description, b);
          return row;
        })
      : [elem("span", tx("history.empty"))]),
  );
}
$("voice").onchange = () => {
  selectedVoice = null;
  updateReadiness();
  previewSelectedReference();
};
$("refresh").onclick = async () => {
  for (const e of Object.keys(names))
    $(e + "-result").dataset.jobSignature = "";
  await refresh();
};
$("generate").onclick = () => submit(false);
$("compare").onclick = () => submit(true);
$("cancel").onclick = async () => {
  try {
    await Promise.all(
      active.map((id) => api("/jobs/" + id + "/cancel", { method: "POST" })),
    );
    await poll();
  } catch (e) {
    report("message", e);
  }
};

function K_status(status) {
  const key = "ds." + status;
  return ppsDictionary[key] ? tx(key) : status;
}
// Voreinstellung der Sprachauswahl folgt der Oberflächensprache, solange sie auf einer Voreinstellung steht.
function syncLanguageDefaults() {
  for (const id of ["language", "media-language", "voice-language"]) {
    const select = $(id);
    if (select && ["de", "fr"].includes(select.value)) select.value = getLang();
  }
}
// Sprachwechsel ohne Neuladen: feste Texte tauscht i18n.js, hier werden die erzeugten Inhalte neu aufgebaut.
document.addEventListener("pps-lang", () => {
  syncLanguageDefaults();
  for (const e of Object.keys(names)) {
    $(e + "-result").dataset.jobSignature = "";
    $(e + "-result").replaceChildren(
      elem("h3", names[e]),
      elem("p", tx("results.empty", { engine: names[e] }), "empty-result"),
    );
  }
  $("reference-result").dataset.reference = "";
  $("message").replaceChildren();
  navigate(currentView, false);
  busy(generating);
  updateDurationEstimate();
  updateReadiness();
  if (!selectedVoice) previewSelectedReference();
  refresh();
});
syncLanguageDefaults();
updateDurationEstimate();
updateReadiness();
refresh();
