// Öffentliche Demo: Die echte PPS-Oberfläche (pps/web.html, i18n.js, app.js, media.js, preparation.js, app.css) läuft unverändert.
// Dieses Modul ersetzt nur den lokalen Dienst: fetch("/v1/...") wird mit erfundenen Fällen beantwortet, und Audioquellen
// werden auf kurze synthetische Testsignale (Tonfolgen, keine Sprache) umgeleitet. Es gibt keine Modelle, keine Uploads,
// keine Netzwerkanfragen. Ein Neuladen setzt die Demo zurück.
// Zweisprachig: Deutsch ist Standard, Französisch (die ursprüngliche Sprache der Oberfläche) wählbar über den Schalter
// DE | FR im Demo-Hinweis oder ?lang=fr. Alle erfundenen Daten und Meldungen liegen in beiden Sprachen vor.
(() => {
  "use strict";

  // ---------------------------------------------------------------- Sprache
  const sprache = () => (typeof window.getLang === "function" ? window.getLang() : "de");
  // L(de, fr) markiert einen zweisprachigen Wert; er wird erst bei der Antwort in der aktuellen Sprache aufgelöst.
  const L = (de, fr) => ({ __l: [de, fr] });
  const lokal = (wert) => {
    if (Array.isArray(wert)) return wert.map(lokal);
    if (wert && typeof wert === "object") {
      if (wert.__l) return wert.__l[sprache() === "fr" ? 1 : 0];
      return Object.fromEntries(Object.entries(wert).map(([k, v]) => [k, lokal(v)]));
    }
    return wert;
  };
  const kopie = lokal; // tiefe Kopie, in der alle L(...) aufgelöst sind
  const S = (wert) => lokal(wert);

  // Meldungen und Hinweise der Demo: [de, fr].
  const M = {
    keinModell: ["Öffentliche Demo: Es ist keine Sprach-Engine installiert, eine echte Synthese ist hier nicht verfügbar. Die Beispiele stehen unter „Ihre Ergebnisse“ und im Verlauf.", "Démo publique : aucun moteur vocal n’est installé, la synthèse réelle n’est pas disponible ici. Les exemples sont dans « Vos résultats » et dans l’historique."],
    keinImport: ["Öffentliche Demo: Import und Analyse von Dateien benötigen den lokalen PPS-Dienst. Es wird nichts gesendet.", "Démo publique : l’import et l’analyse de fichiers nécessitent le service PPS local. Rien n’est envoyé."],
    keinDienst: ["Öffentliche Demo: Diese Funktion benötigt den lokalen PPS-Dienst.", "Démo publique : cette fonction nécessite le service PPS local."],
    keinAsr: ["Öffentliche Demo: Die automatische Transkription ist nicht installiert. Geben Sie die Wörter von Hand ein.", "Démo publique : la transcription automatique n’est pas installée. Saisissez les mots à la main."],
    demoCode: ["DEMO_NICHT_VERFUEGBAR", "DEMO_INDISPONIBLE"],
    unbekannt: ["Diese Ressource gibt es in der Demo nicht.", "Cette ressource n’existe pas dans la démo."],
    stimmeFehlt: ["Stimme nicht gefunden.", "Voix introuvable."],
    referenzFehlt: ["Referenz nicht gefunden.", "Référence introuvable."],
    auftragFehlt: ["Auftrag nicht gefunden.", "Tâche introuvable."],
    vergleichFehlt: ["Vergleich nicht gefunden.", "Comparaison introuvable."],
    audioFehlt: ["Audio nicht gefunden.", "Audio introuvable."],
    vorbFehlt: ["Vorbereitung nicht gefunden.", "Préparation introuvable."],
    vorbAuswahlFehlt: ["Vorbereitung oder Ausschnitt nicht gefunden.", "Préparation ou extrait introuvable."],
    ausschnittFehlt: ["Ausschnitt nicht gefunden.", "Extrait introuvable."],
    dienstName: ["PPS (öffentliche Demo)", "PPS (démo publique)"],
    rahmen: ["Öffentliche Demo: die echte Oberfläche von PPS mit erfundenen Fällen (Alex Beispiel, Mara Muster, Jonas Probe). Zahlen, Dauern und Bewertungen sind Beispiele. Die Audios sind Testsignale, keine Sprache. Es wird keine Synthese ausgeführt und nichts gesendet; ein Neuladen setzt die Demo zurück.", "Démo publique : l’interface réelle de PPS, avec des cas fictifs (Alex Beispiel, Mara Muster, Jonas Probe). Chiffres, durées et notes sont des exemples. Les audios sont des signaux de test, pas de la parole. Aucune synthèse n’est exécutée et rien n’est envoyé ; recharger la page remet la démo à zéro."],
    synthese: ["Demo: Die echte Synthese ist nicht verfügbar, es ist keine Sprach-Engine installiert. Die Ergebnisse unten sind erfundene Beispiele.", "Démo : la synthèse réelle n’est pas disponible, aucun moteur vocal n’est installé. Les résultats ci-dessous sont des exemples fictifs."],
    import: ["Demo: Der Dateiimport ist deaktiviert. Es wird keine Datei gelesen oder gesendet. Der Fall „Jonas Probe“ oben ist vorbereitet und kann bestätigt werden.", "Démo : l’import de fichiers est désactivé. Aucun fichier n’est lu ni envoyé. Le cas de « Jonas Probe » ci-dessus est préparé et peut être confirmé."],
    importAusschnitt: ["Demo: Der Import eines Ausschnitts ist deaktiviert.", "Démo : l’import d’un extrait est désactivé."],
    dienst: ["Demo: Es ist kein lokaler Dienst verbunden. Die Werte unten sind Beispiele; die beiden Engines sind nicht installiert.", "Démo : aucun service local n’est connecté. Les valeurs ci-dessous sont des exemples ; les deux moteurs ne sont pas installés."],
    deaktiviert: ["In der öffentlichen Demo deaktiviert", "Désactivé dans la démo publique"],
    docs: ["Die API-Dokumentation liefert der lokale PPS-Dienst; sie ist nicht Teil der Demo.", "La documentation de l’API est fournie par le service PPS local ; elle n’est pas incluse dans la démo."],
    mp3: ["Demo: Nur das Testsignal ist verfügbar, im WAV-Format.", "Démo : seul le signal de test est disponible, au format WAV."],
    signalName: ["testsignal-", "signal-de-test-"],
    textA: ["Guten Tag und herzlich willkommen. Hier ist ein kurzer Text, um zwei Sprach-Engines zu vergleichen.", "Bonjour et bienvenue. Voici un court texte pour comparer deux moteurs vocaux."],
    wortMethode: ["Beispielwert", "exemple fictif"],
    wortUnsicher: ["Beispielwerte", "valeurs d’exemple"],
    wortKorrektur: ["manuelle Korrektur (Beispiel)", "correction manuelle (exemple)"],
    wortSchaetzung: ["menschliche Schätzung", "estimation humaine"],
  };
  const m = (schluessel) => M[schluessel][sprache() === "fr" ? 1 : 0];
  const ML = (schluessel) => L(M[schluessel][0], M[schluessel][1]);

  // ---------------------------------------------------------------- Zeit und Hilfen
  const jetzt = () => Date.now() / 1000;
  const vor = (tage, stunden = 0) => jetzt() - tage * 86400 - stunden * 3600;
  const runde = (zahl, stellen = 3) => Math.round(zahl * 10 ** stellen) / 10 ** stellen;

  // Wortgrenzen (Beispielwerte): gleichmäßig nach Wortlänge über die Dauer verteilt. Sie gehören zum Testsignal, nicht
  // zu gesprochener Sprache, und sind als solche beschriftet.
  function woerter(text, dauer, methode = m("wortMethode"), status = sprache() === "fr" ? "incertain" : "unsicher", unsicherheit = [m("wortUnsicher")]) {
    const teile = text.split(/\s+/).filter(Boolean);
    const gewicht = teile.map((t) => t.length + 2);
    const summe = gewicht.reduce((a, b) => a + b, 0);
    const von = 0.2, bis = Math.max(von + 0.5, dauer - 0.25);
    let t = von;
    return teile.map((wort, i) => {
      const laenge = ((bis - von) * gewicht[i]) / summe;
      const start = t;
      t += laenge;
      return { text: wort, start: runde(start), end: runde(start + laenge * 0.9), method: methode, confidence_status: status, uncertainty: [...unsicherheit] };
    });
  }

  // ---------------------------------------------------------------- Erfundene Fälle
  const stimmen = [
    {
      id: "voix-alex", name: "Alex Beispiel", language: L("de", "fr"), version: 2, created_at: vor(6),
      references: [
        {
          id: "ref-alex-1", filename: L("alex-lesung-01.wav", "alex-lecture-01.wav"), language: L("de", "fr"), transcription_verified: true,
          transcription: L("Guten Tag, ich heiße Alex und stelle Ihnen ein kurzes Beispiel für eine Erzählung vor.", "Bonjour, je m’appelle Alex et je vous présente un court exemple de narration."),
          pcm: { duration_seconds: 6.4 }, authorization: { granted: true },
        },
        {
          id: "ref-alex-2", filename: L("alex-lesung-02.wav", "alex-lecture-02.wav"), language: L("de", "fr"), transcription_verified: true,
          transcription: L("Das Sprachstudio behält Ihre Dateien auf diesem Computer und bereitet jede Stimme nur ein einziges Mal vor.", "Le studio vocal garde vos fichiers sur cet ordinateur et prépare chaque voix une seule fois."),
          pcm: { duration_seconds: 7.8 }, authorization: { granted: true },
          media_metrics: { score: 84.5, consistency_with_saved_voice_cosine: 0.912 },
        },
      ],
      active_reference_id: "ref-alex-1", engine_reference_ids: { cosyvoice: "ref-alex-2" },
    },
    {
      id: "voix-mara", name: "Mara Muster", language: "en", version: 1, created_at: vor(3),
      references: [
        {
          id: "ref-mara-1", filename: "mara-reading.wav", language: "en", transcription_verified: false,
          transcription: "Good morning, this is a short example sentence for a test recording.",
          pcm: { duration_seconds: 5.9 }, authorization: { granted: true },
        },
      ],
      active_reference_id: "ref-mara-1", engine_reference_ids: {},
    },
    { id: "voix-jonas", name: "Jonas Probe", language: L("de", "fr"), version: 0, created_at: vor(1), references: [], active_reference_id: null, engine_reference_ids: {} },
  ];

  // Audio-Ergebnisse: id -> Metadaten (Text, Dauer, Prüfung). Die Wortgrenzen werden bei Bedarf in der Sprache berechnet.
  const audios = {};
  function audioAnlegen(id, voiceId, referenceId, text, dauer, engine, optionen, pruefung) {
    audios[id] = {
      id, voice_id: voiceId, reference_id: referenceId, text, engine, alignment_revision: 1,
      pcm: { duration_seconds: dauer, sha256: L("beispiel-" + id, "exemple-" + id) },
      metrics: {},
      effective_engine_options: optionen,
      audio_quality: { true_peak_dbfs: -2.1, rails: { rail_sample_count: 0, clipping_evidence: false } },
      pruefung: pruefung || null,
    };
  }
  const woerterFuer = (a) => {
    const liste = woerter(S(a.text), a.pcm.duration_seconds);
    for (const c of a.pruefung?.corrections || []) {
      const w = liste[c.word_index];
      if (w) Object.assign(w, { start: c.start, end: c.end, method: m("wortKorrektur"), confidence_status: "human_corrected", uncertainty: [m("wortSchaetzung")] });
    }
    return liste;
  };
  const textA = ML("textA");
  const textB = L("Wir treffen uns morgen um neun Uhr im Besprechungsraum.", "Rendez-vous demain à neuf heures dans la salle de réunion.");
  audioAnlegen("audio-demo-1", "voix-alex", "ref-alex-1", textA, 5.6, "chatterbox", { exaggeration: 0.5, cfg_weight: 0.5, temperature: 0.8 }, null);
  audioAnlegen("audio-demo-2", "voix-alex", "ref-alex-2", textA, 6.1, "cosyvoice", { speed: 1 }, null);
  audioAnlegen("audio-demo-4", "voix-alex", "ref-alex-1", textB, 4.2, "chatterbox", { exaggeration: 0.5, cfg_weight: 0.5, temperature: 0.8 }, {
    ratings: { fidelity: 4, naturalness: 4, pronunciation: 5, expressiveness: 3, artifacts: 5, audio_quality: 4 },
    notes: L("Fiktives Beispiel: gute Aussprache, Rhythmus etwas gleichförmig.", "Exemple fictif : bonne prononciation, rythme un peu uniforme."), acoustically_confirmed: true, corrections: [],
  });

  const metrik = (synthese, ram, vram) => ({
    synthesis_seconds: synthese, real_time_factor: runde(synthese / 5.6, 2), load_seconds: 8.2, queue_seconds: 0.1, alignment_seconds: 2.3,
    worker_peak_rss_bytes_observed: ram * 1024 ** 3, cuda_peak_allocated_bytes: vram * 1024 ** 3, synthesis_cache_hit: false,
  });
  const auftrag = (id, engine, status, erstellt, voiceId, referenceId, text, sprachcode, extra = {}) => ({
    id, status, created_at: erstellt, audio_id: null, error: null, metrics: {}, cancel_requested: false, comparison_id: null,
    request: { engine, voice_id: voiceId, reference_id: referenceId, text, options: {}, language: sprachcode, seed: 1986 }, ...extra,
  });
  const deFr = L("de", "fr");
  const auftraege = [
    auftrag("job-demo-1", "chatterbox", "completed", vor(3), "voix-alex", "ref-alex-1", textA, deFr, { comparison_id: "cmp-demo-1", audio_id: "audio-demo-1", metrics: metrik(3.4, 5.1, 3.9) }),
    auftrag("job-demo-2", "cosyvoice", "completed", vor(3), "voix-alex", "ref-alex-2", textA, deFr, { comparison_id: "cmp-demo-1", audio_id: "audio-demo-2", metrics: metrik(4.2, 6.3, 4.4) }),
    auftrag("job-demo-3", "chatterbox", "failed", vor(2), "voix-mara", "ref-mara-1", "This test sentence could not be generated because of a resource limit.", "en", {
      error: { code: "RESOURCES_INSUFFICIENT", message: L("Nicht genügend freier Grafikspeicher, um Chatterbox zu laden (5 GiB erforderlich). Schließen Sie andere Programme und versuchen Sie es erneut.", "Mémoire graphique libre insuffisante pour charger Chatterbox (5 Gio nécessaires). Fermez d’autres programmes puis réessayez."), retryable: true },
    }),
    auftrag("job-demo-4", "chatterbox", "completed", vor(1), "voix-alex", "ref-alex-1", textB, deFr, { audio_id: "audio-demo-4", metrics: metrik(2.9, 5.0, 3.8) }),
    auftrag("job-demo-5", "cosyvoice", "cancelled", vor(0, 2), "voix-alex", "ref-alex-2", L("Text abgebrochen, bevor die Engine fertig geladen war.", "Texte annulé avant la fin du chargement du moteur."), deFr),
  ];
  const vergleiche = [{
    id: "cmp-demo-1", created_at: vor(3), job_ids: ["job-demo-1", "job-demo-2"], text: textA, voice_id: "voix-alex", reference_id: "ref-alex-1",
    engine_reference_ids: { chatterbox: "ref-alex-1", cosyvoice: "ref-alex-2" }, same_reference: false,
  }];

  // Medien, Analyse und Vorbereitung für „Jonas Probe“: ein bereits vorliegender Fall, der bestätigt werden kann.
  const kandidatText = L("Hallo zusammen, heute erkläre ich Ihnen, wie unser kleines Studio funktioniert.", "Bonjour à tous, aujourd’hui je vais vous expliquer comment fonctionne notre petit studio.");
  const interviewName = L("interview-beispiel.mp4", "interview-exemple.mp4");
  const quelleKennung = L("beispiel-quelle", "exemple-quelle");
  const kandidaten = [
    { id: "kand-1", start_seconds: 192.4, end_seconds: 200.9, metrics: { score: 88.2, speaker_status: "single", music_status: "clear", noise_status: "clear", echo_status: "clear" }, source_sha256: quelleKennung, track_index: 1, filename: interviewName, transcription: { text: kandidatText, language: deFr } },
    { id: "kand-2", start_seconds: 341.0, end_seconds: 349.2, metrics: { score: 71.6, speaker_status: "suspected_multiple", music_status: "clear", noise_status: "suspected_noise", echo_status: "clear" }, source_sha256: quelleKennung, track_index: 1, filename: interviewName },
    { id: "kand-3", start_seconds: 47.5, end_seconds: 55.0, metrics: { score: 64.0, speaker_status: "single", music_status: "suspected_music", noise_status: "clear", echo_status: "clear" }, source_sha256: quelleKennung, track_index: 1, filename: interviewName },
  ];
  const medien = {
    media: [{
      id: "media-demo-1", voice_id: "voix-jonas", status: "ready", filename: interviewName, size_bytes: 48234496, received_bytes: 48234496,
      probe: { duration_seconds: 612, audio_tracks: [{ index: 1, tags: { language: L("deu", "fra"), title: L("Hauptspur", "Piste principale") } }] },
    }],
    analyses: [{
      id: "analyse-demo-1", media_id: "media-demo-1", voice_id: "voix-jonas", status: "completed", track_index: 1, phase: "finished",
      processed_seconds: 612, total_seconds: 612, progress: 1, error: null, candidates: kandidaten,
    }],
  };
  const vorbereitung = {
    id: "prep-demo-1", voice_id: "voix-jonas", media_ids: ["media-demo-1"], track_indices: { "media-demo-1": 1 }, missing_track_media_ids: [],
    analysis_ids: ["analyse-demo-1"], status: "needs_confirmation", progress: 1, files_completed: 1, files_total: 1, error: null,
    selection: { analysis_id: "analyse-demo-1", candidate_id: "kand-1", filename: interviewName, start_seconds: 192.4, end_seconds: 200.9, score: 88.2, issues: [] },
    transcription: { text: kandidatText, language: deFr, verified: false },
    suggestions: [],
  };
  vorbereitung.suggestions = [
    vorbereitung.selection,
    { analysis_id: "analyse-demo-1", candidate_id: "kand-2", filename: interviewName, start_seconds: 341.0, end_seconds: 349.2, score: 71.6, issues: [L("mehrere Stimmen vermutet", "plusieurs voix suspectées")] },
  ];
  const vorbereitungen = [vorbereitung];

  // ---------------------------------------------------------------- Antworten des „Dienstes“
  const NICHT_VERFUEGBAR = (schluessel) => ({ status: 503, daten: { error: { code: m("demoCode"), message: m(schluessel) } } });
  const fehler = (status, code, schluessel) => ({ status, daten: { error: { code, message: m(schluessel) } } });

  const stimme = (id) => stimmen.find((v) => v.id === id || v.name.toLowerCase() === String(id).toLowerCase());
  const auftragOeffentlich = (j) => kopie(j);
  const vergleich = (c) => {
    const kinder = c.job_ids.map((id) => auftraege.find((j) => j.id === id));
    const alle = kinder.every((j) => j.status === "completed");
    return { ...kopie(c), status: alle ? "completed" : "partial", jobs: kopie(kinder) };
  };
  const metadaten = (a) => {
    const { pruefung: _p, engine: _e, ...rest } = a;
    return kopie(rest);
  };
  function wirksameAusrichtung(a) {
    const geprueft = a.pruefung?.acoustically_confirmed;
    return {
      status: geprueft ? "human_reviewed" : "asr_unverified", mainclip_compatible: !!geprueft,
      quality: geprueft ? "human_attested_word_boundaries" : "automatic_unverified", words: woerterFuer(a),
    };
  }

  function schaetzung(body) {
    // Wie der Dienst: ohne eigenen Wert für die Sprache gilt der französische Wert der Engine.
    const wpm = { chatterbox: { fr: 123, en: 135 }, cosyvoice: { fr: 113, en: 125 } };
    const text = body.text || "";
    const bereinigt = text.replace(/[^\p{L}\p{N}_\s'’-]/gu, " ");
    const anzahl = bereinigt.split(/\s+/).filter((w) => w.trim()).length;
    const basis = wpm[body.engine]?.[body.language] ?? wpm[body.engine]?.fr ?? (body.language === "en" ? 160 : 150);
    const effektiv = basis * Math.max(0.5, Math.min(2, body.speed || 1));
    const saetze = (text.match(/[.!?…]+/g) || []).length, absaetze = (text.match(/\n\s*\n/g) || []).length;
    const erwartet = (anzahl / effektiv) * 60 + saetze * 0.35 + absaetze * 0.5;
    const r1 = (x) => Math.round(x * 10) / 10;
    const est = {
      word_count: anzahl, char_count: text.trim().length, sentence_count: saetze, paragraph_count: absaetze,
      estimated_duration_seconds: r1(erwartet), estimated_duration_min_seconds: r1(erwartet * 0.8), estimated_duration_max_seconds: r1(erwartet * 1.25),
      effective_wpm: r1(effektiv), engine: body.engine, language: body.language, speed: body.speed || 1,
    };
    const ziel = body.target_duration_seconds;
    if (!ziel) return est;
    const ist = est.estimated_duration_seconds, verhaeltnis = Math.round((ist / ziel) * 100) / 100;
    const de = sprache() !== "fr", z = (x) => (de ? String(x).replace(".", ",") : String(x));
    let status = "compatible";
    let meldung = de ? `Die geschätzte Dauer (${z(ist)} s) passt zur Zielvorgabe (${z(ziel)} s).` : `La durée estimée (${ist}s) est compatible avec la cible (${ziel}s).`;
    if (verhaeltnis < 0.85) { status = "too_short"; meldung = de ? `Das Skript ist zu kurz, um ${z(ziel)} s zu erreichen (geschätzt ${z(ist)} s, Verhältnis ${z(verhaeltnis)}).` : `Le script est trop court pour atteindre ${ziel}s (estimé à ${ist}s, ratio ${verhaeltnis}).`; }
    else if (verhaeltnis > 1.15) { status = "too_long"; meldung = de ? `Das Skript ist zu lang für ${z(ziel)} s (geschätzt ${z(ist)} s, Verhältnis ${z(verhaeltnis)}).` : `Le script est trop long pour ${ziel}s (estimé à ${ist}s, ratio ${verhaeltnis}).`; }
    return { ...est, target_duration_seconds: ziel, duration_difference_seconds: r1(ist - ziel), duration_ratio: verhaeltnis, duration_status: status, advice: { message: meldung } };
  }

  function referenzHinzufuegen(voiceId, dateiname, text, sprachcode, dauer) {
    const v = stimme(voiceId);
    const ref = { id: "ref-" + voiceId.replace("voix-", "") + "-" + (v.references.length + 1), filename: dateiname, language: sprachcode, transcription: text, transcription_verified: true, pcm: { duration_seconds: dauer }, authorization: { granted: true } };
    v.references.push(ref);
    v.version += 1;
    v.active_reference_id = ref.id;
    return v;
  }

  function antwort(pfad, methode, body) {
    let mm;
    if (pfad === "/health" && methode === "GET") {
      // Beispielwerte: kein lokaler Dienst, keine Grafikkarte.
      return { daten: { status: "demo", service: m("dienstName"), resources: { ram_available_gib: 18.4, disk_free_gib: 212, gpu: null } } };
    }
    if (pfad === "/engines" && methode === "GET") {
      const engine = (id, name) => ({ id, name, ready_to_attempt_load: false, model_files_available: false, imports_verified: false });
      return { daten: { engines: [engine("chatterbox", "Chatterbox"), engine("cosyvoice", "CosyVoice")], resource_thresholds: { min_ram_gib: 6, min_vram_gib: 5 } } };
    }
    if (pfad === "/duration-estimate" && methode === "POST") return { daten: schaetzung(body) };
    if (pfad === "/voices" && methode === "GET") return { daten: { voices: kopie(stimmen) } };
    if (pfad === "/voices" && methode === "POST") return NICHT_VERFUEGBAR("keinImport");
    if ((mm = /^\/voices\/([^/]+)$/.exec(pfad)) && methode === "GET") {
      const v = stimme(decodeURIComponent(mm[1]));
      return v ? { daten: kopie(v) } : fehler(404, "VOICE_UNKNOWN", "stimmeFehlt");
    }
    if ((mm = /^\/voices\/([^/]+)\/references\/([^/]+)\/alignment$/.exec(pfad)) && methode === "GET") {
      const ref = stimme(mm[1])?.references.find((r) => r.id === mm[2]);
      if (!ref) return fehler(404, "REFERENCE_UNKNOWN", "referenzFehlt");
      // Nur die Alex-Referenzen haben Beispielgrenzen; Mara bleibt ohne Ausrichtung.
      const text = S(ref.transcription);
      return { daten: mm[1] === "voix-mara" || !text ? { words: [], status: "not_available" } : { status: "asr_unverified", timestamp_provenance: m("wortMethode"), words: woerter(text, ref.pcm.duration_seconds) } };
    }
    if ((mm = /^\/voices\/([^/]+)\/references\/([^/]+)\/transcription$/.exec(pfad)) && methode === "PUT") {
      const v = stimme(mm[1]), ref = v?.references.find((r) => r.id === mm[2]);
      if (!ref) return fehler(404, "REFERENCE_UNKNOWN", "referenzFehlt");
      ref.transcription = body.transcription;
      ref.transcription_verified = !!body.verified;
      v.version += 1;
      return { daten: kopie(ref) };
    }
    if ((mm = /^\/voices\/([^/]+)\/engine-reference$/.exec(pfad)) && methode === "PUT") {
      const v = stimme(mm[1]);
      if (!v) return fehler(404, "VOICE_UNKNOWN", "stimmeFehlt");
      v.engine_reference_ids = { ...v.engine_reference_ids, [body.engine]: body.reference_id };
      return { daten: kopie(v) };
    }
    if ((pfad === "/syntheses" || pfad === "/comparisons" || pfad === "/reference-trials" || pfad === "/tts/synthesize") && methode === "POST") return NICHT_VERFUEGBAR("keinModell");
    if (pfad === "/jobs" && methode === "GET") return { daten: { jobs: auftraege.map(auftragOeffentlich) } };
    if ((mm = /^\/jobs\/([^/]+)$/.exec(pfad)) && methode === "GET") {
      const j = auftraege.find((x) => x.id === mm[1]);
      return j ? { daten: auftragOeffentlich(j) } : fehler(404, "JOB_UNKNOWN", "auftragFehlt");
    }
    if (pfad === "/comparisons" && methode === "GET") return { daten: { comparisons: vergleiche.map(vergleich) } };
    if ((mm = /^\/comparisons\/([^/]+)$/.exec(pfad)) && methode === "GET") {
      const c = vergleiche.find((x) => x.id === mm[1]);
      return c ? { daten: vergleich(c) } : fehler(404, "COMPARISON_UNKNOWN", "vergleichFehlt");
    }
    if (pfad === "/reference-trials" && methode === "GET") return { daten: { trials: [] } };
    if ((mm = /^\/audio\/([^/]+)\/metadata$/.exec(pfad)) && methode === "GET") {
      const a = audios[mm[1]];
      return a ? { daten: metadaten(a) } : fehler(404, "AUDIO_UNKNOWN", "audioFehlt");
    }
    if ((mm = /^\/audio\/([^/]+)\/review$/.exec(pfad))) {
      const a = audios[mm[1]];
      if (!a) return fehler(404, "AUDIO_UNKNOWN", "audioFehlt");
      if (methode === "GET") return { daten: { latest: a.pruefung ? kopie(a.pruefung) : null, history: [], alignment_revision: a.alignment_revision, effective_alignment: wirksameAusrichtung(a) } };
      if (methode === "POST") {
        a.pruefung = { ratings: body.ratings, notes: body.notes, acoustically_confirmed: !!body.acoustically_confirmed, corrections: body.corrections || [] };
        return { status: 201, daten: { id: "review-" + a.id, audio_id: a.id, ...kopie(a.pruefung) } };
      }
    }
    if (pfad === "/media" && methode === "GET") return { daten: kopie(medien) };
    if (pfad === "/voice-preparations" && methode === "GET") return { daten: { preparations: kopie(vorbereitungen) } };
    if (pfad === "/voice-preparations" && methode === "POST") {
      // Der vorbereitete Fall von „Jonas Probe“ liegt bereits vor; es wird nichts neu analysiert.
      const vorhanden = vorbereitungen.find((p) => p.voice_id === body.voice_id);
      return vorhanden ? { status: 202, daten: kopie(vorhanden) } : NICHT_VERFUEGBAR("keinImport");
    }
    if ((mm = /^\/voice-preparations\/([^/]+)\/candidate$/.exec(pfad)) && methode === "PUT") {
      const p = vorbereitungen.find((x) => x.id === mm[1]);
      const s = p?.suggestions.find((c) => c.candidate_id === body.candidate_id);
      if (!s) return fehler(404, "PREPARATION_UNKNOWN", "vorbAuswahlFehlt");
      p.selection = s;
      p.transcription = { text: s.candidate_id === "kand-1" ? kandidatText : "", language: deFr, verified: false };
      return { daten: kopie(p) };
    }
    if ((mm = /^\/voice-preparations\/([^/]+)\/confirm$/.exec(pfad)) && methode === "POST") {
      const p = vorbereitungen.find((x) => x.id === mm[1]);
      if (!p) return fehler(404, "PREPARATION_UNKNOWN", "vorbFehlt");
      const dauer = runde(p.selection.end_seconds - p.selection.start_seconds, 1);
      const sprachcode = body.language || sprache();
      const v = referenzHinzufuegen(p.voice_id, L("interview-beispiel-ausschnitt.wav", "interview-exemple-extrait.wav"), body.transcription, sprachcode, dauer);
      v.language = sprachcode;
      vorbereitungen.splice(vorbereitungen.indexOf(p), 1);
      return { status: 201, daten: kopie(v) };
    }
    if ((mm = /^\/media\/analyses\/([^/]+)\/candidates\/([^/]+)\/reference$/.exec(pfad)) && methode === "POST") {
      const k = kandidaten.find((c) => c.id === mm[2]);
      const analyse = medien.analyses.find((a) => a.id === mm[1]);
      if (!k || !analyse) return fehler(404, "CANDIDATE_UNKNOWN", "ausschnittFehlt");
      const v = referenzHinzufuegen(analyse.voice_id, L("interview-beispiel-" + k.id + ".wav", "interview-exemple-" + k.id + ".wav"), body.transcription, body.language || sprache(), runde(k.end_seconds - k.start_seconds, 1));
      k.saved_reference_id = v.active_reference_id;
      return { status: 201, daten: kopie(v) };
    }
    if (/\/transcribe$/.test(pfad) && methode === "POST") return NICHT_VERFUEGBAR("keinAsr");
    if (methode !== "GET") return NICHT_VERFUEGBAR("keinDienst");
    return fehler(404, "DEMO_UNBEKANNT", "unbekannt");
  }

  // Die Oberfläche startet ihre Abfragen schon in app.js, bevor media.js und preparation.js geladen sind. Ein lokaler
  // Dienst antwortet nie im selben Moment; deshalb wartet die Demo bis alle Skripte laufen und antwortet dann mit
  // einer kleinen Verzögerung.
  const skripteBereit = new Promise((fertig) => {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fertig, { once: true });
    else fertig();
  });
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (eingabe, optionen = {}) => {
    const adresse = new URL(typeof eingabe === "string" ? eingabe : eingabe instanceof URL ? eingabe.href : eingabe.url, location.href);
    if (!adresse.pathname.startsWith("/v1/")) return originalFetch(eingabe, optionen);
    await skripteBereit;
    await new Promise((fertig) => setTimeout(fertig, 15));
    const methode = (optionen.method || "GET").toUpperCase();
    let body = {};
    if (typeof optionen.body === "string" && optionen.body) { try { body = JSON.parse(optionen.body); } catch (_) { body = {}; } }
    const a = antwort(adresse.pathname.slice(3), methode, body);
    return new Response(JSON.stringify(a.daten), { status: a.status || 200, headers: { "Content-Type": "application/json" } });
  };

  // ---------------------------------------------------------------- Testsignale statt Sprache
  function wavBlob(dauer, worte) {
    const rate = 16000, n = Math.max(1, Math.floor(dauer * rate)), daten = new DataView(new ArrayBuffer(44 + n * 2));
    const text = (pos, s) => [...s].forEach((c, i) => daten.setUint8(pos + i, c.charCodeAt(0)));
    text(0, "RIFF"); daten.setUint32(4, 36 + n * 2, true); text(8, "WAVEfmt "); daten.setUint32(16, 16, true);
    daten.setUint16(20, 1, true); daten.setUint16(22, 1, true); daten.setUint32(24, rate, true); daten.setUint32(28, rate * 2, true);
    daten.setUint16(32, 2, true); daten.setUint16(34, 16, true); text(36, "data"); daten.setUint32(40, n * 2, true);
    worte.forEach((w, i) => {
      const frequenz = 300 + ((i * 7) % 6) * 45, von = Math.floor(w.start * rate), bis = Math.min(n, Math.floor(w.end * rate));
      for (let s = von; s < bis; s++) {
        const t = (s - von) / rate, rest = (bis - s) / rate;
        const huellkurve = Math.min(1, t / 0.02, rest / 0.04);
        daten.setInt16(44 + s * 2, Math.round(Math.sin(2 * Math.PI * frequenz * t) * 0.22 * huellkurve * 32767), true);
      }
    });
    return new Blob([daten], { type: "audio/wav" });
  }
  // Das Testsignal hängt nur von der Wortzahl und der Dauer ab, nicht von der Sprache; deshalb genügt ein Signal je Pfad.
  const signale = new Map();
  function signalFuer(pfad) {
    let mm, dauer, worte;
    if ((mm = /^\/v1\/audio\/([^/]+)$/.exec(pfad)) && audios[mm[1]]) { dauer = audios[mm[1]].pcm.duration_seconds; worte = woerterFuer(audios[mm[1]]); }
    else if ((mm = /^\/v1\/voices\/([^/]+)\/references\/([^/]+)\/audio$/.exec(pfad))) {
      const ref = stimme(mm[1])?.references.find((r) => r.id === mm[2]);
      if (!ref) return null;
      dauer = ref.pcm.duration_seconds; worte = woerter(S(ref.transcription) || "a b c d e f", dauer);
    } else if ((mm = /^\/v1\/media\/analyses\/([^/]+)\/candidates\/([^/]+)\/audio$/.exec(pfad))) {
      const k = kandidaten.find((c) => c.id === mm[2]);
      if (!k) return null;
      dauer = k.end_seconds - k.start_seconds; worte = woerter(S(k.transcription?.text || kandidatText), dauer);
    } else return null;
    if (!signale.has(pfad)) signale.set(pfad, URL.createObjectURL(wavBlob(dauer, worte)));
    return signale.get(pfad);
  }
  const quelle = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "src");
  Object.defineProperty(HTMLMediaElement.prototype, "src", {
    configurable: true, enumerable: quelle.enumerable, get: quelle.get,
    set(wert) {
      const pfad = typeof wert === "string" && wert.startsWith("/v1/") ? new URL(wert, location.href).pathname : null;
      quelle.set.call(this, (pfad && signalFuer(pfad)) || wert);
    },
  });

  // ---------------------------------------------------------------- Hinweise (kurz, ehrlich) und Sprachwahl
  const hinweisbox = document.createElement("div");
  hinweisbox.setAttribute("role", "status");
  hinweisbox.setAttribute("data-demo-hinweis", "");
  hinweisbox.style.cssText = "position:fixed;left:50%;top:16px;transform:translateX(-50%);z-index:50;max-width:min(520px,calc(100vw - 32px));padding:12px 16px;border-radius:10px;background:#2b2250;color:#fff;font:500 13px/1.5 Inter,'Segoe UI',system-ui,sans-serif;box-shadow:0 10px 30px #2b225033;display:none";
  let hinweisZeit = 0;
  function hinweis(schluessel) {
    hinweisbox.textContent = m(schluessel);
    hinweisbox.style.display = "block";
    clearTimeout(hinweisZeit);
    hinweisZeit = setTimeout(() => { hinweisbox.style.display = "none"; }, 5000);
  }
  // Hinweisfelder, deren Text beim Sprachwechsel neu gesetzt wird: [Element, Schlüssel].
  const hinweisfelder = [];
  const notiz = (schluessel, attribut) => {
    const p = document.createElement("p");
    p.className = "notice";
    p.setAttribute(attribut, "");
    p.textContent = m(schluessel);
    hinweisfelder.push([p, schluessel]);
    return p;
  };
  // Rahmenhinweis mit dem Schalter DE | FR.
  function rahmenhinweis() {
    const p = document.createElement("p");
    p.className = "notice";
    p.setAttribute("data-demo-rahmen", "");
    p.style.cssText = "display:flex;gap:12px;align-items:flex-start;justify-content:space-between";
    const text = document.createElement("span");
    text.textContent = m("rahmen");
    hinweisfelder.push([text, "rahmen"]);
    const gruppe = document.createElement("span");
    gruppe.setAttribute("role", "group");
    gruppe.style.cssText = "display:inline-flex;gap:4px;flex:none";
    gruppe.setAttribute("aria-label", "Sprache / Langue");
    for (const code of ["de", "fr"]) {
      const knopf = document.createElement("button");
      knopf.type = "button";
      knopf.textContent = code.toUpperCase();
      knopf.setAttribute("data-demo-lang", code);
      knopf.style.cssText = "padding:3px 10px;border-radius:7px;font-size:12px";
      knopf.addEventListener("click", () => window.setLang?.(code));
      gruppe.append(knopf);
    }
    p.append(text, gruppe);
    return p;
  }
  function sprachschalterAktualisieren() {
    for (const knopf of document.querySelectorAll("[data-demo-lang]")) {
      const aktiv = knopf.getAttribute("data-demo-lang") === sprache();
      knopf.setAttribute("aria-pressed", String(aktiv));
      knopf.classList.toggle("secondary", !aktiv);
    }
  }
  let saatText = "";
  const saat = () => S(textA);
  document.addEventListener("DOMContentLoaded", () => {
    document.body.append(hinweisbox);
    // Einziger Rahmenhinweis der Demo.
    document.getElementById("workspace").prepend(rahmenhinweis());
    // Zustände, die ohne lokalen Dienst nicht funktionieren: sichtbar deaktiviert statt vorgetäuscht.
    document.querySelector(".generate-bar").before(notiz("synthese", "data-demo-synthese"));
    document.querySelector("#voice-import form").before(notiz("import", "data-demo-import"));
    document.querySelector("#import-form").prepend(notiz("importAusschnitt", "data-demo-import"));
    document.querySelector(".settings-grid .panel + .panel h2").after(notiz("dienst", "data-demo-dienst"));
    for (const knopf of document.querySelectorAll("#media-upload, #import-form button[type=submit]")) { knopf.disabled = true; knopf.title = m("deaktiviert"); }
    for (const feld of document.querySelectorAll("#media-files, #reference")) feld.disabled = true;
    // Konkreter Beispieltext, damit die Synthese-Ansicht nicht leer startet.
    const feld = document.getElementById("text");
    saatText = saat();
    feld.value = saatText;
    feld.dispatchEvent(new Event("input"));
    const docs = document.querySelector('a[href="/docs"]');
    if (docs) { docs.setAttribute("href", "#settings"); docs.removeAttribute("target"); docs.setAttribute("data-demo-docs", ""); }
    sprachschalterAktualisieren();
  });

  // Sprachwechsel: Hinweise, Titel und Beispieltext in der neuen Sprache (die Oberfläche selbst baut i18n.js/app.js neu auf).
  document.addEventListener("pps-lang", () => {
    for (const [element, schluessel] of hinweisfelder) element.textContent = m(schluessel);
    for (const knopf of document.querySelectorAll("#media-upload, #import-form button[type=submit]")) knopf.title = m("deaktiviert");
    const feld = document.getElementById("text");
    if (feld && feld.value === saatText) {
      saatText = saat();
      feld.value = saatText;
      feld.dispatchEvent(new Event("input"));
    } else saatText = saat();
    sprachschalterAktualisieren();
  });

  document.addEventListener("click", (e) => {
    const anker = e.target.closest?.("a[href]");
    if (!anker) return;
    if (anker.hasAttribute("data-demo-docs")) { e.preventDefault(); hinweis("docs"); return; }
    const ziel = anker.getAttribute("href") || "";
    const mm = /^\/v1\/audio\/([^/?]+)\?format=(wav|mp3)/.exec(ziel);
    if (!mm) return;
    e.preventDefault();
    if (mm[2] === "mp3") { hinweis("mp3"); return; }
    const url = signalFuer("/v1/audio/" + mm[1]);
    if (!url) return;
    const lade = document.createElement("a");
    lade.href = url; lade.download = m("signalName") + mm[1] + ".wav";
    document.body.append(lade); lade.click(); lade.remove();
  }, true);
})();
