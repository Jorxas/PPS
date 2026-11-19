from pathlib import Path
import pytest
from pps.errors import PPSError
from pps.manager import EngineManager
from pps.workers import atomic_json


def test_switch_unloads_previous_before_guard(cfg, monkeypatch):
    m = EngineManager(cfg)
    events = []
    monkeypatch.setattr(m, "unload", lambda:events.append("unload"))
    def busy(config):
        events.append("guard")
        raise PPSError("RESOURCE_BUSY", "RAM", 503)
    monkeypatch.setattr("pps.manager.guard", busy)
    monkeypatch.setattr("pps.manager.subprocess.Popen", lambda *a,**k:pytest.fail("Must not launch under insufficient RAM"))
    with pytest.raises(PPSError, match="RAM"):
        m.ensure("cosyvoice", lambda:False)
    assert events == ["unload", "guard"]


class LiveProcess:
    def poll(self):
        return None


def test_running_cancellation_stops_worker(cfg, tmp_path, monkeypatch):
    m = EngineManager(cfg)
    m.ipc, m.process = tmp_path, LiveProcess()
    stops = []
    monkeypatch.setattr(m, "unload", lambda:stops.append(True))
    with pytest.raises(PPSError) as e:
        m.wait("response.json", lambda:True)
    assert e.value.code == "CANCELLED" and stops == [True]


def test_worker_timeout_is_bounded(cfg, tmp_path, monkeypatch):
    cfg["service"]["worker_timeout_seconds"] = 0
    m = EngineManager(cfg)
    m.ipc, m.process = tmp_path, LiveProcess()
    stops = []
    monkeypatch.setattr(m, "unload", lambda:stops.append(True))
    with pytest.raises(PPSError) as e:
        m.wait("response.json", lambda:False)
    assert e.value.code == "WORKER_TIMEOUT" and stops == [True]


def test_worker_oom_does_not_reuse_state(cfg, tmp_path, monkeypatch):
    m = EngineManager(cfg)
    m.ipc, m.process = tmp_path, LiveProcess()
    atomic_json(tmp_path/"response.json", {"ok":False, "code":"CUDA_OOM", "message":"OOM"})
    stops = []
    monkeypatch.setattr(m, "unload", lambda:stops.append(True))
    with pytest.raises(PPSError) as e:
        m.wait("response.json", lambda:False)
    assert e.value.code == "CUDA_OOM" and stops == [True]
    assert not (tmp_path/"response.json").exists()


def test_worker_resource_error_preserves_details(cfg, tmp_path, monkeypatch):
    m = EngineManager(cfg)
    m.ipc, m.process = tmp_path, LiveProcess()
    atomic_json(tmp_path/"loaded.json", {"ok":False, "code":"RESOURCE_BUSY", "message":"RAM",
        "details":{"observed":{"ram_available_gib":3},"thresholds":{"min_ram_gib":6}}})
    monkeypatch.setattr(m, "unload", lambda:None)
    with pytest.raises(PPSError) as e:
        m.wait("loaded.json", lambda:False)
    assert e.value.status == 503 and e.value.retryable
    assert e.value.details["observed"]["ram_available_gib"] == 3


def test_unload_stops_windows_venv_launcher_and_child(cfg, tmp_path):
    import subprocess
    import time
    import psutil
    script = tmp_path / "owned-fixture.py"
    script.write_text("import time\nwhile True: time.sleep(.1)\n")
    py = cfg["chatterbox"]["python"]
    m = EngineManager(cfg)
    m.ipc = tmp_path
    m.process = subprocess.Popen([py, "-B", str(script)], creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    try:
        time.sleep(.3)
        parent = psutil.Process(m.process.pid)
        descendants = parent.children(recursive=True)
        m.unload()
        assert not parent.is_running()
        assert all(not p.is_running() for p in descendants)
    finally:
        m.unload()
