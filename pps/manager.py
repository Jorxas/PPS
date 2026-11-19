import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
import psutil
from .engines import status
from .errors import PPSError
from .resources import guard, loaded_ram_floor, snapshot
from .storage import identity
from .workers import atomic_json


class EngineManager:
    def __init__(self, cfg):
        self.cfg, self.process, self.engine = cfg, None, None
        self.ipc, self.log, self.loaded = None, None, None
        self.lock = threading.RLock()
        self.last_used = time.monotonic()
        self.peak_rss = 0
        self.peak_system_ram_used_gib = 0
        self.peak_gpu_used_gib = 0
        self.last_sample = 0

    def sample(self):
        if self.process and self.process.poll() is None:
            try:
                proc = psutil.Process(self.process.pid)
                rss = proc.memory_info().rss
                rss += sum(p.memory_info().rss for p in proc.children(recursive=True) if p.is_running())
                self.peak_rss = max(self.peak_rss, rss)
            except psutil.Error:
                pass
        if time.monotonic() - self.last_sample >= 1:
            self.last_sample = time.monotonic()
            s = snapshot(self.cfg["paths"]["data"])
            self.peak_system_ram_used_gib = max(self.peak_system_ram_used_gib, s["ram_total_gib"] - s["ram_available_gib"])
            if s["gpu"]:
                self.peak_gpu_used_gib = max(self.peak_gpu_used_gib, s["gpu"]["total_gib"] - s["gpu"]["free_gib"])

    def wait(self, name, cancelled):
        started = time.monotonic()
        file = self.ipc / name
        while not file.exists():
            if cancelled():
                self.unload()
                raise PPSError("CANCELLED", "Génération annulée.", 409)
            if not self.process or self.process.poll() is not None:
                raise PPSError("WORKER_EXITED", "Le worker s'est arrêté ; consulter le journal moteur.", 500)
            if time.monotonic() - started > self.cfg["service"]["worker_timeout_seconds"]:
                self.unload()
                raise PPSError("WORKER_TIMEOUT", "Délai moteur dépassé.", 504)
            self.sample()
            time.sleep(.15)
        result = json.loads(file.read_text(encoding="utf-8"))
        file.unlink()
        if not result["ok"]:
            self.unload()
            raise PPSError(result.get("code", "ENGINE_FAILED"), result.get("message", "Erreur moteur."),
                           503 if result.get("code") == "RESOURCE_BUSY" else 500, result.get("details", {}),
                           retryable=result.get("code") == "RESOURCE_BUSY")
        return result

    def ensure(self, engine, cancelled):
        if self.engine == engine and self.process and self.process.poll() is None and self.loaded:
            desired = status(engine,self.cfg)['model_revision']
            source_file = Path(self.cfg['root'])/'reports'/'sources.lock.json'
            source = json.loads(source_file.read_text(encoding='utf-8'))[engine]['commit'] if source_file.exists() else None
            if desired != self.loaded.get('model_revision') or source != self.loaded.get('source_revision'):
                self.unload()
        if self.engine == engine and self.process and self.process.poll() is None:
            # The resident worker's own RSS is already counted: only the lower resident floor applies.
            # A fresh load (below) still requires the full min_ram_gib through guard().
            if psutil.virtual_memory().available / 1024**3 < loaded_ram_floor(self.cfg):
                self.unload()
                guard(self.cfg)
                return self.ensure(engine,cancelled)
            return {"load_seconds": 0, "model_cache_hit": True, "environment": self.loaded}
        self.unload()
        self.peak_rss, self.peak_system_ram_used_gib, self.peak_gpu_used_gib = 0, 0, 0
        # Resource guard must run before any heavyweight subprocess starts.
        guard(self.cfg)
        current = status(engine, self.cfg)
        if not current["ready_to_attempt_load"]:
            raise PPSError("ENGINE_NOT_READY", "Environnement ou checkpoints moteur non prêts.", 503, current)
        if cancelled():
            raise PPSError("CANCELLED", "Génération annulée.", 409)
        self.ipc = Path(self.cfg["paths"]["data"]) / "workers" / identity("worker")
        self.ipc.mkdir(parents=True)
        atomic_json(self.ipc / "config.json", self.cfg)
        env = {**os.environ, "PYTHONPATH": self.cfg["root"], "PYTHONUNBUFFERED": "1",
               "PPS_PARENT_PID": str(os.getpid()),
               "HF_HOME": self.cfg["paths"]["cache"] + "/hf", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
               "XDG_CACHE_HOME": self.cfg["paths"]["cache"], "TORCH_HOME": self.cfg["paths"]["cache"] + "/torch"}
        env.update(MPLCONFIGDIR=self.cfg["paths"]["cache"] + "/matplotlib", NUMBA_CACHE_DIR=self.cfg["paths"]["cache"] + "/numba",
                   MODELSCOPE_CACHE=self.cfg["paths"]["cache"] + "/modelscope")
        self.log = open(Path(self.cfg["paths"]["logs"]) / (self.ipc.name + "-" + engine + ".log"), "w", encoding="utf-8")
        self.process = subprocess.Popen([self.cfg[engine]["python"], "-B", "-m", "pps.workers", str(self.ipc), engine],
            cwd=self.cfg["root"], env=env, stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.engine = engine
        result = self.wait("loaded.json", cancelled)
        self.loaded = result
        revisions = Path(self.cfg["root"]) / "reports" / "sources.lock.json"
        source_revision = json.loads(revisions.read_text(encoding="utf-8"))[engine]["commit"] if revisions.exists() else None
        self.loaded.update(source_revision=source_revision, model_revision=current["model_revision"])
        self.last_used = time.monotonic()
        return {"load_seconds": result["load_seconds"], "model_cache_hit": False, "environment": self.loaded}

    def synthesize(self, request, output, cancelled):
        if (self.ipc / "response.json").exists():
            (self.ipc / "response.json").unlink()
        atomic_json(self.ipc / "request.json", {**request, "output": str(output)})
        result = self.wait("response.json", cancelled)
        self.sample()
        self.last_used = time.monotonic()
        return {**result, "worker_peak_rss_bytes_observed": self.peak_rss,
                "system_ram_peak_used_gib_observed": self.peak_system_ram_used_gib,
                "gpu_peak_used_gib_observed": self.peak_gpu_used_gib, "resource_sampling_seconds": 1.0}

    def unload(self):
        process = self.process
        if process:
            if process.poll() is None:
                if self.ipc:
                    (self.ipc / "stop").touch()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # Windows venv python.exe can be a launcher whose child owns CUDA.
                    # Terminating only that launcher would leave inference running.
                    try:
                        owner = psutil.Process(process.pid)
                        owned = owner.children(recursive=True) + [owner]
                    except psutil.NoSuchProcess:
                        owned = []
                    for proc in owned:
                        try:
                            proc.terminate()
                        except psutil.NoSuchProcess:
                            pass
                    _, alive = psutil.wait_procs(owned, timeout=5)
                    for proc in alive:
                        try:
                            proc.kill()
                        except psutil.NoSuchProcess:
                            pass
                    _, alive = psutil.wait_procs(alive, timeout=5)
                    if alive:
                        raise PPSError("WORKER_STOP_FAILED", "Le worker précédent est encore actif.", 503)
                    process.wait(timeout=5)
            if self.log:
                self.log.close()
        self.process, self.engine, self.log, self.loaded = None, None, None, None
