const $ = (id) => document.getElementById(id),
  names = { chatterbox: "Chatterbox", cosyvoice: "CosyVoice" };
let active = [],
  timer = null,
  generating = false,
  selectedVoice = null,
  referenceRequest = 0;
const statuses = {
  queued: "En attente",
  loading: "Chargement du moteur",
  running: "En cours",
  synthesizing: "Génération en cours",
  exporting: "Préparation audio",
  aligning: "Préparation des sous-titres",
  completed: "Prêt à écouter",
  failed: "Échec",
  cancelled: "Annulé",
  paused: "En pause",
};
function statusLabel(value) {
  return statuses[value] || "En cours";
}
const pages = {
  synthesis: [
    "Synthèse vocale",
    "Écrivez votre texte. Choisissez une voix. Écoutez.",
  ],
  voices: [
    "Mes voix",
    "Ajoutez une voix et choisissez les extraits qui lui ressemblent le mieux.",
  ],
  history: ["Historique", "Tous vos essais, au même endroit."],
  settings: [
    "Réglages",
    "Personnalisez les moteurs et consultez l’état du service.",
  ],
};
function navigate(view, focus = true) {
  if (!pages[view]) view = "synthesis";
  for (const section of document.querySelectorAll(".view"))
    section.hidden = section.id !== "view-" + view;
  for (const item of document.querySelectorAll("[data-view]")) {
    const on = item.dataset.view === view;
    item.classList.toggle("selected", on);
    if (on) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  }
  $("page-title").textContent = pages[view][0];
  $("page-description").textContent = pages[view][1];
  document.title = "PPS · " + pages[view][0];
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
    ? "Ajoutez ou choisissez une voix pour commencer."
    : !reference
      ? "Préparez cette voix une seule fois dans « Mes voix », puis réutilisez-la pour tous vos textes."
      : !usable
        ? "CosyVoice a besoin des mots exacts de l’extrait. Vérifiez-les dans « Écouter la voix originale »."
        : `${selectedVoice.references.length} extrait${selectedVoice.references.length > 1 ? "s" : ""} disponible${selectedVoice.references.length > 1 ? "s" : ""}.`;
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
      elem("p", "Ajoutez un extrait pour écouter cette voix.", "muted"),
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
  $("text-count").textContent =
    $("text").value.length.toLocaleString("fr-FR") +
    " / 50 000 caractères (" +
    words.toLocaleString("fr-FR") +
    " " +
    (words > 1 ? "mots" : "mot") +
    ")";

  if (!txt.trim()) {
    $("duration-estimate").textContent = "Durée estimée : ~0.0 s";
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
          language: $("language")?.value || "fr",
          engine: $("engine")?.value || "chatterbox",
          target_duration_seconds: targetSec,
        }),
      });

      $("duration-estimate").textContent =
        "Durée estimée : ~" +
        res.estimated_duration_seconds +
        " s (plage " +
        res.estimated_duration_min_seconds +
        "–" +
        res.estimated_duration_max_seconds +
        " s, débit " +
        res.effective_wpm +
        " mots/min)";

      const alertBox = $("duration-alert");
      if (alertBox) {
        if (res.duration_status === "too_short" || res.duration_status === "too_long") {
          alertBox.style.display = "block";
          alertBox.className = "status warning";
          alertBox.textContent = res.advice?.message || "Écart avec la durée cible détecté.";
        } else if (res.duration_status === "compatible") {
          alertBox.style.display = "block";
          alertBox.className = "status success";
          alertBox.textContent = res.advice?.message || "Durée compatible avec la cible.";
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
      (d.error?.code || r.status) +
        " : " +
        (d.error?.message || JSON.stringify(d)),
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
    $("resources").textContent =
      `RAM disponible : ${s.ram_available_gib.toFixed(2)} Gio · VRAM disponible : ${s.gpu?.free_gib.toFixed(2) ?? "indisponible"} Gio · Disque : ${s.disk_free_gib.toFixed(1)} Gio`;
    $("resources").append(
      elem(
        "p",
        `Minimum au chargement : ${engines.resource_thresholds.min_ram_gib} Gio de RAM disponible après imports / ${engines.resource_thresholds.min_vram_gib} Gio de VRAM libre.`,
      ),
    );
    $("engines").replaceChildren(
      ...engines.engines.map((e) =>
        elem(
          "div",
          `${e.name} : ${e.ready_to_attempt_load ? "prêt pour un essai de chargement" : "préparation incomplète"}`,
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
        : [elem("option", "Aucune voix enregistrée")]),
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
      Error(
        "Service PPS indisponible. Ouvrez les réglages pour actualiser la connexion.",
      ),
    );
  }
}
$("import-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = e.target.querySelector("button");
  button.disabled = true;
  $("import-message").textContent = "Préparation audio…";
  try {
    const v = await api("/voices", {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: new FormData(e.target),
    });
    $("import-message").textContent = "Voix enregistrée : " + v.name;
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
      Error("Choisissez une voix autorisée et saisissez un texte."),
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
          elem("span", "En attente", "badge"),
        );
    $("message").textContent =
      "Préparation en cours… Le premier essai peut prendre un peu plus de temps.";
    await poll();
  } catch (e) {
    report("message", e);
    busy(false);
  }
}
function busy(on) {
  generating = on;
  $("cancel").hidden = !on;
  $("generate").textContent = on
    ? "Génération en cours…"
    : "Générer la voix ↗";
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
    elem("p", `${r.pcm.duration_seconds.toFixed(1)} secondes`, "metrics"),
  );
  const edit = elem("details");
  edit.append(
    elem("summary", "Corriger / vérifier la transcription de cette référence"),
  );
  const transcript = elem("textarea");
  transcript.value = r.transcription;
  transcript.maxLength = 5000;
  transcript.setAttribute(
    "aria-label",
    "Transcription corrigée de la référence",
  );
  const verified = elem("input");
  verified.type = "checkbox";
  verified.checked = r.transcription_verified !== false && !!r.transcription;
  const label = elem("label");
  label.append(
    verified,
    document.createTextNode(" J’ai écouté l’extrait et vérifié tous les mots."),
  );
  const save = elem("button", "Conserver la transcription"),
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
  advanced.append(elem("summary", "Détails de cet extrait"));
  advanced.append(
    elem("p", `Référence ${r.id} · Version ${voice.version}`, "metrics"),
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
        `Score technique : ${m.score.toFixed(1)} / 100 · Cohérence avec les autres références : ${m.consistency_with_saved_voice_cosine == null ? "pas encore mesurée" : m.consistency_with_saved_voice_cosine.toFixed(3) + " (estimation à vérifier à l’écoute)"}`,
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
    b.title = `${w.start.toFixed(3)}–${w.end.toFixed(3)} s · ${w.method || "inconnu"} · ${(w.uncertainty || []).join(", ")}`;
    b.setAttribute(
      "aria-label",
      `${w.text || w.word}, de ${w.start.toFixed(3)} à ${w.end.toFixed(3)} secondes`,
    );
    b.onclick = () => {
      player.currentTime = w.start;
      player.play().catch(() => {});
    };
    strip.append(b);
    return b;
  });
  const clock = elem("p", "0,000 s", "metrics");
  container.append(strip, clock);
  player.addEventListener("timeupdate", () => {
    clock.textContent = player.currentTime.toFixed(3) + " s";
    words.forEach((w, i) => {
      const on = player.currentTime >= w.start && player.currentTime < w.end;
      buttons[i].classList.toggle("speaking", on);
      buttons[i].setAttribute("aria-current", on ? "true" : "false");
    });
  });
  const edits = elem("details");
  edits.append(
    elem(
      "summary",
      "Frontières temporelles · estimation automatique et corrections",
    ),
  );
  const table = elem("table"),
    head = elem("tr");
  for (const title of [
    "Mot",
    "Automatique (s)",
    "Éffective (s)",
    "Provenance / incertitudes",
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
      elem("td", `${automatic.start.toFixed(3)} → ${automatic.end.toFixed(3)}`),
    );
    const start = elem("input"),
      end = elem("input");
    for (const [input, value, label] of [
      [start, w.start, "Début"],
      [end, w.end, "Fin"],
    ]) {
      input.type = "number";
      input.min = "0";
      input.max = String(player.duration || 999);
      input.step = ".001";
      input.value = value.toFixed(3);
      input.disabled = !edit;
      input.setAttribute("aria-label", label + " de " + (w.text || w.word));
      cell.append(input);
    }
    tr.append(cell);
    tr.append(
      elem(
        "td",
        `${w.method || alignment.timestamp_provenance} · ${w.confidence_status || "incertain"} · ${(w.uncertainty || []).join(", ")}`,
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
      "Alignement : " +
        alignment.status +
        " · Sous-titres MainClip : " +
        (alignment.mainclip_compatible
          ? "acceptés par la revue"
          : "bloqués tant que les frontières restent incertaines"),
      "metrics",
    ),
  );
  const inputs = wordPlayer(c, player, alignment, true),
    form = elem("form");
  form.className = "review-form";
  form.append(elem("h4", "Votre écoute · notes de 1 à 5"));
  const fields = {
      fidelity: "Fidélité à la référence",
      naturalness: "Naturel",
      pronunciation: "Prononciation",
      expressiveness: "Expressivité",
      artifacts: "Absence d’artefacts",
      audio_quality: "Qualité sonore",
    },
    selectors = {};
  for (const [key, label] of Object.entries(fields)) {
    const id = m.id + "-" + key,
      l = elem("label", label);
    l.htmlFor = id;
    const select = elem("select");
    select.id = id;
    const empty = elem("option", "Non évalué");
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
  notes.placeholder = "Vos observations après écoute";
  notes.setAttribute("aria-label", "Observations après écoute");
  notes.maxLength = 5000;
  notes.value = r.latest?.notes || "";
  const confirmed = elem("input");
  confirmed.type = "checkbox";
  confirmed.checked = r.latest?.acoustically_confirmed || false;
  const confirmation = elem("label");
  confirmation.className = "consent";
  confirmation.append(
    confirmed,
    document.createTextNode(
      " J’ai écouté tous les mots et vérifié/corrigé leurs frontières sur cet audio. Autoriser les sous-titres MainClip.",
    ),
  );
  const save = elem("button", "Enregistrer ma revue"),
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
      const l = elem("a", "Télécharger " + f.toUpperCase(), "link");
      l.href = "/v1/audio/" + j.audio_id + "?format=" + f + "&download=true";
      links.append(l);
    }
    c.append(
      links,
      elem("p", m.pcm.duration_seconds.toFixed(1) + " secondes", "metrics"),
    );
    const review = elem("details");
    review.append(
      elem("summary", "Évaluer l’audio et vérifier les sous-titres"),
    );
    c.append(review);
    await reviewPanel(review, a, m, j);
    const details = elem("details");
    details.className = "technical-details";
    details.append(elem("summary", "Détails techniques"));
    const x = j.metrics || m.metrics;
    const durMeta = x.duration_meta || {};
    let durLine = `Audio : ${m.pcm.duration_seconds.toFixed(2)} s · TTS : ${(x.synthesis_seconds || 0).toFixed(2)} s · RTF : ${(x.real_time_factor || 0).toFixed(2)} · Chargement : ${(x.load_seconds || 0).toFixed(2)} s · File : ${(x.queue_seconds || 0).toFixed(2)} s · Alignement : ${(x.alignment_seconds || 0).toFixed(2)} s`;
    if (durMeta.effective_words_per_minute) {
      durLine += `\nDébit réel : ${durMeta.effective_words_per_minute} mots/min (${durMeta.spoken_word_count} mots) · Segments : ${x.segments_count || 1}`;
    }
    if (durMeta.target_duration_seconds) {
      durLine += ` · Cible : ${durMeta.target_duration_seconds} s (écart ${durMeta.duration_difference_seconds > 0 ? "+" : ""}${durMeta.duration_difference_seconds} s, statut : ${durMeta.duration_status})`;
    }
    durLine += `\nRAM worker : ${((x.worker_peak_rss_bytes_observed || 0) / 1024 ** 3).toFixed(2)} Gio · VRAM allouée max : ${((x.cuda_peak_allocated_bytes || 0) / 1024 ** 3).toFixed(2)} Gio · Cache synthèse : ${x.synthesis_cache_hit ? "oui" : "non"}`;
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
        "Paramètres effectifs : " +
          JSON.stringify(
            m.effective_engine_options || { ...defaults, ...j.request.options },
          ) +
          " · Seed " +
          j.request.seed +
          " · Langue " +
          j.request.language,
        "metrics",
      ),
    );
    if (m.audio_quality) {
      const q = m.audio_quality;
      details.append(
        elem(
          "p",
          `True peak : ${q.true_peak_dbfs} dBFS · Échantillons aux rails : ${q.rails.rail_sample_count} · Clipping mesuré : ${q.rails.clipping_evidence ? "indices présents" : "aucun plateau détecté"} · Audible : à évaluer`,
          "metrics",
        ),
      );
    }
    if (m.alignment_recheck_seconds)
      details.append(
        elem(
          "p",
          `Réanalyse hors ligne : ${m.alignment_recheck_seconds.toFixed(2)} s · révision ${m.alignment_revision}`,
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
        ? "Résultats disponibles."
        : "Traitement terminé. Consultez l'état de chaque moteur.";
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
              new Date(j.created_at * 1000).toLocaleString("fr-FR"),
            ),
          );
          const b = elem("button", "Écouter / télécharger", "secondary");
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
      : [elem("span", "Vos générations apparaîtront ici.")]),
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
updateReadiness();
refresh();
