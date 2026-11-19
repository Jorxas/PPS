"""Optional ASR evidence. No fabricated or uniformly interpolated timestamps."""
import json
import re
import subprocess
import math
import unicodedata
from pathlib import Path
from difflib import SequenceMatcher


def tokens(text):
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text.casefold(), re.UNICODE)


def canonical_tokens(text):
    text=unicodedata.normalize('NFC', text).replace('’', "'").replace('ʼ', "'")
    return tokens(re.sub(r"'\s+", "'",text))


def french_contractions(words):
    merged=[]
    for original in words:
        w=dict(original)
        text=w.get('text',w.get('word','')).strip()
        previous=merged[-1] if merged else None
        prev=previous.get('text',previous.get('word','')).strip() if previous else ''
        if previous and (prev.endswith(("'",'’','ʼ')) or text.startswith(("'",'’','ʼ'))) and isinstance(w.get('start'),(int,float)) and isinstance(previous.get('end'),(int,float)) and -.001<=w['start']-previous['end']<=.06:
            source=previous.get('source_tokens',[dict(previous)])+[dict(w)]
            previous.update(word=prev+text,text=prev+text,end=w.get('end'),source_tokens=source,
                            method='French_explicit_apostrophe_token_merge',
                            provenance={'source_tokens':source,'boundary_policy':'outer_original_bounds_no_interpolation'},
                            probability=min(previous.get('probability',1),w.get('probability',1)))
        else:
            merged.append(w)
    return merged


def valid_bounds(words, duration):
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0:
        return False
    previous = 0.0
    for w in words:
        a, b = w.get('start'), w.get('end')
        if any(isinstance(x, bool) or not isinstance(x, (int,float)) or not math.isfinite(x) for x in (a,b)):
            return False
        if not 0 <= a < b <= duration + .001 or a < previous - .001:
            return False
        previous = b
    return bool(words)


def annotated(words, method, provenance):
    return [{**w, 'text':w.get('text', w.get('word','')).strip(), 'method':w.get('method',method),
             'provenance':w.get('provenance',provenance), 'confidence_status':w.get('confidence_status','uncertain'),
             'index':i,'confidence':w.get('confidence'), 'timing_origin':w.get('timing_origin','estimated'),
             'uncertainty':w.get('uncertainty',['acoustic_boundary_not_independently_verified'])} for i,w in enumerate(words)]


def evaluate(script, words, duration, provenance="whisper_asr"):
    raw_words=words
    words=french_contractions(words)
    expected = canonical_tokens(script)
    observed = [t for w in words for t in canonical_tokens(w.get("text",w.get("word", "")))]
    matcher = SequenceMatcher(None, expected, observed, autojunk=False)
    matched = sum(m.size for m in matcher.get_matching_blocks())
    differences = [{"operation":tag, "script_word_start":a, "script_words":expected[a:b],
                    "recognized_word_start":c, "recognized_words":observed[c:d]}
                   for tag,a,b,c,d in matcher.get_opcodes() if tag != "equal"]
    coverage = matched / len(expected) if expected else 0
    valid = valid_bounds(words, duration)
    homophones = [d for d in differences if d['operation']=='replace' and
                  {tuple(d['script_words']),tuple(d['recognized_words'])} == {('locale',),('local',)}]
    return {"status": "asr_unverified" if coverage >= .95 and valid and words else "incomplete",
            "quality": "requires_forced_alignment_review", "mainclip_compatible": False,
            "display_text": script, "spoken_text": " ".join(w.get("word", "") for w in words),
            "words": annotated(words, provenance, provenance), "script_coverage": coverage, "timestamp_provenance": provenance,
            'raw_words':raw_words,
            "orthographic_homophone_candidates": homophones,
            "homophone_note": "local/locale peuvent avoir la même prononciation ; aucune omission ni correction automatique déduite.",
            "interpolated_word_count": 0, "timestamps_valid": valid,
            "unmatched_script_words": [w for diff in differences for w in diff["script_words"]],
            "unmatched_recognized_words": [w for diff in differences for w in diff["recognized_words"]],
            "word_differences": differences,
            "exact": False}


