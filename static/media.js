/* Media intake and reference choices. Original sources remain in local PPS storage. */
(() => {
  const json = (method, body) => ({
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const clock = (n) =>
    `${Math.floor(n / 3600)
      .toString()
      .padStart(2, "0")}:${Math.floor((n % 3600) / 60)
      .toString()
      .padStart(2, "0")}:${(n % 60).toFixed(2).padStart(5, "0")}`;
  let transfer = null,
    libraryTimer = null,
    voices = [],
    lastVoice = "",
    trialId = null,
    trialTimer = null;
  function filterLibrary() {
    for (const section of $("media-library").children)
      section.hidden =
        !!$("media-filter").value &&
        section.dataset.voice !== $("media-filter").value;
  }
  const error = (node, e) => {
    node.textContent = e.message;
    node.className = "error";
  };
  const button = (label, action) => {
    const b = elem("button", label, "secondary");
    b.type = "button";
    b.onclick = async () => {
      b.disabled = true;
      try {
        await action();
      } catch (e) {
        error($("media-message"), e);
      } finally {
        b.disabled = false;
      }
    };
    return b;
  };

  async function loadVoices() {
    voices = (await api("/voices")).voices;
    const selected = $("media-voice").value;
    const empty = elem("option", "Créer une voix");
    empty.value = "";
    $("media-voice").replaceChildren(
      empty,
      ...voices.map((v) => {
        const o = elem("option", v.name);
        o.value = v.id;
        return o;
      }),
    );
    if (voices.some((v) => v.id === selected))
      $("media-voice").value = selected;
    const filtered = $("media-filter").value,
      all = elem("option", "Toutes les voix");
    all.value = "";
    $("media-filter").replaceChildren(
      all,
      ...voices.map((v) => {
        const o = elem("option", v.name);
        o.value = v.id;
        return o;
      }),
    );
    if (voices.some((v) => v.id === filtered))
      $("media-filter").value = filtered;
    filterLibrary();
    $("media-voice").dispatchEvent(new Event("change"));
    $("voice-count").textContent = voices.length;
    $("voice-library").replaceChildren(
      ...(voices.length
        ? voices.map((v) => {
            const card = elem("article");
            card.className = "card voice-card";
            card.dataset.voice = v.id;
            card.dataset.pending = String(!v.references.length);
            card.append(
              elem("div", v.name.slice(0, 2).toUpperCase(), "voice-avatar"),
              elem("h3", v.name),
              elem(
                "p",
                (v.language === "fr" ? "Français" : "Anglais") +
                  " · " +
                  v.references.length +
                  " extrait" +
                  (v.references.length > 1 ? "s" : ""),
                "muted",
              ),
            );
            card.append(
              elem(
                "p",
                v.references.length
                  ? "Extrait enregistré. Vous pouvez utiliser cette voix."
                  : "PPS peut préparer automatiquement cette voix depuis vos fichiers.",
                "field-help",
              ),
            );
            const actions = elem("div");
            actions.className = "row";
            actions.append(
              button(
                v.references.length
                  ? "Utiliser cette voix"
                  : "Préparer cette voix",
                async () => {
                  if (!v.references.length) {
                    const sources = (await api("/media")).media.filter(
                      (m) => m.voice_id === v.id && m.status === "ready",
                    );
                    if (sources.length)
                      await window.startVoicePreparation(
                        v.id,
                        sources.map((m) => m.id),
                      );
                    else addVoice(v.id);
                    return;
                  }
                  $("voice").value = v.id;
                  $("voice").dispatchEvent(new Event("change"));
                  navigate("synthesis");
                  $("text").focus();
                },
              ),
              button("Ajouter des fichiers", async () => addVoice(v.id)),
            );
            card.append(actions);
            return card;
          })
        : [
            (() => {
              const empty = elem("div");
              empty.className = "empty-voices";
              empty.append(
                elem("h2", "Votre première voix"),
                elem(
                  "p",
                  "Ajoutez une vidéo ou un audio pour créer votre voix.",
                  "muted",
                ),
                button("+ Ajouter une voix", async () => addVoice()),
              );
              return empty;
            })(),
          ]),
    );
    document.dispatchEvent(new Event("pps-voice-cards"));
  }
  async function referenceChoices() {
    const id = $("voice").value;
    if (!id) {
      setVoiceState(null);
      return;
    }
    const voice = await api("/voices/" + id);
    lastVoice = id;
    if ($("voice").value !== id) return;
    $("trial-reference-list").replaceChildren(
      ...voice.references.map((r) => {
        const label = elem("label"),
          check = elem("input");
        check.type = "checkbox";
        check.value = r.id;
        label.append(
          check,
          document.createTextNode(
            r.filename + " · " + r.pcm.duration_seconds.toFixed(1) + " s",
          ),
        );
        return label;
      }),
    );
    for (const engine of Object.keys(names)) {
      const select = $(engine + "-reference"),
        fallback = elem("option", "Référence enregistrée");
      fallback.value = "";
      select.replaceChildren(
        fallback,
        ...voice.references.map((r) => {
          const o = elem(
            "option",
            r.filename +
              " · " +
              r.pcm.duration_seconds.toFixed(1) +
              " s" +
              (engine === "cosyvoice" && r.transcription_verified === false
                ? " · transcription à vérifier"
                : ""),
          );
          o.value = r.id;
          return o;
        }),
      );
      select.value =
        voice.engine_reference_ids?.[engine] || voice.active_reference_id || "";
    }
    setVoiceState(voice);
  }
  for (const engine of Object.keys(names))
    $(engine + "-reference").onchange = async () => {
      try {
        const reference = $(engine + "-reference").value;
        if (!reference) return;
        await api(
          "/voices/" + $("voice").value + "/engine-reference",
          json("PUT", { engine, reference_id: reference }),
        );
        $("reference-choice-message").textContent =
          "Choix conservé pour " + names[engine];
        await referenceChoices();
      } catch (e) {
        report("reference-choice-message", e);
        await referenceChoices();
      }
    };
  async function trialsHistory() {
    const d = await api("/reference-trials");
    $("trial-history").replaceChildren(
      ...d.trials
        .slice(-10)
        .reverse()
        .map((t) =>
          button(
            new Date(t.created_at * 1000).toLocaleString("fr-FR") +
              " · " +
              t.reference_ids.length +
              " références",
            async () => {
              trialId = t.id;
              $("trial-results").replaceChildren();
              await pollTrial();
            },
          ),
        ),
    );
  }
  async function pollTrial() {
    clearTimeout(trialTimer);
    if (!trialId) return;
    try {
      const t = await api("/reference-trials/" + trialId);
      $("trial-cancel").hidden = t.finished;
      $("trial-start").disabled = !t.finished;
      $("trial-message").textContent = t.finished
        ? "Essais terminés. Écoutez et choisissez votre référence ; aucun gagnant automatique."
        : "Essais en cours, un moteur à la fois.";
      for (const j of t.jobs) {
        const id = "trial-result-" + j.id;
        let card = $(id);
        if (!card) {
          card = elem("article");
          card.id = id;
          card.className = "card";
          card.style.marginTop = "12px";
          $("trial-results").append(card);
        }
        const signature = j.status + ":" + j.audio_id;
        if (card.dataset.signature === signature) continue;
        card.dataset.signature = signature;
        const reference = voices
          .find((v) => v.id === t.voice_id)
          ?.references.find((r) => r.id === j.request.reference_id);
        card.replaceChildren(
          elem(
            "h4",
            names[j.request.engine] +
              " · " +
              (reference?.filename || "Extrait vocal"),
          ),
          elem("span", statusLabel(j.status), "badge"),
        );
        if (j.error) card.append(elem("p", j.error.message, "error"));
        if (j.audio_id) {
          const player = elem("audio");
          player.controls = true;
          player.preload = "none";
          player.src = "/v1/audio/" + j.audio_id;
          card.append(
            player,
            elem(
              "p",
              `Synthèse : ${(j.metrics.synthesis_seconds || 0).toFixed(2)} s · Chargement : ${(j.metrics.load_seconds || 0).toFixed(2)} s · RAM : ${((j.metrics.worker_peak_rss_bytes_observed || 0) / 1024 ** 3).toFixed(2)} Gio · Résultat en cache : ${j.metrics.synthesis_cache_hit ? "oui" : "non"}`,
              "metrics",
            ),
            button("Évaluer ce résultat", async () => {
              navigate("synthesis");
              await showJob(j);
              $(j.request.engine + "-result").scrollIntoView({
                behavior: "smooth",
              });
            }),
            button(
              "Utiliser cette référence pour " + names[j.request.engine],
              async () => {
                await api(
                  "/voices/" + t.voice_id + "/engine-reference",
                  json("PUT", {
                    engine: j.request.engine,
                    reference_id: j.request.reference_id,
                  }),
                );
                $("voice").value = t.voice_id;
                await referenceChoices();
                $("trial-message").textContent =
                  "Choix enregistré pour " + names[j.request.engine];
              },
            ),
          );
        }
      }
      if (!t.finished) trialTimer = setTimeout(pollTrial, 1500);
      else await trialsHistory();
    } catch (e) {
      report("trial-message", e);
      $("trial-start").disabled = false;
    }
  }
  $("trial-start").onclick = async () => {
    try {
      const refs = Array.from(
        $("trial-reference-list").querySelectorAll("input:checked"),
      ).map((x) => x.value);
      if (refs.length < 2 || refs.length > 8)
        throw Error("Choisissez entre 2 et 8 références de cette voix.");
      if (!$("text").value.trim())
        throw Error("Saisissez le texte commun à prononcer.");
      $("trial-start").disabled = true;
      const t = await api("/reference-trials", {
        ...json("POST", {
          voice_id: $("voice").value,
          reference_ids: refs,
          engine: $("trial-engine").value,
          text: $("text").value,
          language: $("language").value,
          engine_options: opts(),
          seed: 1986,
        }),
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": crypto.randomUUID(),
        },
      });
      trialId = t.trial_id;
      $("trial-results").replaceChildren();
      await pollTrial();
    } catch (e) {
      report("trial-message", e);
      $("trial-start").disabled = false;
    }
  };
  $("trial-cancel").onclick = async () => {
    try {
      if (trialId)
        await api("/reference-trials/" + trialId + "/cancel", {
          method: "POST",
        });
      await pollTrial();
    } catch (e) {
      report("trial-message", e);
    }
  };
  $("voice").addEventListener("change", () =>
    referenceChoices().catch((e) => report("reference-choice-message", e)),
  );
  document.addEventListener("pps-voices", () =>
    Promise.all([loadVoices(), referenceChoices()]).catch((e) =>
      report("media-message", e),
    ),
  );
  $("media-voice").onchange = () => {
    $("media-name").disabled = !!$("media-voice").value;
    $("media-name").required = !$("media-voice").value;
  };
  $("media-filter").onchange = filterLibrary;
  $("media-files").onchange = () => {
    $("media-file-list").replaceChildren(
      ...Array.from($("media-files").files).map((f) =>
        elem("div", `${f.name} · ${(f.size / 1024 ** 2).toFixed(1)} Mo`),
      ),
    );
  };

  async function fingerprint(file) {
    const head = new Uint8Array(await file.slice(0, 65536).arrayBuffer()),
      tail = new Uint8Array(
        await file.slice(Math.max(0, file.size - 65536)).arrayBuffer(),
      );
    const info = new TextEncoder().encode(
      JSON.stringify([file.name, file.size, file.lastModified]),
    );
    const bytes = new Uint8Array(head.length + tail.length + info.length);
    bytes.set(head);
    bytes.set(tail, head.length);
    bytes.set(info, head.length + tail.length);
    return Array.from(
      new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    )
      .map((x) => x.toString(16).padStart(2, "0"))
      .join("");
  }
  $("media-upload-cancel").onclick = () => transfer?.abort();
  $("media-import-form").onsubmit = async (e) => {
    e.preventDefault();
    if (transfer) return;
    if (!$("media-authorized").checked) {
      error(
        $("media-message"),
        Error(
          "Confirmez que vous avez le droit de cloner et d’utiliser cette voix.",
        ),
      );
      return;
    }
    if (!$("media-files").files.length) {
      error($("media-message"), Error("Choisissez au moins un fichier."));
      return;
    }
    const agreement =
      $("media-origin").value === "own"
        ? "Ma propre voix, clonage et utilisation avec mon accord."
        : $("media-origin").value === "permission"
          ? "Voix d’une personne dont j’ai obtenu l’accord pour le clonage et l’utilisation."
          : "Je confirme disposer du droit de cloner et d’utiliser la voix contenue dans les fichiers sélectionnés.";
    $("media-declaration").value =
      agreement +
      ($("media-source").value.trim()
        ? " Origine des fichiers : " + $("media-source").value.trim()
        : " Fichiers sélectionnés par l’utilisateur, noms conservés dans la provenance.");
    transfer = new AbortController();
    $("media-upload").disabled = true;
    $("media-upload-cancel").hidden = false;
    let voiceId = $("media-voice").value;
    const mediaIds = [];
    $("media-message").className = "status";
    try {
      for (const file of $("media-files").files) {
        if (transfer.signal.aborted)
          throw Error(
            "Transfert interrompu. Sélectionnez les mêmes fichiers pour reprendre.",
          );
        const fp = await fingerprint(file),
          key = "pps-media-upload:" + fp,
          prior = localStorage.getItem(key);
        let m;
        if (prior) {
          try {
            m = await api("/media/uploads/" + prior);
          } catch (_) {
            localStorage.removeItem(key);
          }
        }
        if (m && voiceId && m.voice_id !== voiceId)
          throw Error(
            "Ce fichier est déjà associé à une autre voix. Choisissez cette voix pour reprendre.",
          );
        if (!m) {
          m = await api(
            "/media/uploads",
            json("POST", {
              filename: file.name,
              size_bytes: file.size,
              fingerprint: fp,
              voice_id: voiceId || null,
              name: $("media-name").value,
              language: $("media-language").value,
              authorized: $("media-authorized").checked,
              declaration: $("media-declaration").value,
            }),
          );
          localStorage.setItem(key, m.id);
        }
        voiceId = m.voice_id;
        mediaIds.push(m.id);
        if (m.status === "ready") continue;
        for (let offset = m.received_bytes; offset < file.size; ) {
          $("media-message").textContent =
            `${file.name} : ${((100 * offset) / file.size).toFixed(1)} % transférés`;
          m = await api("/media/uploads/" + m.id + "?offset=" + offset, {
            method: "PUT",
            body: file.slice(
              offset,
              Math.min(file.size, offset + 8 * 1024 ** 2),
            ),
            signal: transfer.signal,
          });
          offset = m.received_bytes;
        }
        $("media-message").textContent =
          "Vérification des pistes : " + file.name;
        await api("/media/uploads/" + m.id + "/complete", { method: "POST" });
      }
      $("media-message").textContent =
        "Import terminé. PPS prépare automatiquement votre voix…";
      await refresh();
      await loadVoices();
      $("media-voice").value = voiceId;
      $("media-voice").dispatchEvent(new Event("change"));
      $("voice").value = voiceId;
      await referenceChoices();
      await library();
      $("media-filter").value = voiceId;
      filterLibrary();
      $("voice-import").open = false;
      await window.startVoicePreparation(voiceId, mediaIds);
    } catch (e) {
      error(
        $("media-message"),
        e.name === "AbortError"
          ? Error(
              "Transfert interrompu. Resélectionnez le fichier pour reprendre au dernier bloc reçu.",
            )
          : e,
      );
    } finally {
      transfer = null;
      $("media-upload").disabled = false;
      $("media-upload-cancel").hidden = true;
      await library();
    }
  };

  function candidateCard(analysis, c, index = 0) {
    const card = elem("article");
    card.className = "card candidate-card";
    const m = c.metrics,
      score = m.score ?? 0;
    card.append(
      elem(
        "h4",
        `Extrait ${index + 1} · ${(c.end_seconds - c.start_seconds).toFixed(1)} secondes`,
      ),
      elem("p", `À ${clock(c.start_seconds)} dans le fichier`, "metrics"),
    );
    const player = elem("audio");
    player.controls = true;
    player.preload = "none";
    player.src = `/v1/media/analyses/${analysis.id}/candidates/${c.id}/audio`;
    card.append(player);
    if (m.speaker_status === "suspected_multiple")
      card.append(
        elem(
          "p",
          "Plusieurs voix sont suspectées. Vérifiez que cet extrait contient seulement la personne choisie.",
          "notice",
        ),
      );
    if (
      m.music_status === "suspected_music" ||
      m.noise_status === "suspected_noise" ||
      m.echo_status === "suspected_repetition"
    )
      card.append(
        elem(
          "p",
          "Un bruit, de la musique ou des répétitions sont suspectés. Vérifiez à l’écoute.",
          "notice",
        ),
      );
    const setup = elem("details");
    setup.className = "candidate-setup";
    setup.append(
      elem(
        "summary",
        c.saved_reference_id
          ? "Extrait enregistré · vérifier ou utiliser"
          : "Choisir cet extrait pour ma voix",
      ),
      elem(
        "p",
        "Écoutez l’extrait. Faites proposer sa transcription, puis corrigez les mots si nécessaire.",
        "field-help",
      ),
    );
    const transcript = elem("textarea");
    transcript.value = c.transcription?.text || "";
    transcript.maxLength = 5000;
    transcript.placeholder = "Les mots exacts prononcés dans cet extrait";
    transcript.setAttribute("aria-label", "Mots prononcés dans cet extrait");
    const asrStatus = elem(
      "p",
      c.transcription
        ? "Ces mots ont été proposés automatiquement. Vérifiez-les à l’écoute."
        : "Pour CosyVoice, les mots prononcés doivent être vérifiés.",
      "field-help",
    );
    const lang = elem("select");
    lang.setAttribute("aria-label", "Langue de cet extrait");
    for (const [value, label] of [
      ["fr", "Français"],
      ["en", "Anglais"],
    ]) {
      const o = elem("option", label);
      o.value = value;
      lang.append(o);
    }
    if (["fr", "en"].includes(c.transcription?.language))
      lang.value = c.transcription.language;
    setup.append(
      button("Proposer les mots prononcés", async () => {
        asrStatus.textContent = "Transcription en cours…";
        try {
          const updated = await api(
            `/media/analyses/${analysis.id}/candidates/${c.id}/transcribe`,
            { method: "POST" },
          );
          transcript.value = updated.transcription.text;
          asrStatus.textContent = "Vérifiez et corrigez ces mots après écoute.";
          if (["fr", "en"].includes(updated.transcription.language))
            lang.value = updated.transcription.language;
        } catch (e) {
          error(asrStatus, e);
        }
      }),
      asrStatus,
      transcript,
      lang,
    );
    const speaker = elem("input"),
      verified = elem("input");
    speaker.type = verified.type = "checkbox";
    const speakerLabel = elem("label");
    speakerLabel.className = "consent";
    speakerLabel.append(
      speaker,
      document.createTextNode(
        " J’ai écouté : une seule voix, celle de la personne autorisée, sans musique gênante.",
      ),
    );
    const verifiedLabel = elem("label");
    verifiedLabel.className = "consent";
    verifiedLabel.append(
      verified,
      document.createTextNode(
        " J’ai vérifié et corrigé tous les mots. Nécessaire pour CosyVoice.",
      ),
    );
    const saveStatus = elem(
      "p",
      c.saved_reference_id
        ? "Cet extrait est déjà disponible dans votre voix."
        : "",
      "status",
    );
    const save = button("Enregistrer cet extrait", async () => {
      if (!speaker.checked) {
        error(
          saveStatus,
          Error(
            "Écoutez cet extrait, puis confirmez qu’il contient uniquement la voix autorisée.",
          ),
        );
        return;
      }
      try {
        const v = await api(
          `/media/analyses/${analysis.id}/candidates/${c.id}/reference`,
          json("POST", {
            speaker_confirmed: speaker.checked,
            transcription: transcript.value,
            transcription_verified: verified.checked,
            language: lang.value,
          }),
        );
        saveStatus.textContent =
          "Extrait enregistré. Votre voix est prête à être utilisée.";
        $("media-message").className = "status";
        $("media-message").textContent =
          "Extrait enregistré dans « " +
          v.name +
          " ». Cliquez sur « Utiliser cette voix » pour écrire votre texte.";
        await refresh();
        $("voice").value = v.id;
        await referenceChoices();
        await forceLibrary();
      } catch (e) {
        error(saveStatus, e);
      }
    });
    setup.append(speakerLabel, verifiedLabel, save, saveStatus);
    if (c.saved_reference_id) {
      for (const engine of Object.keys(names))
        setup.append(
          button("Utiliser avec " + names[engine], async () => {
            await api(
              "/voices/" + analysis.voice_id + "/engine-reference",
              json("PUT", { engine, reference_id: c.saved_reference_id }),
            );
            $("voice").value = analysis.voice_id;
            await referenceChoices();
            selectEngine(engine);
            navigate("synthesis");
            $("text").focus();
          }),
        );
    }
    card.append(setup);
    const trimming = elem("details");
    trimming.append(elem("summary", "Ajuster le début et la fin"));
    const range = elem("div");
    range.className = "row range-inputs";
    const start = elem("input"),
      end = elem("input");
    for (const [input, value, title] of [
      [start, c.start_seconds, "Début (secondes)"],
      [end, c.end_seconds, "Fin (secondes)"],
    ]) {
      input.type = "number";
      input.min = "0";
      input.max = analysis.total_seconds;
      input.step = ".01";
      input.value = value.toFixed(2);
      input.setAttribute("aria-label", title);
      const label = elem("label", title);
      label.append(input);
      range.append(label);
    }
    range.append(
      button("Rechercher dans ce passage", async () => {
        await api(
          "/media/analyses/" + analysis.id + "/candidates",
          json("POST", {
            start_seconds: Number(start.value),
            end_seconds: Number(end.value),
          }),
        );
        await forceLibrary();
      }),
    );
    trimming.append(range);
    card.append(trimming);
    const details = elem("details");
    details.append(
      elem("summary", "Score et détails de l’extrait"),
      elem(
        "p",
        `Score technique : ${score.toFixed(1)} / 100. Il aide à choisir un extrait ; écoutez-le pour juger la voix.`,
        "field-help",
      ),
      elem(
        "pre",
        JSON.stringify(
          {
            metrics: m,
            source_sha256: c.source_sha256,
            track: c.track_index,
            source: c.filename,
          },
          null,
          2,
        ),
      ),
    );
    card.append(details);
    return card;
  }
  function analysisCard(a) {
    const block = elem("div");
    block.className = "analysis-block";
    const running = ["running", "queued"].includes(a.status);
    if (running) {
      const progress = elem("progress");
      progress.max = 1;
      progress.value = a.progress;
      progress.setAttribute(
        "aria-label",
        "Progression de la recherche des extraits",
      );
      block.append(
        elem(
          "p",
          a.phase === "extracting_audio"
            ? "Préparation de l’audio…"
            : `Recherche des extraits… ${Math.round((a.progress || 0) * 100)} %`,
          "status",
        ),
        progress,
        button("Annuler la recherche", async () => {
          await api("/media/analyses/" + a.id + "/cancel", { method: "POST" });
          await library();
        }),
      );
      return block;
    }
    if (a.error) block.append(elem("p", a.error.message, "error"));
    if (["paused", "cancelled", "failed"].includes(a.status))
      block.append(
        button("Reprendre la recherche", async () => {
          await api("/media/analyses/" + a.id + "/resume", { method: "POST" });
          await library();
        }),
      );
    if (a.candidates.length) {
      block.append(
        elem(
          "p",
          "Écoutez un extrait, puis cliquez sur « Choisir cet extrait pour ma voix ».",
          "file-subheading",
        ),
      );
      a.candidates
        .slice(0, 3)
        .forEach((c, i) => block.append(candidateCard(a, c, i)));
      if (a.candidates.length > 3) {
        const more = elem("details");
        more.append(
          elem(
            "summary",
            "Voir les " + (a.candidates.length - 3) + " autres extraits",
          ),
        );
        a.candidates
          .slice(3)
          .forEach((c, i) => more.append(candidateCard(a, c, i + 3)));
        block.append(more);
      }
    } else
      block.append(
        elem(
          "p",
          "Aucun extrait proposé. Vous pouvez choisir un passage ci-dessous.",
          "muted",
        ),
      );
    const manual = elem("details");
    manual.append(
      elem("summary", "Choisir moi-même un passage (3 à 30 secondes)"),
    );
    const range = elem("div");
    range.className = "row range-inputs";
    const start = elem("input"),
      end = elem("input");
    for (const [input, value, title] of [
      [start, 0, "Début (secondes)"],
      [end, Math.min(10, a.total_seconds), "Fin (secondes)"],
    ]) {
      input.type = "number";
      input.value = value;
      input.min = 0;
      input.max = a.total_seconds;
      input.step = ".01";
      input.setAttribute("aria-label", title);
      const label = elem("label", title);
      label.append(input);
      range.append(label);
    }
    range.append(
      button("Écouter ce passage", async () => {
        await api(
          "/media/analyses/" + a.id + "/candidates",
          json("POST", {
            start_seconds: Number(start.value),
            end_seconds: Number(end.value),
          }),
        );
        await forceLibrary();
      }),
    );
    manual.append(range);
    block.append(manual);
    return block;
  }
  async function forceLibrary() {
    for (const c of $("media-library").children) c.dataset.signature = "";
    await library();
  }
  async function library() {
    clearTimeout(libraryTimer);
    try {
      const d = await api("/media"),
        root = $("media-library");
      for (const section of Array.from(root.children))
        if (!d.media.some((m) => m.id === section.dataset.media))
          section.remove();
      for (const m of d.media) {
        let section = Array.from(root.children).find(
          (c) => c.dataset.media === m.id,
        );
        if (!section) {
          section = elem("section");
          section.dataset.media = m.id;
          section.className = "media-file";
          root.append(section);
        }
        const analyses = d.analyses.filter((a) => a.media_id === m.id),
          signature = JSON.stringify([
            m.status,
            m.received_bytes,
            analyses.map((a) => [
              a.id,
              a.status,
              a.phase,
              a.processed_seconds,
              a.extraction_seconds,
              a.error,
              a.candidates.map((c) => [c.id, c.saved_reference_id]),
            ]),
          ]);
        section.dataset.voice = m.voice_id;
        if (section.dataset.signature === signature) continue;
        section.dataset.signature = signature;
        const name = voices.find((v) => v.id === m.voice_id)?.name;
        section.replaceChildren(
          elem("h3", m.filename),
          elem(
            "p",
            (name ? name + " · " : "") +
              (m.probe
                ? clock(m.probe.duration_seconds)
                : "Import : " +
                  ((100 * m.received_bytes) / m.size_bytes).toFixed(0) +
                  " %"),
            "metrics",
          ),
        );
        if (m.error) section.append(elem("p", m.error.message, "error"));
        if (m.status !== "ready") continue;
        const tracks = elem("select");
        tracks.setAttribute("aria-label", "Piste audio de " + m.filename);
        for (const t of m.probe.audio_tracks) {
          const o = elem(
            "option",
            `Piste audio ${t.index} · ${t.tags.language || "Langue non indiquée"} ${t.tags.title || ""}`,
          );
          o.value = t.index;
          tracks.append(o);
        }
        if (m.probe.audio_tracks.length > 1) {
          const o = elem("option", "Choisir une piste audio…");
          o.value = "";
          tracks.prepend(o);
          tracks.value = "";
          section.append(
            elem(
              "p",
              "Ce fichier contient plusieurs pistes audio. Choisissez celle qui contient votre voix.",
              "field-help",
            ),
          );
        } else tracks.hidden = true;
        section.append(tracks);
        if (!analyses.length || m.probe.audio_tracks.length > 1)
          section.append(
            button("Créer la voix automatiquement", async () => {
              if (tracks.value === "")
                throw Error(
                  "Choisissez la piste audio qui contient votre voix.",
                );
              await window.startVoicePreparation(m.voice_id, [m.id], {
                [m.id]: Number(tracks.value),
              });
              await library();
            }),
          );
        // Show only the latest analysis of each track; previous runs remain available.
        const latest = new Map();
        for (const a of analyses) latest.set(a.track_index, a);
        for (const a of latest.values()) section.append(analysisCard(a));
        if (analyses.length) {
          const advanced = elem("details");
          advanced.append(
            elem(
              "summary",
              "Relancer la recherche / consulter les recherches précédentes",
            ),
            button("Nouvelle recherche d’extraits", async () => {
              if (tracks.value === "")
                throw Error("Choisissez une piste audio.");
              await api(
                "/media/" + m.id + "/analyses",
                json("POST", {
                  track_index: Number(tracks.value),
                  fresh: true,
                }),
              );
              await library();
            }),
          );
          for (const a of analyses.filter(
            (a) => latest.get(a.track_index) !== a,
          )) {
            const old = elem("details");
            old.append(
              elem(
                "summary",
                "Recherche précédente · Piste " +
                  a.track_index +
                  " · " +
                  a.candidates.length +
                  " extraits",
              ),
            );
            old.append(analysisCard(a));
            advanced.append(old);
          }
          section.append(advanced);
        }
      }
      $("media-library-header").hidden = !d.media.length;
      filterLibrary();
      if (d.analyses.some((a) => ["running", "queued"].includes(a.status)))
        libraryTimer = setTimeout(library, 2000);
    } catch (e) {
      report("media-message", e);
    }
  }
  // Re-evaluate selected voice after the existing refresh populates its dropdown.
  new MutationObserver(() => {
    if ($("voice").value && $("voice").value !== lastVoice)
      referenceChoices().catch((e) => report("reference-choice-message", e));
  }).observe($("voice"), { childList: true });
  Promise.all([loadVoices(), library(), trialsHistory()]).catch((e) =>
    report("media-message", e),
  );
})();
