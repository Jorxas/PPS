import hashlib
import subprocess
import wave
import array
import math
import sys
from pathlib import Path
import imageio_ffmpeg
from .errors import PPSError


def ffmpeg():
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args):
    try:
        subprocess.run([ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)],
                       check=True, capture_output=True, timeout=90,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        message = getattr(e, "stderr", b"")
        if isinstance(message, bytes):
            message = message.decode("utf-8", "replace")
        raise PPSError("AUDIO_INVALID", "Traitement audio impossible.", 422,
                       {"reason": str(message or e)[-1500:]}) from e


def hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def pcm_metadata(path):
    with wave.open(str(path), "rb") as f:
        frames, rate = f.getnframes(), f.getframerate()
        if f.getnchannels() != 1 or f.getsampwidth() != 2 or f.getcomptype() != "NONE" or frames == 0:
            raise PPSError("AUDIO_INVALID", "WAV PCM16 mono non valide.", 422)
        peak, squares, count, clipped = 0, 0, 0, 0
        while data := f.readframes(65536):
            values = array.array("h", data)
            if sys.byteorder != "little":
                values.byteswap()
            peak = max(peak, max(abs(v) for v in values))
            squares += sum(v*v for v in values)
            count += len(values)
            clipped += sum(abs(v) >= 32760 for v in values)
    return {"frames": frames, "sample_rate": rate, "duration_seconds": frames / rate,
            "channels": 1, "encoding": "PCM16", "sha256": hash_file(path),
            "peak_amplitude": peak / 32768, "rms_amplitude": math.sqrt(squares/count) / 32768,
            "clipped_sample_count": clipped, "near_rail_sample_count":clipped,
            "clipped_sample_count_legacy_note":"Counts abs(sample)>=32760; use rail_metrics for clipping evidence.",
            "normalization": "format_channels_sample_rate_only"}


def prepare_reference(original, target_dir):
    target_dir = Path(target_dir)
    # Decode at most 31 seconds; reject long references without allocating their full PCM.
    wav24, wav16 = target_dir / "reference-24000.wav", target_dir / "reference-16000.wav"
    run(["-i", original, "-t", "31", "-map", "0:a:0", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", wav24])
    meta = pcm_metadata(wav24)
    if not 3 <= meta["duration_seconds"] <= 30:
        raise PPSError("REFERENCE_DURATION", "La référence doit durer de 3 à 30 secondes ; 8 à 10 sont recommandées.", 422, meta)
    if meta["rms_amplitude"] < .0001:
        raise PPSError("REFERENCE_SILENT", "Référence silencieuse ou niveau trop faible.", 422)
    run(["-i", wav24, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav16])
    return {"chatterbox": str(wav24), "cosyvoice": str(wav16), "pcm": meta,
            "original_sha256": hash_file(original)}


def export(raw, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    master, mp3 = directory / "master.wav", directory / "mainclip.mp3"
    # No destructive loudness processing: preserve engine dynamics and watermark.
    run(["-i", raw, "-ac", "1", "-c:a", "pcm_s16le", master])
    meta = pcm_metadata(master)
    run(["-i", master, "-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "128k", mp3])
    decoded = directory / "mp3-decoded.wav"
    run(["-i", mp3, "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", decoded])
    mp3_meta = pcm_metadata(decoded)
    decoded.unlink()
    if abs(mp3_meta["duration_seconds"] - meta["duration_seconds"]) > .05:
        raise PPSError("EXPORT_DURATION_MISMATCH", "Durée MP3 décodée incohérente avec le master.", 500)
    return {"wav": str(master), "mp3": str(mp3), "pcm": meta,
            "mp3_decoded_pcm": mp3_meta, "mp3_sha256": hash_file(mp3),
            "mp3_sample_rate": 44100, "mp3_bitrate": 128000}


def concatenate_pcm_files(segment_paths, output_path, gap_seconds=0.06):
    """Concatenate multiple PCM WAV files on an exact 44.1kHz sample clock with gap_seconds silence.

    Never allocates entire long files at once; streaming chunk write to output.
    Returns metadata list of segment boundaries: [{'index': i, 'start': start, 'end': end, 'duration': dur}]
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rate = 44100
    gap_frames = int(gap_seconds * rate)
    silence_bytes = b"\x00\x00" * gap_frames
    
    # Pre-convert all segment wavs to 44.1kHz 16-bit mono into temporary files
    import tempfile
    normalized_paths = []
    offsets = []
    current_frame = 0

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        for i, path in enumerate(segment_paths):
            norm_wav = tmp / f"norm_{i}.wav"
            run(["-i", path, "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", norm_wav])
            normalized_paths.append(norm_wav)

        with wave.open(str(output_path), "wb") as out_wav:
            out_wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))

            for i, norm_path in enumerate(normalized_paths):
                if i > 0 and gap_frames > 0:
                    out_wav.writeframes(silence_bytes)
                    current_frame += gap_frames

                start_sec = current_frame / rate
                with wave.open(str(norm_path), "rb") as in_wav:
                    seg_frames = in_wav.getnframes()
                    while data := in_wav.readframes(65536):
                        out_wav.writeframes(data)
                
                current_frame += seg_frames
                end_sec = current_frame / rate
                offsets.append({
                    "index": i,
                    "start": round(start_sec, 4),
                    "end": round(end_sec, 4),
                    "duration_seconds": round(end_sec - start_sec, 4)
                })

    meta = pcm_metadata(output_path)
    return {"path": str(output_path), "segments": offsets, "pcm": meta}

