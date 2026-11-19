import copy
import io
import math
import struct
import time
import sys
import wave
import pytest
from fastapi.testclient import TestClient
from pps.api import create_app
from pps.config import load_config
from pps.errors import PPSError


def tone_bytes(seconds=8, rate=24000):
    out = io.BytesIO()
    with wave.open(out, "wb") as f:
        f.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        f.writeframes(b"".join(struct.pack("<h", int(1000*math.sin(i*2*math.pi*220/rate))) for i in range(int(seconds*rate))))
    return out.getvalue()


class FakeManager:
    """Contract-only fixture. Never exposed by production APIs; not speech synthesis."""
    def __init__(self):
        self.engine = None
        self.last_used = time.monotonic()
        self.loads = []
        self.delay = .03

    def ensure(self, engine, cancelled):
        if self.engine != engine:
            self.loads.append(engine)
            self.engine = engine
        return {"load_seconds": .001, "model_cache_hit": False}

    def synthesize(self, request, output, cancelled):
        end = time.monotonic() + self.delay
        while time.monotonic() < end:
            if cancelled():
                raise PPSError("CANCELLED", "Fixture cancelled", 409)
            time.sleep(.005)
        output.write_bytes(tone_bytes(.4))
        return {"synthesis_seconds": self.delay, "prepare_seconds": 0, "cache_hit": False,
                "worker_peak_rss_bytes_observed": 0, "cuda_peak_allocated_bytes": 0}

    def unload(self):
        self.engine = None


@pytest.fixture
def cfg(tmp_path):
    config = load_config()
    config = copy.deepcopy(config)
    # Prozess-Fixtures benötigen keine installierten Sprachmodell-Umgebungen.
    config['chatterbox']['python'] = sys.executable
    config["alignment"]["command"] = ""  # Never load production ASR in contract fixtures.
    config["root"] = str(tmp_path)
    for key in config["paths"]:
        path = tmp_path / key
        path.mkdir()
        config["paths"][key] = str(path)
    (tmp_path / "runtime").mkdir()
    (tmp_path / "pps").mkdir()
    (tmp_path / "pps" / "web.html").write_text("<html>PPS test fixture</html>")
    config["service"]["resource_retry_seconds"] = 0
    config["service"]["worker_idle_seconds"] = 100
    return config


@pytest.fixture
def client(cfg):
    manager = FakeManager()
    app = create_app(cfg, manager=manager)
    with TestClient(app) as client:
        yield client


def import_tone(client, transcript="Un signal synthétique, sans voix humaine.", key=None):
    return client.post("/v1/voices", data={"name": "TEST ONLY · synthetic signal", "language": "fr",
        "transcription": transcript, "authorized": "true", "declaration": "Fixture de contrat : signal synthétique, aucun locuteur réel."},
        files={"file": ("fixture.wav", tone_bytes(), "audio/wav")},
        headers={"Idempotency-Key": key} if key else {})


@pytest.fixture
def voice(client):
    response = import_tone(client)
    assert response.status_code == 201, response.text
    return response.json()


def wait_job(client, identifier, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get("/v1/jobs/" + identifier).json()
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(.025)
    raise AssertionError("Fixture job timed out")
