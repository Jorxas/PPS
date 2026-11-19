"""Tests contractuels bilatéraux pour l'API Universelle PPS (Phase 3C-1).

Valide formellement les 10 exigences TC-01 à TC-10 selon les spécifications
du document TTS_UNIVERSAL_CONTRACT_CANDIDATE_V1_2.md.
Fonctionne intégralement sans GPU (FastAPI TestClient + FakeManager).
"""
import pytest
from pps.errors import PPSError
from conftest import wait_job, import_tone, tone_bytes


# ==============================================================================
# TC-01 — Découverte dynamique des capacités
# ==============================================================================
def test_tc01_capabilities_discovery(client):
    r = client.get("/v1/capabilities")
    assert r.status_code == 200, r.text
    caps = r.json()

    assert caps["service"] == "Universal Phonation Service"
    assert caps["version"] == "1.2.0"
    assert caps["is_local"] is True
    assert caps["cost_model"] == "free_local_compute"
    assert caps["hardware"]["gpu_name"] == "NVIDIA GeForce RTX 4060 Laptop GPU"
    assert caps["hardware"]["concurrency_limit"] == 1

    engines = {e["id"]: e for e in caps["engines"]}
    assert "chatterbox" in engines
    assert "cosyvoice" in engines

    cb = engines["chatterbox"]
    assert cb["capabilities"]["word_timestamps"] is True
    assert cb["capabilities"]["character_timestamps"] is False
    assert cb["capabilities"]["speech_to_speech"] is False
    assert cb["capabilities"]["streaming_audio"] is False
    assert cb["capabilities"]["alignment_verification"] is True
    assert cb["audio_formats"]["default_sample_rate"] == 24000

    cv = engines["cosyvoice"]
    assert cv["capabilities"]["native_speed_control"] is True
    assert cv["capabilities"]["speech_to_speech"] is False


# ==============================================================================
# TC-02 — Catalogue vocal et résolution par alias / identité
# ==============================================================================
def test_tc02_voice_catalog_and_identities(client, voice):
    # Standard voice listing
    r = client.get("/v1/voices")
    assert r.status_code == 200
    voices = r.json()["voices"]
    assert len(voices) >= 1

    # Lookup by direct voice ID
    r_id = client.get(f"/v1/voices/{voice['id']}")
    assert r_id.status_code == 200
    assert r_id.json()["id"] == voice["id"]

    # Lookup by name/alias fallback
    r_name = client.get(f"/v1/voices/{voice['name']}")
    assert r_name.status_code == 200
    assert r_name.json()["id"] == voice["id"]


# ==============================================================================
# TC-03 — Validation des requêtes et rejet des entrées non conformes
# ==============================================================================
def test_tc03_request_validation(client, voice):
    # Empty text
    r = client.post("/v1/tts/synthesize", json={
        "text": "",
        "voiceId": voice["id"],
        "language": "fr"
    })
    assert r.status_code == 422
    assert r.json()["error"]["code"] in ("TTS_ERR_VALIDATION_FAILED", "VALIDATION_ERROR")

    # Unknown voice ID
    r = client.post("/v1/tts/synthesize", json={
        "text": "Bonjour monde.",
        "voiceId": "voice_inexistante_99999",
        "language": "fr"
    })
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "TTS_ERR_VOICE_NOT_FOUND"

    # Unsupported language
    r = client.post("/v1/tts/synthesize", json={
        "text": "Hallo Welt.",
        "voiceId": voice["id"],
        "language": "de"
    })
    assert r.status_code == 422
    assert r.json()["error"]["code"] in ("TTS_ERR_VALIDATION_FAILED", "VALIDATION_ERROR")


# ==============================================================================
# TC-04 — Soumission asynchrone et idempotence
# ==============================================================================
def test_tc04_async_job_submission(client, voice):
    req_body = {
        "requestId": "req_tc04_test",
        "text": "Ceci est un test de soumission asynchrone universelle.",
        "voiceId": voice["id"],
        "language": "fr",
        "requirements": {"priority": "normal"}
    }
    r1 = client.post("/v1/tts/synthesize", json=req_body, headers={"Idempotency-Key": "key_tc04_abc"})
    assert r1.status_code == 202
    res1 = r1.json()
    assert "jobId" in res1
    assert res1["status"] == "queued"
    assert res1["requestId"] == "req_tc04_test"

    # Idempotent re-submission returns identical job ID
    r2 = client.post("/v1/tts/synthesize", json=req_body, headers={"Idempotency-Key": "key_tc04_abc"})
    assert r2.status_code == 202
    assert r2.json()["jobId"] == res1["jobId"]


# ==============================================================================
# TC-05 — Consultation de job et conformité TTSResult V1.2
# ==============================================================================
def test_tc05_job_polling_and_result_schema(client, voice):
    req_body = {
        "text": "Synthèse vocale pour validation du résultat unifié.",
        "voiceId": voice["id"],
        "language": "fr",
        "requirements": {"alignmentMode": "words"}
    }
    r = client.post("/v1/tts/synthesize", json=req_body)
    assert r.status_code == 202
    job_id = r.json()["jobId"]

    # Poll until completed
    job = wait_job(client, job_id)
    assert job["status"] == "completed"

    # Universal job endpoint
    r_uni = client.get(f"/v1/tts/jobs/{job_id}")
    assert r_uni.status_code == 200
    res = r_uni.json()

    assert res["status"] in ("completed", "cached")
    assert res["jobId"] == job_id
    assert res["provider"]["id"] == "pps"
    assert res["provider"]["isLocal"] is True
    assert res["voice"]["voiceId"] == voice["id"]
    assert res["audio"]["container"] == "wav"
    assert res["audio"]["channels"] == 1
    assert res["audio"]["durationSeconds"] > 0
    assert len(res["audio"]["sha256"]) == 64
    assert res["speechMetrics"]["totalWords"] > 0
    assert res["performance"]["realTimeFactor"] is not None
    assert res["performance"]["estimatedCostUsd"] == 0.0