def boundary_evidence(result, analysis, candidate=None):
    """Keep raw ASR/DTW; select an offset-mapped analysis crop as a review candidate."""
    result['signal_analysis'] = {k:v for k,v in analysis.items() if k != 'rms_windows'}
    fa = result.get('forced_alignment')
    # Prioritize candidate that actually has valid bounds
    if candidate and candidate.get('timestamps_valid'):
        selected = candidate
    elif fa and fa.get('timestamps_valid'):
        selected = fa
    else:
        selected = candidate or fa or result
    candidate = selected
    words = annotated(candidate.get('words', []), candidate.get('timestamp_provenance','unknown'),
                      {'alignment':candidate.get('timestamp_provenance','unknown'),
                       'audio_sha256':result.get('audio_sha256'),
                       'crop_offset_seconds':candidate.get('crop_offset_seconds',0)})
    asr = result.get('words', [])
    for i,w in enumerate(words):
        provenance=w.get('provenance')
        w['provenance']={**(provenance if isinstance(provenance,dict) else {'alignment':provenance}),
                         'audio_sha256':result.get('audio_sha256'),
                         'crop_offset_seconds':candidate.get('crop_offset_seconds',0)}
        doubts = list(w['uncertainty'])
        a,b = w.get('start',-1),w.get('end',-1)
        if isinstance(a,(int,float)) and isinstance(b,(int,float)) and math.isfinite(a+b):
            if i == 0 and analysis['sustained_onset_seconds'] is not None and a < analysis['sustained_onset_seconds'] - .15:
                doubts.append('initial_boundary_precedes_sustained_energy')
            quiet = sum(max(0,min(b,s['end'])-max(a,s['start'])) for s in analysis['silences'])
            if quiet > .15 and quiet > (b-a)*.35:
                doubts.append('word_overlaps_low_energy_interval')
            if i<len(asr) and max(abs(a-asr[i]['start']),abs(b-asr[i]['end'])) > .12:
                doubts.append('ASR_DTW_boundary_disagreement')
            if w.get('probability',1) < .7:
                doubts.append('low_model_token_probability_not_boundary_confidence')
        w['uncertainty'] = list(dict.fromkeys(doubts))
    result['review_alignment'] = {**candidate, 'words':words, 'exact':False,
        'status':'candidate_requires_review', 'mainclip_compatible':False,
        'timestamps_valid':valid_bounds(words,analysis['duration_seconds']),
        'quality':'energy_constrained_analysis_not_independent_phonetic_validation'}
    result['mainclip_compatible'] = False
    return result


