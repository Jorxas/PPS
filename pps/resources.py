import csv
import io
import shutil
import subprocess
import psutil
from .errors import PPSError

GIB = 1024 ** 3


def snapshot(path):
    mem = psutil.virtual_memory()
    result = {"ram_total_gib": mem.total / GIB, "ram_available_gib": mem.available / GIB,
              "disk_free_gib": shutil.disk_usage(path).free / GIB,
              "gpu": None, "gpu_error": None}
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True,
                           timeout=8, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        name, total, free, driver = next(csv.reader(io.StringIO(p.stdout)))
        result["gpu"] = {"name": name.strip(), "total_gib": float(total) / 1024,
                          "free_gib": float(free) / 1024, "driver": driver.strip()}
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration) as e:
        result["gpu_error"] = str(e)
    return result


def loaded_ram_floor(cfg):
    """Available-RAM floor for KEEPING an already-loaded worker (its own RSS is already counted).

    min_ram_gib remains the threshold for a fresh load (guard). Same default as MainClip's client.
    """
    limits = cfg["resources"]
    return float(limits.get("min_ram_gib_loaded", min(2.0, limits["min_ram_gib"])))


def guard(cfg):
    s = snapshot(cfg["paths"]["data"])
    limits = cfg["resources"]
    good = (s["ram_available_gib"] >= limits["min_ram_gib"] and
            s["disk_free_gib"] >= limits["min_disk_gib"] and s["gpu"] and
            s["gpu"]["free_gib"] >= limits["min_vram_gib"])
    if not good:
        raise PPSError("RESOURCE_BUSY", "Ressources insuffisantes pour charger le moteur.",
                       503, {"observed": s, "thresholds": limits}, retryable=True)
    return s


class ProcessLock:
    """OS-released lock shared by API and diagnostic tools in this PPS checkout."""
    def __init__(self, path):
        self.path, self.file = path, None

    def acquire(self):
        try:
            self.file = open(self.path, "a+b")
            # Windows byte locks also block reads: inspect length without reading byte 0.
            if __import__("os").fstat(self.file.fileno()).st_size == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if __import__("os").name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if self.file:
                self.file.close()
            self.file = None
            raise PPSError("RESOURCE_BUSY", "Une autre instance PPS détient le GPU.", 503) from e

    def release(self):
        if self.file:
            self.file.close()
            self.file = None
