import json
import shutil
import time
from pathlib import Path
from .audio import prepare_reference
from .errors import PPSError
from .storage import identity


def register(store, cfg, upload, name, language, transcription, authorized, declaration, voice_id=None):
    if not authorized or not declaration.strip():
        raise PPSError("AUTHORIZATION_REQUIRED", "Une autorisation explicite de clonage et d'utilisation est nécessaire.", 422)
    if language not in ("fr", "en", "de") or not name.strip() or len(name) > 120 or len(declaration) > 2000 or len(transcription) > 5000:
        raise PPSError("VOICE_INVALID", "Nom, langue ou transcription non valide.", 422)
    identifier = voice_id or identity("voice")
    previous = store.get("voices", identifier) if voice_id else None
    ref_id = identity("ref")
    directory = Path(cfg["paths"]["data"]) / "voices" / identifier / ref_id
    directory.mkdir(parents=True)
    original = directory / "original.audio"
    try:
        size = 0
        with original.open("wb") as dest:
            for block in iter(lambda: upload.file.read(1024 * 1024), b""):
                size += len(block)
                if size > cfg["service"]["max_reference_bytes"]:
                    raise PPSError("UPLOAD_TOO_LARGE", "Référence trop volumineuse.", 413)
                dest.write(block)
        prepared = prepare_reference(original, directory)
        ref = {"id": ref_id, "original": str(original), "filename": Path(upload.filename or "audio").name,
               "transcription": transcription.strip(), "language": language,
               "transcription_verified": bool(transcription.strip()),
               "authorization": {"granted": True, "declaration": declaration.strip(), "recorded_at": time.time()},
               "preparation": {"chatterbox": "audio_ready_conditioning_pending",
                               "cosyvoice": "audio_ready_conditioning_pending" if transcription.strip() else "transcription_required"},
               **prepared}
        # Serialize version changes even if two uploads finish at the same time.
        with store.transaction() as db:
            previous = store.get("voices", identifier, db) if voice_id else None
            voice = previous or {"id": identifier, "name": name.strip(), "language": language,
                                 "version": 0, "references": [], "created_at": time.time()}
            voice["version"] += 1
            voice["references"].append(ref)
            voice["active_reference_id"] = ref_id
            store.put("voices", voice, db)
        return voice
    except Exception:
        # Only the new PPS-owned import directory is removed; never the source upload.
        shutil.rmtree(directory)
        raise


def public_voice(voice):
    voice = json.loads(json.dumps(voice))
    for ref in voice["references"]:
        for key in ("original", "chatterbox", "cosyvoice"):
            ref.pop(key, None)
        ref.get('media_metrics',{}).pop('embedding',None)
    return voice
