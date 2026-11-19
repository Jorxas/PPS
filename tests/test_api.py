import copy
import json
from pathlib import Path
from pps.api import create_app
from pps.storage import Store
from pps.jobs import JobService
from pps.errors import PPSError
from pps.resources import ProcessLock
from conftest import FakeManager, import_tone, tone_bytes, wait_job


def request(voice, engine="chatterbox"):
    return {"voice_id": voice["id"], "text": "Bonjour, ceci est un test.", "engine": engine, "language": "fr"}


def test_openapi_and_health(client):
    assert client.get("/v1/health").json()["inference_concurrency"] == 1
    schema = client.get("/openapi.json").json()
    for path in ["health", "engines", "voices", "voices/{voice_id}/references", "syntheses", "jobs/{job_id}", "jobs/{job_id}/cancel", "audio/{audio_id}", "audio/{audio_id}/metadata", "audio/{audio_id}/alignment", "comparisons", "comparisons/{comparison_id}", "metrics"]:
        assert "/v1/" + path in schema["paths"]
    docs = client.get("/docs")
    assert "/static/swagger-ui-bundle.js" in docs.text
    assert "cdn.jsdelivr.net" not in docs.text
    assert client.get("/static/swagger-ui-bundle.js").status_code == 200


def test_voice_authorization_required(client):
    r = client.post("/v1/voices", data={"name": "unauthorized"}, files={"file": ("x.wav", tone_bytes())})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "AUTHORIZATION_REQUIRED"
    assert client.get("/v1/voices").json()["voices"] == []


def test_voice_bad_audio_and_duration(client):
    for payload, code in [(b"not audio", "AUDIO_INVALID"), (tone_bytes(1), "REFERENCE_DURATION"), (tone_bytes(31), "REFERENCE_DURATION")]:
        r = client.post("/v1/voices", data={"name": "bad", "authorized": "true", "declaration": "synthetic fixture"}, files={"file": ("x.wav", payload)})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == code
    assert client.get("/v1/voices").json()["voices"] == []


def test_voice_registry_versioning_and_hash(client, voice):
    assert voice["version"] == 1 and len(voice["references"][0]["original_sha256"]) == 64
    assert "original" not in voice["references"][0]
    r = client.post("/v1/voices/"+voice["id"]+"/references", data={"authorized": "true", "declaration": "second synthetic signal", "transcription": "signal"}, files={"file": ("second.wav", tone_bytes(9))})
    assert r.status_code == 201
    assert r.json()["version"] == 2
    assert len(r.json()["references"]) == 2
    assert client.get("/v1/voices/"+voice["id"]).json()["version"] == 2


def test_voice_idempotency(client):
    a = import_tone(client, key="voice-key")
    b = import_tone(client, key="voice-key")
    assert a.json()["id"] == b.json()["id"]
    assert len(client.get("/v1/voices").json()["voices"]) == 1
    assert import_tone(client, transcript="different", key="voice-key").status_code == 409


def test_synthesis_audio_and_alignment_contract(client, voice):
    r = client.post("/v1/syntheses", json=request(voice))
    assert r.status_code == 202
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "completed", job
    assert "reference" not in job["request"]
    states = [x["status"] for x in job["history"]]
    assert states == ["queued", "loading", "synthesizing", "exporting", "aligning", "completed"]
    identifier = job["audio_id"]
    assert client.get("/v1/audio/"+identifier).content[:4] == b"RIFF"
    assert client.get("/v1/audio/"+identifier+"?format=mp3&download=true").headers["content-type"] == "audio/mpeg"
    meta = client.get("/v1/audio/"+identifier+"/metadata").json()
    assert meta["pcm"]["channels"] == 1 and meta["pcm"]["encoding"] == "PCM16"
    assert abs(meta["pcm"]["duration_seconds"] - .4) < .001
    assert meta["mp3_sample_rate"] == 44100 and meta["mp3_bitrate"] == 128000
    alignment = client.get("/v1/audio/"+identifier+"/alignment").json()
    assert alignment["status"] == "not_available"
    assert alignment["words"] == [] and not alignment["mainclip_compatible"]
    assert alignment["audio_sha256"] == meta["pcm"]["sha256"]


def test_synthesis_idempotency_and_conflict(client, voice):
    a = client.post("/v1/syntheses", json=request(voice), headers={"Idempotency-Key": "job-key"})
    b = client.post("/v1/syntheses", json=request(voice), headers={"Idempotency-Key": "job-key"})
    assert a.json()["job_id"] == b.json()["job_id"]
    body = {**request(voice), "text": "Un texte différent."}
    assert client.post("/v1/syntheses", json=body, headers={"Idempotency-Key": "job-key"}).status_code == 409