def run_one_shot(args, timeout):
    """Historical alignment call: one fresh CPU Whisper process per request."""
    return subprocess.run(args, capture_output=True, check=True, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def align(audio_path, display_text, language, duration, cfg, output_dir, runner=None):
    """runner(args, timeout) may replace the one-shot call with the same contract (pps.aligner)."""
    command = cfg["command"]
    base = evaluate(display_text, [], duration, "none")
    base["audio_sha256"] = __import__("pps.audio", fromlist=["hash_file"]).hash_file(audio_path)
    base["aligned_file"] = Path(audio_path).name
    if not command:
        return {**base, "status": "not_available", "reason": "Whisper local non configuré. Forced alignment non validé."}
    # Never ask Whisper to download a model implicitly.
    model_path = Path(cfg["model_dir"]) / (cfg["model"] + ".pt")
    if not model_path.is_file():
        return {**base, "status": "not_available", "reason": "Checkpoint Whisper local absent."}
    try:
        prefix = [command, "-B", cfg["script"]] if cfg.get("script") else [command]
        extra = []
        if cfg.get("script"):
            script_file = Path(output_dir) / "alignment-script.txt"
            script_file.write_text(display_text, encoding="utf-8")
            extra = ["--script_text_file", str(script_file)]
        args = [*prefix, str(audio_path), "--model", str(model_path), "--device", "cpu",
                "--language", language, "--word_timestamps", "True", "--output_format", "json",
                "--output_dir", str(output_dir), *extra]
        (runner or run_one_shot)(args, cfg["timeout_seconds"])
        output = json.loads((Path(output_dir) / (Path(audio_path).stem + ".json")).read_text(encoding="utf-8"))
        words = [w for s in output["segments"] for w in s.get("words", [])]
        result = {**base, **evaluate(display_text, words, duration), "resource_metrics": output.get("pps_metrics")}
        forced = output.get("pps_forced_alignment")
        if output.get('pps_forced_alignment_error'):
            result['forced_alignment_error']=output['pps_forced_alignment_error']
        if forced:
            assessed = evaluate(display_text, forced["words"], duration, forced["provenance"])
            result["forced_alignment"] = {**assessed, "status":"forced_unverified" if assessed["timestamps_valid"] and assessed["words"] else "failed",
                "quality":"requires_acoustic_review", "note":forced["note"]}
        from .signal import activity
        analysis = activity(audio_path)
        cropped = output.get('pps_cropped_forced_alignment')
        candidate = None
        if cropped:
            candidate = {**evaluate(display_text,cropped['words'],duration,cropped['provenance']),
                         'crop_offset_seconds':cropped['crop_offset_seconds'], 'note':cropped['note']}
            result['cropped_forced_alignment'] = candidate
        boundary_evidence(result, analysis, candidate)
        return result
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as e:
        return {**base, "status": "failed", "reason": str(e)}


def align_long_form(audio_path, display_text, language, duration, segments_info, cfg, output_dir, runner=None):
    """Run forced alignment for long-form audio.

    If audio <= 30s: runs standard single-pass align().
    If audio > 30s:
      - If segments_info is provided: uses constituent segment WAVs and sample clock offsets.
      - If segments_info is not provided: slices monolithic audio at natural silences
        into <= 25s chunks, aligns each chunk, and shifts timestamps based on exact offsets.
    """
    if duration <= 30.0:
        return align(audio_path, display_text, language, duration, cfg, output_dir, runner)

    output_dir = Path(output_dir)
    if not segments_info:
        from .signal import activity
        import wave
        act = activity(audio_path)
        total_dur = act["duration_seconds"]
        silences = act.get("silences", [])
        cut_points = [0.0]
        cur_start = 0.0
        for s in silences:
            mid = (s["start"] + s["end"]) / 2.0
            if mid - cur_start >= 12.0 and total_dur - mid >= 5.0:
                cut_points.append(mid)
                cur_start = mid
        if cut_points[-1] < total_dur:
            cut_points.append(total_dur)

        with wave.open(str(audio_path), 'rb') as f:
            params = f.getparams()
            rate = f.getframerate()
            frames = f.readframes(f.getnframes())

        segments_info = []
        for i in range(len(cut_points) - 1):
            st = cut_points[i]
            et = cut_points[i+1]
            f_st = int(st * rate)
            f_et = int(et * rate)
            sub_wav = output_dir / f"monolithic_slice_{i}.wav"
            with wave.open(str(sub_wav), 'wb') as out_f:
                out_f.setparams(params)
                out_f.writeframes(frames[f_st*2 : f_et*2])
            segments_info.append({
                "index": i,
                "wav_path": str(sub_wav),
                "start": round(st, 4),
                "end": round(et, 4),
                "duration_seconds": round((f_et - f_st) / rate, 4),
                "text": ""
            })

    all_words = []
    all_raw_words = []
    valid = True
    combined_status = "asr_unverified"

    for seg in segments_info:
        seg_idx = seg["index"]
        seg_audio = seg.get("wav_path")
        seg_text = seg.get("text", "")
        seg_start = seg.get("start", 0.0)
        seg_dur = seg.get("duration_seconds", 0.0)

        if not seg_audio or not Path(seg_audio).exists():
            continue

        seg_out_dir = output_dir / f"align_seg_{seg_idx}"
        seg_out_dir.mkdir(parents=True, exist_ok=True)
        seg_res = align(seg_audio, seg_text, language, seg_dur, cfg, seg_out_dir, runner)

        # Shift word timestamps by segment start offset
        words = seg_res.get("review_alignment", {}).get("words") or seg_res.get("forced_alignment", {}).get("words") or seg_res.get("words", [])
        for w in words:
            shifted = dict(w)
            if isinstance(shifted.get("start"), (int, float)):
                shifted["start"] = round(shifted["start"] + seg_start, 4)
            if isinstance(shifted.get("end"), (int, float)):
                shifted["end"] = round(shifted["end"] + seg_start, 4)
            shifted["segment_index"] = seg_idx
            all_words.append(shifted)

        raw = seg_res.get("raw_words", [])
        for rw in raw:
            sh = dict(rw)
            if isinstance(sh.get("start"), (int, float)):
                sh["start"] = round(sh["start"] + seg_start, 4)
            if isinstance(sh.get("end"), (int, float)):
                sh["end"] = round(sh["end"] + seg_start, 4)
            all_raw_words.append(sh)

        if seg_res.get("status") != "asr_unverified":
            combined_status = seg_res.get("status", "incomplete")

    eval_res = evaluate(display_text, all_words, duration, "whisper_asr_long_form")
    eval_res["audio_sha256"] = __import__("pps.audio", fromlist=["hash_file"]).hash_file(audio_path)
    eval_res["aligned_file"] = Path(audio_path).name
    eval_res["raw_words"] = all_raw_words
    eval_res["long_form_segments_count"] = len(segments_info)
    eval_res["mainclip_compatible"] = False
    eval_res["status"] = "candidate_requires_review" if eval_res.get("timestamps_valid") else "incomplete"
    eval_res["review_alignment"] = {**eval_res, 'status': 'candidate_requires_review',
        'quality': 'energy_constrained_analysis_not_independent_phonetic_validation'}
    if eval_res.get("timestamps_valid") and eval_res.get("words"):
        eval_res["forced_alignment"] = {**eval_res, 'status': 'forced_unverified',
            'quality': 'requires_acoustic_review'}
    return eval_res

