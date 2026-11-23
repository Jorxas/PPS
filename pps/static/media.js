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
      .padStart(2, "0")}:${fixed(n % 60, 2).padStart(5, "0")}`;
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
    const empty = elem("option", tx("mv.create"));
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
      all = elem("option", tx("filter.all"));
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
                tx("card.sub_" + plural(v.references.length), {
                  lang: langName(v.language),
                  n: v.references.length,
                }),
                "muted",
              ),
            );
            card.append(
              elem(
                "p",
                v.references.length ? tx("card.saved") : tx("card.prep_help"),
                "field-help",
              ),
            );
            const actions = elem("div");
            actions.className = "row";
            actions.append(
              button(
                v.references.length ? tx("card.use") : tx("card.prepare"),
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
              button(tx("card.files"), async () => addVoice(v.id)),
            );
            card.append(actions);
            return card;
          })
        : [
            (() => {
              const empty = elem("div");
              empty.className = "empty-voices";
              empty.append(
                elem("h2", tx("empty.title")),
                elem("p", tx("empty.text"), "muted"),
                button(tx("add.voice"), async () => addVoice()),
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
            r.filename + " · " + fixed(r.pcm.duration_seconds, 1) + " s",
          ),
        );
        return label;
      }),
    );
    for (const engine of Object.keys(names)) {
      const select = $(engine + "-reference"),
        fallback = elem("option", tx("ref.saved"));
      fallback.value = "";
      select.replaceChildren(
        fallback,
        ...voice.references.map((r) => {
          const o = elem(
            "option",
            r.filename +
              " · " +
              fixed(r.pcm.duration_seconds, 1) +
              " s" +
              (engine === "cosyvoice" && r.transcription_verified === false
                ? tx("opt.tr_check")
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
        $("reference-choice-message").textContent = tx("ref.kept", {
          engine: names[engine],
        });
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
            tx("trial.n", {
              date: dateTime(t.created_at),
              n: t.reference_ids.length,
            }),
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
        ? tx("trial.done")
        : tx("trial.running");
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
              (reference?.filename || tx("trial.clip")),
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
              tx("trial.metrics", {
                s: fixed(j.metrics.synthesis_seconds || 0, 2),
                l: fixed(j.metrics.load_seconds || 0, 2),
                r: fixed(
                  (j.metrics.worker_peak_rss_bytes_observed || 0) / 1024 ** 3,
                  2,
                ),
                u: tx("unit.gib"),
                c: tx(j.metrics.synthesis_cache_hit ? "yes" : "no"),
              }),
              "metrics",
            ),
            button(tx("trial.eval"), async () => {
              navigate("synthesis");
              await showJob(j);
              $(j.request.engine + "-result").scrollIntoView({
                behavior: "smooth",
              });
            }),
            button(
              tx("trial.use", { engine: names[j.request.engine] }),
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
                $("trial-message").textContent = tx("trial.saved", {
                  engine: names[j.request.engine],
                });
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
        throw Error(tx("trial.err.count"));
      if (!$("text").value.trim())
        throw Error(tx("trial.err.text"));
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
        elem(
          "div",
          `${f.name} · ${tx("up.mb", { n: fixed(f.size / 1024 ** 2, 1) })}`,
        ),
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
        Error(tx("up.consent")),
      );
      return;
    }
    if (!$("media-files").files.length) {
      error($("media-message"), Error(tx("up.nofile")));
      return;
    }
    const agreement =
      $("media-origin").value === "own"
        ? tx("up.own")
        : $("media-origin").value === "permission"
          ? tx("up.perm")
          : tx("up.default");
    $("media-declaration").value =
      agreement +
      ($("media-source").value.trim()
        ? tx("up.origin", { s: $("media-source").value.trim() })
        : tx("up.origin.default"));
    transfer = new AbortController();
    $("media-upload").disabled = true;
    $("media-upload-cancel").hidden = false;
    let voiceId = $("media-voice").value;
    const mediaIds = [];
    $("media-message").className = "status";
    try {
      for (const file of $("media-files").files) {
        if (transfer.signal.aborted)
          throw Error(tx("up.aborted"));
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
          throw Error(tx("up.other"));
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
          $("media-message").textContent = tx("up.progress", {
            f: file.name,
            p: fixed((100 * offset) / file.size, 1),
          });
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
        $("media-message").textContent = tx("up.verify", { f: file.name });
        await api("/media/uploads/" + m.id + "/complete", { method: "POST" });
      }
      $("media-message").textContent = tx("up.done");
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
          ? Error(tx("up.aborted2"))
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
        tx("cand.title", {
          i: index + 1,
          d: fixed(c.end_seconds - c.start_seconds, 1),
        }),
      ),
      elem("p", tx("cand.at", { t: clock(c.start_seconds) }), "metrics"),
    );
    const player = elem("audio");
    player.controls = true;
    player.preload = "none";
    player.src = `/v1/media/analyses/${analysis.id}/candidates/${c.id}/audio`;
    card.append(player);
    if (m.speaker_status === "suspected_multiple")
      card.append(
        elem("p", tx("cand.multi"), "notice"),
      );
    if (
      m.music_status === "suspected_music" ||
      m.noise_status === "suspected_noise" ||
      m.echo_status === "suspected_repetition"
    )
      card.append(
        elem("p", tx("cand.noise"), "notice"),
      );
    const setup = elem("details");
    setup.className = "candidate-setup";
    setup.append(
      elem(
        "summary",
        c.saved_reference_id ? tx("cand.sum.saved") : tx("cand.sum.choose"),
      ),
      elem("p", tx("cand.help"), "field-help"),
    );
    const transcript = elem("textarea");
    transcript.value = c.transcription?.text || "";
    transcript.maxLength = 5000;
    transcript.placeholder = tx("cand.tr.ph");
    transcript.setAttribute("aria-label", tx("cand.tr.aria"));
    const asrStatus = elem(
      "p",
      c.transcription ? tx("cand.asr.auto") : tx("cand.asr.need"),
      "field-help",
    );
    const lang = elem("select");
    lang.setAttribute("aria-label", tx("cand.lang.aria"));
    for (const value of ["de", "en", "fr"]) {
      const o = elem("option", langName(value));
      o.value = value;
      lang.append(o);
    }
    lang.value = getLang();
    if (["de", "en", "fr"].includes(c.transcription?.language))
      lang.value = c.transcription.language;
    setup.append(
      button(tx("cand.suggest"), async () => {
        asrStatus.textContent = tx("cand.transcribing");
        try {
          const updated = await api(
            `/media/analyses/${analysis.id}/candidates/${c.id}/transcribe`,
            { method: "POST" },
          );
          transcript.value = updated.transcription.text;
          asrStatus.textContent = tx("cand.check");
          if (["de", "en", "fr"].includes(updated.transcription.language))
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
      document.createTextNode(tx("cand.speaker")),
    );
    const verifiedLabel = elem("label");
    verifiedLabel.className = "consent";
    verifiedLabel.append(
      verified,
      document.createTextNode(tx("cand.verified")),
    );
    const saveStatus = elem(
      "p",
      c.saved_reference_id ? tx("cand.already") : "",
      "status",
    );
    const save = button(tx("cand.save"), async () => {
      if (!speaker.checked) {
        error(
          saveStatus,
          Error(tx("cand.err.speaker")),
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
        saveStatus.textContent = tx("cand.saved.ok");
        $("media-message").className = "status";
        $("media-message").textContent = tx("cand.saved.msg", { name: v.name });
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
          button(tx("cand.use", { engine: names[engine] }), async () => {
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
    trimming.append(elem("summary", tx("cand.trim")));
    const range = elem("div");
    range.className = "row range-inputs";
    const start = elem("input"),
      end = elem("input");
    for (const [input, value, title] of [
      [start, c.start_seconds, tx("range.start")],
      [end, c.end_seconds, tx("range.end")],
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
      button(tx("cand.search"), async () => {
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
      elem("summary", tx("cand.score.sum")),
      elem(
        "p",
        tx("cand.score.help", { s: fixed(score, 1) }),
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
      progress.setAttribute("aria-label", tx("an.aria"));
      block.append(
        elem(
          "p",
          a.phase === "extracting_audio"
            ? tx("an.extract")
            : tx("an.search", { p: Math.round((a.progress || 0) * 100) }),
          "status",
        ),
        progress,
        button(tx("an.cancel"), async () => {
          await api("/media/analyses/" + a.id + "/cancel", { method: "POST" });
          await library();
        }),
      );
      return block;
    }
    if (a.error) block.append(elem("p", a.error.message, "error"));
    if (["paused", "cancelled", "failed"].includes(a.status))
      block.append(
        button(tx("an.resume"), async () => {
          await api("/media/analyses/" + a.id + "/resume", { method: "POST" });
          await library();
        }),
      );
    if (a.candidates.length) {
      block.append(
        elem("p", tx("an.hint"), "file-subheading"),
      );
      a.candidates
        .slice(0, 3)
        .forEach((c, i) => block.append(candidateCard(a, c, i)));
      if (a.candidates.length > 3) {
        const more = elem("details");
        more.append(
          elem("summary", tx("an.more", { n: a.candidates.length - 3 })),
        );
        a.candidates
          .slice(3)
          .forEach((c, i) => more.append(candidateCard(a, c, i + 3)));
        block.append(more);
      }
    } else
      block.append(
        elem("p", tx("an.none"), "muted"),
      );
    const manual = elem("details");
    manual.append(elem("summary", tx("an.manual")));
    const range = elem("div");
    range.className = "row range-inputs";
    const start = elem("input"),
      end = elem("input");
    for (const [input, value, title] of [
      [start, 0, tx("range.start")],
      [end, Math.min(10, a.total_seconds), tx("range.end")],
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
      button(tx("an.listen"), async () => {
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
                : tx("lib.import", {
                    p: fixed((100 * m.received_bytes) / m.size_bytes, 0),
                  })),
            "metrics",
          ),
        );
        if (m.error) section.append(elem("p", m.error.message, "error"));
        if (m.status !== "ready") continue;
        const tracks = elem("select");
        tracks.setAttribute("aria-label", tx("lib.track.aria", { f: m.filename }));
        for (const t of m.probe.audio_tracks) {
          const o = elem(
            "option",
            tx("lib.track", {
              i: t.index,
              lang: t.tags.language || tx("lib.nolang"),
              title: t.tags.title || "",
            }),
          );
          o.value = t.index;
          tracks.append(o);
        }
        if (m.probe.audio_tracks.length > 1) {
          const o = elem("option", tx("lib.pick"));
          o.value = "";
          tracks.prepend(o);
          tracks.value = "";
          section.append(
            elem("p", tx("lib.multi"), "field-help"),
          );
        } else tracks.hidden = true;
        section.append(tracks);
        if (!analyses.length || m.probe.audio_tracks.length > 1)
          section.append(
            button(tx("lib.auto"), async () => {
              if (tracks.value === "")
                throw Error(tx("lib.err.pick"));
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
            elem("summary", tx("lib.rerun")),
            button(tx("lib.new"), async () => {
              if (tracks.value === "") throw Error(tx("lib.err.pick2"));
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
                tx("lib.prev", { i: a.track_index, n: a.candidates.length }),
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
  // Sprachwechsel: erzeugte Inhalte neu aufbauen (die Stimmen kommen über "pps-voices" aus refresh()).
  document.addEventListener("pps-lang", () => {
    $("trial-results").replaceChildren();
    for (const section of $("media-library").children)
      section.dataset.signature = "";
    $("trial-message").textContent = "";
    Promise.all([library(), trialsHistory()]).catch((e) =>
      report("media-message", e),
    );
  });
  // Re-evaluate selected voice after the existing refresh populates its dropdown.
  new MutationObserver(() => {
    if ($("voice").value && $("voice").value !== lastVoice)
      referenceChoices().catch((e) => report("reference-choice-message", e));
  }).observe($("voice"), { childList: true });
  Promise.all([loadVoices(), library(), trialsHistory()]).catch((e) =>
    report("media-message", e),
  );
})();