def test_comparison_same_reference_and_two_files(client, voice):
    r = client.post("/v1/comparisons", json={"voice_id": voice["id"], "text": "Bonjour, comparaison.", "language": "fr"}, headers={"Idempotency-Key": "AB"})
    assert r.status_code == 202
    for j in r.json()["job_ids"]:
        assert wait_job(client, j)["status"] == "completed"
    c = client.get("/v1/comparisons/"+r.json()["comparison_id"]).json()
    assert c["status"] == "completed"
    a, b = c["jobs"]
    assert a["request"]["text"] == b["request"]["text"]
    assert a["request"]["reference_hash"] == b["request"]["reference_hash"]
    assert a["audio_id"] != b["audio_id"]
    assert client.app.state.jobs.manager.loads == ["chatterbox", "cosyvoice"]
    # Switch A -> B -> A contract, using synthetic fixture only.
    j = client.post("/v1/syntheses", json=request(voice)).json()["job_id"]
    assert wait_job(client, j)["status"] == "completed"
    assert client.app.state.jobs.manager.loads == ["chatterbox", "cosyvoice", "chatterbox"]


def test_no_partial_comparison_when_transcript_missing(client):
    voice = import_tone(client, transcript="").json()
    before = len(client.get("/v1/jobs").json()["jobs"])
    r = client.post("/v1/comparisons", json={"voice_id": voice["id"], "text": "Bonjour."})
    assert r.status_code == 422 and r.json()["error"]["code"] == "TRANSCRIPTION_REQUIRED"
    assert len(client.get("/v1/jobs").json()["jobs"]) == before


def test_invalid_options_and_unknown_ids(client, voice):
    for options in [{"stability": .8}, {"exaggeration": 5}]:
        r = client.post("/v1/syntheses", json={**request(voice), "options": options})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "OPTION_UNSUPPORTED"
    assert client.get("/v1/jobs/unknown").status_code == 404
    assert client.get("/v1/audio/unknown").status_code == 404
    assert client.post("/v1/syntheses", json={**request(voice), "engine": "elevenlabs"}).status_code == 422
    assert client.post("/v1/syntheses", json={**request(voice), "text": " "}).status_code == 422


def test_cancel_running_and_queued(client, voice):
    client.app.state.jobs.manager.delay = 1
    ids = [client.post("/v1/syntheses", json=request(voice)).json()["job_id"] for _ in range(2)]
    for j in ids:
        assert client.post("/v1/jobs/"+j+"/cancel").status_code == 200
        assert wait_job(client, j)["status"] == "cancelled"


def test_resource_busy_bounded_no_worker_launch(client, voice):
    count = []
    def busy(*args):
        count.append(1)
        raise PPSError("RESOURCE_BUSY", "Not enough resources", 503, retryable=True)
    client.app.state.jobs.manager.ensure = busy
    j = client.post("/v1/syntheses", json=request(voice)).json()["job_id"]
    result = wait_job(client, j)
    assert result["status"] == "failed" and result["error"]["code"] == "RESOURCE_BUSY"
    assert len(count) == 3


def test_oom_structured_failure(client, voice):
    def fail(*args):
        raise PPSError("CUDA_OOM", "CUDA out of memory", 500)
    client.app.state.jobs.manager.synthesize = fail
    j = client.post("/v1/syntheses", json=request(voice)).json()["job_id"]
    result = wait_job(client, j)
    assert result["status"] == "failed" and result["audio_id"] is None
    assert result["error"]["code"] == "CUDA_OOM"


def test_restart_persists_and_recovers(cfg):
    store = Store(Path(cfg["paths"]["data"])/"pps.sqlite3")
    store.put("jobs", {"id": "interrupted", "status": "synthesizing", "request": {}, "history": [], "cancel_requested": False})
    service = JobService(store, cfg, FakeManager())
    service.start()
    try:
        recovered = Store(store.path).get("jobs", "interrupted")
        assert recovered["status"] == "failed" and recovered["error"]["code"] == "INTERRUPTED"
    finally:
        service.close()


def test_gpu_process_lock(cfg):
    a, b = ProcessLock(Path(cfg["root"])/"runtime/gpu.lock"), ProcessLock(Path(cfg["root"])/"runtime/gpu.lock")
    a.acquire()
    try:
        try:
            b.acquire()
            assert False, "Second instance must not acquire GPU"
        except PPSError as e:
            assert e.code == "RESOURCE_BUSY"
    finally:
        a.release()
    b.acquire()
    b.release()


def test_local_origin_and_body_limit(client):
    assert client.post("/v1/syntheses", headers={"Origin": "https://external.example"}, json={}).status_code == 403
    r = client.post("/v1/voices", content=b"x", headers={"Content-Length": "999999999"})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "UPLOAD_TOO_LARGE"


def test_queue_bound(client, voice):
    client.app.state.cfg["service"]["max_queue"] = 1
    client.app.state.jobs.manager.delay = 1
    client.post("/v1/syntheses", json=request(voice))
    r = client.post("/v1/syntheses", json=request(voice))
    assert r.status_code == 429 and r.json()["error"]["code"] == "QUEUE_FULL"
