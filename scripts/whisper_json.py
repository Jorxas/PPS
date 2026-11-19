"""Offline CPU Whisper JSON CLI for PPS PCM16 WAV; no PATH/ffmpeg dependency."""
import argparse
import json
import os
import sys
import time
import wave
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(XDG_CACHE_HOME=str(ROOT / "cache"), TORCH_HOME=str(ROOT / "cache" / "torch"),
                  MPLCONFIGDIR=str(ROOT / "cache" / "matplotlib"), NUMBA_CACHE_DIR=str(ROOT / "cache" / "numba"),
                  HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
from pps.memory import memory_snapshot
parser = argparse.ArgumentParser()
parser.add_argument("audio")
parser.add_argument("--model", required=True)
parser.add_argument("--device", choices=["cpu"], default="cpu")
parser.add_argument("--language", required=True)
parser.add_argument("--word_timestamps", default="True")
parser.add_argument("--output_format", choices=["json"], default="json")
parser.add_argument("--output_dir", required=True)
parser.add_argument("--script_text_file")
args = parser.parse_args()
if not Path(args.model).is_file(): raise SystemExit("Local checkpoint required; no implicit download")
stages = []
def record(name): stages.append({"stage":name, **memory_snapshot()})
record("before_import")
if stages[-1]["physical_available_gib"] < 3: raise SystemExit("RESOURCE_BUSY: CPU ASR requires 3 GiB available RAM")
import numpy as np
import torch
import torchaudio
import whisper
torch.set_num_threads(4)
record("after_import_before_weights")
if stages[-1]["physical_available_gib"] < 3: raise SystemExit("RESOURCE_BUSY: CPU ASR requires 3 GiB after imports")
with wave.open(args.audio, "rb") as audio:
    if audio.getsampwidth() != 2 or audio.getnchannels() != 1 or audio.getcomptype() != "NONE":
        raise ValueError("PPS PCM16 mono WAV required")
    rate = audio.getframerate()
    waveform = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768
if rate != 16000:
    waveform = torchaudio.functional.resample(torch.from_numpy(waveform), rate, 16000).numpy()
started = time.perf_counter()
model = whisper.load_model(args.model, device="cpu")
load_seconds = time.perf_counter() - started
record("weights_loaded")
started = time.perf_counter()
result = model.transcribe(waveform, language=args.language, word_timestamps=args.word_timestamps == "True", fp16=False)
if args.script_text_file:
    import re
    from whisper.timing import find_alignment, merge_punctuations
    from whisper.tokenizer import get_tokenizer
    script = Path(args.script_text_file).read_text(encoding="utf-8")
    tokenizer = get_tokenizer(model.is_multilingual, num_languages=model.num_languages,
                              language=args.language, task="transcribe")
    clean_script = re.sub(r'\s+([:;!?»])', r'\1', script)
    clean_script = re.sub(r'([«])\s+', r'\1', clean_script)
    script_tokens = tokenizer.encode(" " + clean_script.strip())

    def forced_words(samples, offset=0):
        if len(samples) > whisper.audio.N_SAMPLES:
            raise ValueError('Forced alignment is limited to 30 seconds; segment longer audio explicitly')
        features = whisper.log_mel_spectrogram(samples, model.dims.n_mels, padding=whisper.audio.N_SAMPLES)
        features = whisper.pad_or_trim(features, whisper.audio.N_FRAMES)
        aligned = find_alignment(model, tokenizer, script_tokens, features,
                                 len(samples) // whisper.audio.HOP_LENGTH)
        merge_punctuations(aligned, "\"'“¿([{-«", "\"'.。,，!！?？:：”)]}、»")
        extracted = []
        for w in aligned:
            word_text = w.word.strip()
            if not word_text or re.match(r'^[\W_]+$', word_text, re.UNICODE):
                continue
            st = float(w.start) + offset
            et = float(w.end) + offset
            if et <= st:
                et = st + 0.02
            extracted.append({"word": word_text, "start": round(st, 4), "end": round(et, 4),
                             "probability": float(w.probability)})
        total_dur = len(samples) / 16000 + offset
        prev_end = offset
        result_words = []
        for w in extracted:
            st = max(prev_end, w["start"])
            et = max(st + 0.02, w["end"])
            if et > total_dur:
                et = total_dur
                if st >= et:
                    st = max(offset, et - 0.02)
            result_words.append({**w, "start": round(st, 4), "end": round(et, 4)})
            prev_end = et
        return result_words
    if len(waveform)>whisper.audio.N_SAMPLES:
        # Preserve ASR evidence rather than silently truncate its forced alignment window.
        result['pps_forced_alignment_error']='Audio exceeds 30 seconds; forced alignment requires explicit shorter segments.'
    else:
        result["pps_forced_alignment"] = {"text":script, "provenance":"whisper_cross_attention_dtw",
            "words":forced_words(waveform),
            "interpolated_word_count":0, "note":"Script is imposed; coverage alone does not establish acoustic correctness."}
        from pps.signal import activity
        evidence = activity(args.audio)
        # Crop only the analysis input, never the delivered WAV. Restore the EXACT sample offset.
        crop_sample = int(evidence['analysis_crop_offset_seconds'] * 16000)
        if crop_sample > 0:
            offset = crop_sample / 16000
            result['pps_cropped_forced_alignment'] = {'text':script,
                'provenance':'whisper_cross_attention_dtw_energy_preroll_crop',
                'words':forced_words(waveform[crop_sample:],offset), 'crop_offset_seconds':offset,
                'note':'120 ms pre-roll before sustained RMS onset. Final audio unchanged; offset mapped to its sample clock. Energy is not phonetic validation.'}
record("transcription_complete")
result["pps_metrics"] = {"device":"cpu", "load_seconds":load_seconds,
    "transcribe_seconds":time.perf_counter()-started, "stages":stages}
output = Path(args.output_dir)
output.mkdir(parents=True, exist_ok=True)
(output / (Path(args.audio).stem + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
