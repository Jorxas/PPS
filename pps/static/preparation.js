/* One import, automatic CPU preparation, one explicit listening confirmation. */
(() => {
  let timer = null,
    polling = false;
  function hideDuplicatePendingCards() {
    for (const card of document.querySelectorAll(".voice-card"))
      card.hidden =
        card.dataset.pending === "true" &&
        Array.from($("voice-preparations").children).some(
          (p) => p.dataset.voice === card.dataset.voice,
        );
  }
  const json = (method, body) => ({
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const action = (label, callback, primary = false) => {
    const b = elem("button", label, primary ? "" : "secondary");
    b.type = "button";
    b.onclick = async () => {
      b.disabled = true;
      try {
        await callback();
      } catch (e) {
        report("preparation-message", e);
      } finally {
        b.disabled = false;
      }
    };
    return b;
  };
  const name = (id) =>
    Array.from($("media-voice").options).find((o) => o.value === id)
      ?.textContent || tx("prep.voice");
  const location = (p) =>
    tx("prep.loc", {
      f: p.filename,
      t: `${Math.floor(p.start_seconds / 60)}:${Math.floor(p.start_seconds % 60)
        .toString()
        .padStart(2, "0")}`,
      d: fixed(p.end_seconds - p.start_seconds, 1),
    });

  window.startVoicePreparation = async (
    voiceId,
    mediaIds,
    trackIndices = {},
  ) => {
    const p = await api(
      "/voice-preparations",
      json("POST", {
        voice_id: voiceId,
        media_ids: mediaIds,
        track_indices: trackIndices,
      }),
    );
    $("preparation-message").replaceChildren();
    navigate("voices");
    await render(p);
    $("voice-preparations").scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
    await refreshPreparations();
    return p;
  };

  function alternatives(card, p, required = false) {
    const suggestions = p.suggestions || [];
    const box = required ? elem("div") : elem("details");
    if (!required) box.append(elem("summary", tx("prep.other")));
    if (!suggestions.length) {
      box.append(
        elem("p", tx("prep.none"), "field-help"),
      );
      card.append(box);
      return;
    }
    const select = elem("select");
    select.setAttribute("aria-label", tx("prep.sel.aria"));
    suggestions.forEach((c, i) => {
      const o = elem(
        "option",
        location(c) + (c.issues.length ? tx("prep.check") : ""),
      );
      o.value = i;
      select.append(o);
    });
    if (p.selection)
      select.value = String(
        suggestions.findIndex(
          (c) => c.candidate_id === p.selection.candidate_id,
        ),
      );
    if (!select.value) select.value = "0";
    const preview = elem("audio");
    preview.controls = true;
    preview.preload = "none";
    const update = () => {
      const c = suggestions[Number(select.value)];
      preview.src = `/v1/media/analyses/${c.analysis_id}/candidates/${c.candidate_id}/audio`;
    };
    select.onchange = update;
    update();
    box.append(
      select,
      preview,
      action(tx("prep.choose"), async () => {
        const c = suggestions[Number(select.value)];
        await api(
          "/voice-preparations/" + p.id + "/candidate",
          json("PUT", {
            analysis_id: c.analysis_id,
            candidate_id: c.candidate_id,
          }),
        );
        await refreshPreparations();
      }),
    );
    card.append(box);
  }

  async function render(p) {
    let card = document.querySelector('[data-preparation="' + p.id + '"]');
    if (p.status === "ready") {
      card?.remove();
      hideDuplicatePendingCards();
      return;
    }
    if (!card) {
      card = elem("article");
      card.className = "panel preparation-card";
      card.dataset.preparation = p.id;
      card.dataset.voice = p.voice_id;
      $("voice-preparations").append(card);
    }
    hideDuplicatePendingCards();
    const signature = JSON.stringify([
      p.status,
      p.selection?.candidate_id,
      p.transcription?.text,
      p.error,
      p.missing_track_media_ids,
    ]);
    // Poll progress without replacing the user's corrected words or chosen track.
    if (card.dataset.signature === signature) {
      const progress = card.querySelector("progress");
      if (progress) progress.value = p.progress;
      const label = card.querySelector(".preparation-progress");
      if (label)
        label.textContent = tx("prep.progress", {
          p: Math.round(p.progress * 100),
          c: p.files_completed,
          n: p.files_total,
        });
      return;
    }
    card.dataset.signature = signature;
    card.replaceChildren(elem("h2", name(p.voice_id)));
    if (["analyzing", "transcribing"].includes(p.status)) {
      card.append(
        elem(
          "p",
          p.status === "transcribing"
            ? tx("prep.transcribing")
            : tx("prep.analyzing"),
          "muted",
        ),
      );
      const progress = elem("progress");
      progress.max = 1;
      progress.value = p.progress;
      progress.setAttribute("aria-label", tx("prep.aria"));
      card.append(
        progress,
        elem(
          "p",
          tx("prep.progress", {
            p: Math.round(p.progress * 100),
            c: p.files_completed,
            n: p.files_total,
          }),
          "metrics preparation-progress",
        ),
        action(tx("prep.cancel"), async () => {
          await api("/voice-preparations/" + p.id + "/cancel", {
            method: "POST",
          });
          await refreshPreparations();
        }),
      );
    } else if (p.status === "needs_confirmation") {
      card.append(
        elem("p", tx("prep.almost"), "muted"),
      );
      const player = elem("audio");
      player.controls = true;
      player.preload = "metadata";
      player.src = `/v1/media/analyses/${p.selection.analysis_id}/candidates/${p.selection.candidate_id}/audio`;
      card.append(player);
      const words = elem("textarea");
      words.value = p.transcription?.text || "";
      words.maxLength = 5000;
      words.setAttribute("aria-label", tx("prep.words.aria"));
      words.placeholder = tx("prep.words.ph");
      const label = elem("label", tx("prep.words.label"));
      label.append(words);
      card.append(label);
      const language = elem("select");
      language.setAttribute("aria-label", tx("prep.lang.aria"));
      for (const value of ["de", "en", "fr"]) {
        const o = elem("option", langName(value));
        o.value = value;
        language.append(o);
      }
      language.value = ["de", "en", "fr"].includes(p.transcription?.language)
        ? p.transcription.language
        : getLang();
      const extra = elem("details");
      extra.append(elem("summary", tx("prep.lang.sum")), language);
      card.append(extra);
      if (
        p.selection.issues.length ||
        !["de", "en", "fr"].includes(p.transcription?.language) ||
        p.transcription?.segments?.some((s) =>
          s.words?.some((w) => w.probability != null && w.probability < 0.6),
        )
      )
        card.append(
          elem("p", tx("prep.uncertain"), "notice"),
        );
      card.append(
        elem("p", tx("prep.confirm.help"), "field-help"),
        action(
          tx("prep.confirm"),
          async () => {
            if (!words.value.trim())
              throw Error(tx("prep.err.words"));
            const v = await api(
              "/voice-preparations/" + p.id + "/confirm",
              json("POST", {
                speaker_confirmed: true,
                transcription: words.value,
                transcription_verified: true,
                language: language.value,
              }),
            );
            await refresh();
            $("voice").value = v.id;
            $("voice").dispatchEvent(new Event("change"));
            navigate("synthesis");
            $("message").textContent = tx("prep.saved", { name: v.name });
            $("text").focus();
            card.remove();
            await refreshPreparations();
          },
          true,
        ),
      );
      alternatives(card, p);
    } else if (p.status === "needs_track") {
      card.append(
        elem("p", tx("prep.needs_track"), "muted"),
      );
      const sources = (await api("/media")).media,
        choices = {};
      for (const id of p.missing_track_media_ids) {
        const source = sources.find((m) => m.id === id),
          select = elem("select");
        select.setAttribute("aria-label", tx("prep.track.aria", { f: source.filename }));
        const empty = elem("option", tx("prep.track.pick"));
        empty.value = "";
        select.append(empty);
        for (const t of source.probe.audio_tracks) {
          const o = elem(
            "option",
            tx("prep.track.opt", {
              i: t.index,
              lang: t.tags?.language || "",
              title: t.tags?.title || "",
            }),
          );
          o.value = t.index;
          select.append(o);
        }
        const label = elem("label", source.filename);
        label.append(select);
        card.append(label);
        choices[id] = select;
      }
      card.append(
        action(
          tx("prep.continue"),
          async () => {
            if (Object.values(choices).some((s) => s.value === ""))
              throw Error(tx("prep.err.track"));
            await window.startVoicePreparation(p.voice_id, p.media_ids, {
              ...p.track_indices,
              ...Object.fromEntries(
                Object.entries(choices).map(([id, s]) => [id, Number(s.value)]),
              ),
            });
          },
          true,
        ),
      );
    } else if (p.status === "needs_selection") {
      card.append(
        elem("p", tx("prep.needs_sel"), "muted"),
      );
      alternatives(card, p, true);
    } else {
      card.append(
        elem(
          "p",
          p.status === "cancelled"
            ? tx("prep.cancelled")
            : tx("prep.interrupted"),
          "muted",
        ),
      );
      if (p.error) card.append(elem("p", p.error.message, "error"));
      card.append(
        action(
          tx("prep.resume"),
          async () => {
            await api("/voice-preparations/" + p.id + "/resume", {
              method: "POST",
            });
            await refreshPreparations();
          },
          true,
        ),
      );
    }
  }

  async function refreshPreparations() {
    clearTimeout(timer);
    if (polling) {
      timer = setTimeout(refreshPreparations, 500);
      return;
    }
    polling = true;
    try {
      const d = await api("/voice-preparations"),
        latest = new Map();
      for (const p of d.preparations) latest.set(p.voice_id, p);
      for (const card of Array.from($("voice-preparations").children))
        if (
          !Array.from(latest.values()).some(
            (p) => p.id === card.dataset.preparation,
          )
        )
          card.remove();
      for (const p of latest.values()) await render(p);
      if (
        d.preparations.some((p) =>
          ["analyzing", "transcribing"].includes(p.status),
        )
      )
        timer = setTimeout(refreshPreparations, 2000);
    } catch (e) {
      report("preparation-message", e);
    } finally {
      polling = false;
    }
  }
  document.addEventListener("pps-preparation-refresh", () =>
    refreshPreparations(),
  );
  document.addEventListener("pps-voice-cards", hideDuplicatePendingCards);
  // Sprachwechsel: die Karten neu aufbauen (die Signatur verhindert sonst das Ersetzen).
  document.addEventListener("pps-lang", () => {
    for (const card of document.querySelectorAll("[data-preparation]"))
      card.dataset.signature = "";
    refreshPreparations();
  });
  refreshPreparations();
})();
