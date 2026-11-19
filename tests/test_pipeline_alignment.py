"""Resident-worker RAM floor, no-unload rules, background alignment and the resident aligner.

Fakes and temporary directories only: no engine, no Whisper checkpoint, no GPU.
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from conftest import wait_job
from pps.aligner import ResidentAligner
from pps.alignment import evaluate
from pps.errors import PPSError
from pps.manager import EngineManager
from pps.resources import GIB, loaded_ram_floor

ROOT = Path(__file__).resolve().parent.parent


def request(voice, text="Bonjour, ceci est un signal de test."):
    return {"voice_id": voice["id"], "engine": "chatterbox", "language": "fr", "text": text}


class LiveProcess:
    def poll(self):
        return None


def test_loaded_floor_defaults_and_config(cfg):
    cfg["resources"].pop("min_ram_gib_loaded", None)
    assert cfg["resources"]["min_ram_gib"] == 3.0 and loaded_ram_floor(cfg) == 2.0
    cfg["resources"]["min_ram_gib_loaded"] = 2.5
    assert loaded_ram_floor(cfg) == 2.5
    cfg["resources"].pop("min_ram_gib_loaded")
    cfg["resources"]["min_ram_gib"] = 1.5
    assert loaded_ram_floor(cfg) == 1.5  # never above the load threshold


@pytest.mark.parametrize("available,kept", [(2.5, True), (1.5, False)])
def test_loaded_worker_kept_down_to_resident_floor(cfg, monkeypatch, available, kept):
    m = EngineManager(cfg)
    m.engine, m.process = "chatterbox", LiveProcess()
    m.loaded = {"model_revision": "r", "source_revision": None}
    monkeypatch.setattr("pps.manager.status", lambda *a: {"model_revision": "r", "ready_to_attempt_load": False})
    monkeypatch.setattr("pps.manager.psutil.virtual_memory", lambda: SimpleNamespace(available=available * GIB))
    events = []
    def unload():
        events.append("unload")
        m.process = m.engine = None
    monkeypatch.setattr(m, "unload", unload)
    def guard(c):
        events.append("guard")
        raise PPSError("RESOURCE_BUSY", "RAM", 503)
    monkeypatch.setattr("pps.manager.guard", guard)
    if kept:
        assert m.ensure("chatterbox", lambda: False)["model_cache_hit"] is True
        assert events == []
    else:
        with pytest.raises(PPSError):
            m.ensure("chatterbox", lambda: False)
        assert events[0] == "unload" and "guard" in events


def counting_unload(client):
    manager = client.app.state.jobs.manager
    calls = []
    original = manager.unload
    manager.unload = lambda: (calls.append(True), original())[1]
    return calls


def test_resource_busy_and_cancel_do_not_unload(client, voice):
    calls = counting_unload(client)
    manager = client.app.state.jobs.manager
    manager.ensure = lambda *a: (_ for _ in ()).throw(PPSError("RESOURCE_BUSY", "busy", 503, retryable=True))
    assert wait_job(client, client.post("/v1/syntheses", json=request(voice)).json()["job_id"])["error"]["code"] == "RESOURCE_BUSY"
    assert calls == []
    manager.ensure = lambda *a: (_ for _ in ()).throw(PPSError("CANCELLED", "cancel", 409))
    assert wait_job(client, client.post("/v1/syntheses", json=request(voice, "Autre texte.")).json()["job_id"])["status"] == "cancelled"
    assert calls == []
    manager.ensure = lambda *a: (_ for _ in ()).throw(PPSError("CUDA_OOM", "oom", 500))
    assert wait_job(client, client.post("/v1/syntheses", json=request(voice, "Troisième texte.")).json()["job_id"])["error"]["code"] == "CUDA_OOM"
    assert calls == [True]  # other worker failures still discard the worker


def test_idle_loop_keeps_worker_above_resident_floor(client, monkeypatch):
    calls = counting_unload(client)
    client.app.state.jobs.manager.last_used = time.monotonic()
    monkeypatch.setattr("pps.jobs.psutil.virtual_memory", lambda: SimpleNamespace(total=32 * GIB, available=2.5 * GIB))
    time.sleep(1.2)
    assert calls == []
    monkeypatch.setattr("pps.jobs.psutil.virtual_memory", lambda: SimpleNamespace(total=32 * GIB, available=1.5 * GIB))
    time.sleep(1.2)
    assert calls


def slow_alignment(monkeypatch, seconds=.6):
    spans = []
    def align(audio, text, language, duration, cfg, output, runner=None):
        start = time.time()
        time.sleep(seconds)
        spans.append((start, time.time()))
        return evaluate(text, [], duration, "test_slow_alignment")
    monkeypatch.setattr("pps.jobs.align", align)
    return spans


def history_at(job, state):
    return next(h["at"] for h in job["history"] if h["status"] == state)


def test_next_synthesis_runs_during_previous_alignment(client, voice, monkeypatch):
    spans = slow_alignment(monkeypatch)
    ids = [client.post("/v1/syntheses", json=request(voice, f"Segment numéro {i}.")).json()["job_id"] for i in range(2)]
    first, second = (wait_job(client, j) for j in ids)
    assert first["status"] == second["status"] == "completed"
    for job in (first, second):
        assert [h["status"] for h in job["history"]] == ["queued", "loading", "synthesizing", "exporting", "aligning", "completed"]
        assert job["metrics"]["alignment_seconds"] >= .55 and "alignment_queue_seconds" in job["metrics"]
        assert job["metrics"]["processing_seconds"] >= job["metrics"]["alignment_seconds"]
    # Job 2 synthesized while job 1 was still aligning; job 1 completed only after its alignment.
    assert history_at(second, "synthesizing") < history_at(first, "completed")
    assert history_at(first, "completed") >= spans[0][1]
    assert spans[1][0] >= spans[0][1]  # alignments stay serial


def test_identical_request_waits_for_twin_and_hits_cache(client, voice, monkeypatch):
    slow_alignment(monkeypatch)
    manager = client.app.state.jobs.manager
    calls = []
    synth = manager.synthesize
    manager.synthesize = lambda *a: (calls.append(True), synth(*a))[1]
    ids = [client.post("/v1/syntheses", json=request(voice)).json()["job_id"] for _ in range(2)]
    first, second = (wait_job(client, j) for j in ids)
    assert first["status"] == second["status"] == "completed"
    assert len(calls) == 1 and second["metrics"]["synthesis_cache_hit"] is True
    assert second["audio_id"] == first["audio_id"]


def test_cancel_during_background_alignment(client, voice, monkeypatch):
    slow_alignment(monkeypatch, 1.0)
    j = client.post("/v1/syntheses", json=request(voice)).json()["job_id"]
    deadline = time.monotonic() + 5
    while client.get("/v1/jobs/" + j).json()["status"] != "aligning":
        assert time.monotonic() < deadline
        time.sleep(.02)
    client.post("/v1/jobs/" + j + "/cancel")
    job = wait_job(client, j)
    assert job["status"] == "cancelled" and job["audio_id"] is None


# ---------------------------------------------------------------- resident aligner (fake CLI)

FAKE_CLI = r'''
import argparse, json, os, sys, time
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("audio"); p.add_argument("--model"); p.add_argument("--device"); p.add_argument("--language")
p.add_argument("--word_timestamps"); p.add_argument("--output_format"); p.add_argument("--output_dir")
p.add_argument("--script_text_file")
a = p.parse_args()
name = Path(a.audio).stem
if "crash" in name: os._exit(3)
if "busy" in name: raise SystemExit("RESOURCE_BUSY: CPU ASR requires 3 GiB available RAM")
if "slow" in name: time.sleep(30)
text = Path(a.script_text_file).read_text(encoding="utf-8") if a.script_text_file else ""
out = {"segments": [{"words": [{"word": w, "start": i * .1, "end": i * .1 + .08} for i, w in enumerate(text.split())]}],
       "pps_metrics": {"pid": os.getpid()}}
Path(a.output_dir).mkdir(parents=True, exist_ok=True)
(Path(a.output_dir) / (name + ".json")).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
'''


@pytest.fixture
def fake_cli(cfg, tmp_path):
    scripts = tmp_path / "fake-scripts"
    scripts.mkdir()
    (scripts / "whisper_json.py").write_text(FAKE_CLI, encoding="utf-8")
    shutil.copy(ROOT / "scripts" / "whisper_serve.py", scripts / "whisper_serve.py")
    cfg["alignment"].update(command=sys.executable, script=str(scripts / "whisper_json.py"), timeout_seconds=10)
    aligner = ResidentAligner(cfg)
    yield cfg, aligner, tmp_path
    aligner.close()


def cli_args(cfg, tmp_path, name, out):
    audio = tmp_path / (name + ".wav")
    audio.write_bytes(b"RIFF")
    script = tmp_path / "script.txt"
    script.write_text("Bonjour à tous", encoding="utf-8")
    return [cfg["alignment"]["command"], "-B", cfg["alignment"]["script"], str(audio), "--model", "base.pt",
            "--device", "cpu", "--language", "fr", "--word_timestamps", "True", "--output_format", "json",
            "--output_dir", str(out), "--script_text_file", str(script)]


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.pop("pps_metrics")["pid"], data


def test_resident_output_matches_one_shot_and_stays_loaded(fake_cli):
    cfg, aligner, tmp = fake_cli
    subprocess.run(cli_args(cfg, tmp, "sample", tmp / "one"), check=True, capture_output=True, timeout=30)
    for out in ("res1", "res2"):
        aligner(cli_args(cfg, tmp, "sample", tmp / out), 10)
    one_pid, one = load(tmp / "one" / "sample.json")
    pid1, r1 = load(tmp / "res1" / "sample.json")
    pid2, r2 = load(tmp / "res2" / "sample.json")
    assert r1 == r2 == one and pid1 == pid2 != one_pid
    assert aligner.stats["resident_requests"] == 2 and aligner.stats["starts"] == 1
    assert aligner.last_mode == "resident"


def test_resident_crash_restarts_once_then_falls_back_to_one_shot(fake_cli):
    cfg, aligner, tmp = fake_cli
    args = cli_args(cfg, tmp, "crash", tmp / "out")
    with pytest.raises(subprocess.CalledProcessError) as e:
        aligner(args, 10)
    assert e.value.returncode == 3 and e.value.cmd == args  # the reference one-shot failure
    assert aligner.stats["restarts"] == 1 and aligner.stats["one_shot_requests"] == 1
    assert aligner.last_mode == "resident_fallback_one_shot"
    aligner(cli_args(cfg, tmp, "fine", tmp / "out2"), 10)  # a later request uses a fresh resident process
    assert aligner.last_mode == "resident"


def test_script_failure_reported_exactly_like_one_shot(fake_cli):
    cfg, aligner, tmp = fake_cli
    args = cli_args(cfg, tmp, "busy", tmp / "out")
    with pytest.raises(subprocess.CalledProcessError) as reference:
        subprocess.run(args, check=True, capture_output=True, timeout=30)
    with pytest.raises(subprocess.CalledProcessError) as resident:
        aligner(args, 10)
    assert str(resident.value) == str(reference.value)
    assert aligner.stats["one_shot_requests"] == 1


def test_resident_timeout_matches_one_shot_message(fake_cli):
    cfg, aligner, tmp = fake_cli
    args = cli_args(cfg, tmp, "slow", tmp / "out")
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as e:
        aligner(args, 1)
    assert time.monotonic() - started < 10
    assert str(e.value) == str(subprocess.TimeoutExpired(args, 1))
    assert aligner.process is None


def test_missing_wrapper_or_disabled_uses_one_shot(fake_cli):
    cfg, aligner, tmp = fake_cli
    cfg["service"]["resident_aligner"] = False
    aligner(cli_args(cfg, tmp, "sample", tmp / "out"), 10)
    assert aligner.last_mode == "one_shot" and aligner.process is None


def test_align_uses_runner_with_unchanged_command(cfg, tmp_path):
    from conftest import tone_bytes
    from pps.alignment import align
    audio = tmp_path / "master.wav"
    audio.write_bytes(tone_bytes(.5))
    (tmp_path / "base.pt").touch()
    cfg["alignment"].update(command="private-python", script="local-asr.py", model="base", model_dir=str(tmp_path))
    seen = []
    def runner(args, timeout):
        seen.append((args, timeout))
        (tmp_path / "master.json").write_text(json.dumps({"segments": [{"words": [{"word": "Bonjour", "start": 0, "end": .4}]}]}))
    result = align(audio, "Bonjour", "fr", .5, cfg["alignment"], tmp_path, runner=runner)
    args, timeout = seen[0]
    assert args[:4] == ["private-python", "-B", "local-asr.py", str(audio)] and timeout == cfg["alignment"]["timeout_seconds"]
    assert result["status"] == "asr_unverified"