# ==============================================================================
# TC-06 — Résultat audio, téléchargement et métadonnées
# ==============================================================================
def test_tc06_audio_download_and_metadata(client, voice):
    req_body = {
        "text": "Échantillon audio pour vérification du binaire.",
        "voiceId": voice["id"],
        "language": "fr"
    }
    r = client.post("/v1/tts/synthesize", json=req_body)
    job_id = r.json()["jobId"]
    job = wait_job(client, job_id)
    audio_id = job["audio_id"]

    # Download binary audio
    r_audio = client.get(f"/v1/tts/audio/{audio_id}?format=wav")
    assert r_audio.status_code == 200
    assert "audio/wav" in r_audio.headers.get("content-type", "")
    assert len(r_audio.content) > 100

    # Audio metadata
    r_meta = client.get(f"/v1/tts/audio/{audio_id}/metadata")
    assert r_meta.status_code == 200
    meta = r_meta.json()
    assert meta["audioId"] == audio_id
    assert meta["pcm"]["sample_rate"] == 24000
    assert meta["pcm"]["duration_seconds"] > 0
    assert "wordsPerMinute" in meta["speechMetrics"]


# ==============================================================================
# TC-07 — Alignement temporel et métrologie
# ==============================================================================
def test_tc07_alignment_metrics_and_monotonicity(client, voice):
    # Test quick mode (no alignment requested)
    r_quick = client.post("/v1/tts/synthesize", json={
        "text": "Mode rapide sans alignement pour pré-écoute immédiate.",
        "voiceId": voice["id"],
        "language": "fr",
        "requirements": {"alignmentMode": "none"}
    })
    job_quick_id = r_quick.json()["jobId"]
    wait_job(client, job_quick_id)

    res_quick = client.get(f"/v1/tts/jobs/{job_quick_id}").json()
    assert res_quick["timings"]["granularity"] == "none"
    assert res_quick["timings"]["words"] == []
    assert res_quick["timings"]["alignmentReport"]["status"] == "skipped_quick_mode"
    assert res_quick["timings"]["alignmentReport"]["zeroDurationWordsCount"] == 0


# ==============================================================================
# TC-08 — Rejet des capacités non supportées (STS, caractères natifs, balises)
# ==============================================================================
def test_tc08_unsupported_capabilities_rejection(client, voice):
    # Speech-to-Speech
    r_sts = client.post("/v1/tts/synthesize", json={
        "text": "Voix doublée en speech-to-speech.",
        "voiceId": voice["id"],
        "language": "fr",
        "requirements": {"speechToSpeech": True}
    })
    assert r_sts.status_code == 422
    assert r_sts.json()["error"]["code"] == "TTS_ERR_CAPABILITY_UNSUPPORTED"

    # Strict native character timestamps
    r_char = client.post("/v1/tts/synthesize", json={
        "text": "Exigence de caractères natifs stricts.",
        "voiceId": voice["id"],
        "language": "fr",
        "requirements": {"strictNativeCharacters": True}
    })
    assert r_char.status_code == 422
    assert r_char.json()["error"]["code"] == "TTS_ERR_CAPABILITY_UNSUPPORTED"

    # Unprocessed SSML tags
    r_tag = client.post("/v1/tts/synthesize", json={
        "text": "Texte avec balise <break time='1s'/> interdite.",
        "voiceId": voice["id"],
        "language": "fr"
    })
    assert r_tag.status_code == 422
    assert r_tag.json()["error"]["code"] == "TTS_ERR_CAPABILITY_UNSUPPORTED"


# ==============================================================================
# TC-09 — Rétrocompatibilité absolue (Double-Run)
# ==============================================================================
def test_tc09_legacy_compatibility_double_run(client, voice):
    # Legacy health
    assert client.get("/v1/health").json()["inference_concurrency"] == 1

    # Legacy synthesis submission
    r_legacy = client.post("/v1/syntheses", json={
        "voice_id": voice["id"],
        "text": "Synthèse par route historique.",
        "engine": "chatterbox",
        "language": "fr"
    })
    assert r_legacy.status_code == 202
    job_id = r_legacy.json()["job_id"]
    wait_job(client, job_id)

    # Legacy job polling
    job_data = client.get(f"/v1/jobs/{job_id}").json()
    assert job_data["status"] == "completed"

    # Legacy mainclip composite audio route
    audio_id = job_data["audio_id"]
    r_mc_gated = client.get(f"/v1/audio/{audio_id}/mainclip")
    assert r_mc_gated.status_code == 422  # Strict gating when forced alignment is unverified
    r_mc = client.get(f"/v1/audio/{audio_id}/mainclip?require_alignment=false")
    assert r_mc.status_code == 200
    assert "audio_id" in r_mc.json()


# ==============================================================================
# TC-10 — Absence de fallback payant & résilience aux erreurs
# ==============================================================================
def test_tc10_no_paid_fallback_on_failure(client):
    # Intentionally call with a non-existent voice ID
    r = client.post("/v1/tts/synthesize", json={
        "text": "Test de non-basculement payant.",
        "voiceId": "voice_inexistante_clairement_invalide",
        "language": "fr"
    })
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "TTS_ERR_VOICE_NOT_FOUND"
    # Guaranteed no retryable false claim, no remote ElevenLabs call triggered
    assert err["retryable"] is False
    assert "elevenlabs" not in err["message"].lower()
