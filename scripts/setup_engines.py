"""Controlled, sequential installations. Run using the PPS API environment."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
import psutil

ROOT = Path(__file__).resolve().parents[1]


def run(engine, label, args):
    s = {"ram_available_gib": psutil.virtual_memory().available / 1024**3,
         "disk_free_gib": shutil.disk_usage(ROOT).free / 1024**3, "command": args}
    print(label, json.dumps(s), flush=True)
    if s["ram_available_gib"] < 2 or s["disk_free_gib"] < 20:
        raise RuntimeError("RESOURCE_BUSY: dependency installation requires >=2 GiB RAM and >=20 GiB disk")
    env = {**os.environ, "UV_CACHE_DIR": str(ROOT / "cache" / "uv"), "PIP_CACHE_DIR": str(ROOT / "cache" / "pip"),
           "UV_CONCURRENT_DOWNLOADS": "1", "UV_CONCURRENT_INSTALLS": "1", "UV_CONCURRENT_BUILDS": "1",
           "TEMP": str(ROOT / "runtime" / "tmp"), "TMP": str(ROOT / "runtime" / "tmp")}
    Path(env["TEMP"]).mkdir(parents=True, exist_ok=True)
    with (ROOT / "logs" / (engine + "-" + label + ".log")).open("w", encoding="utf-8") as log:
        log.write(json.dumps(s) + "\n")
        log.flush()
        subprocess.run(args, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)


def install(engine, dependencies_only=False):
    py = str(ROOT / ".venvs" / engine / "Scripts" / "python.exe")
    uv = shutil.which("uv")
    torch_version, cuda = ("2.6.0", "cu124") if engine == "chatterbox" else ("2.3.1", "cu121")
    base = [uv, "pip", "install", "--python", py, "--no-cache", "--build-constraint", str(ROOT / "requirements" / "build.txt")]
    rows = []
    for line in (ROOT / "requirements" / (engine + ".txt")).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, version = line.partition("==")
        url = "https://pypi.org/pypi/" + name + ("/" + version if version else "") + "/json"
        with urllib.request.urlopen(url, timeout=30) as response:
            info = json.load(response)["info"]
        rows.append({"name": name, "version": info["version"], "source": url,
                     "license": info.get("license_expression") or info.get("license"),
                     "license_classifiers": [c for c in info.get("classifiers", []) if c.startswith("License")]})
    (ROOT / "reports" / (engine + "-dependency-preflight.json")).write_text(json.dumps(rows, indent=2), encoding="utf-8")
    lock = ROOT / "requirements" / (engine + ".lock.txt")
    if lock.exists() and not dependencies_only:
        run(engine, "locked-install", base + ["-r", str(lock), "--extra-index-url", "https://download.pytorch.org/whl/" + cuda])
    else:
        if not dependencies_only:
            run(engine, "torch-install", base + ["torch==" + torch_version, "torchaudio==" + torch_version,
                                               "--index-url", "https://download.pytorch.org/whl/" + cuda, "--only-binary", ":all:"])
        run(engine, "deps-repair" if dependencies_only else "deps-install", base + ["-r", "requirements/" + engine + ".txt", "--index-url", "https://pypi.org/simple"])
        if engine == "chatterbox":
            run(engine, "perth-install", base + ["./vendor/Perth", "--no-deps"])
            run(engine, "source-install", base + ["./vendor/chatterbox", "--no-deps"])
    frozen = subprocess.check_output([uv, "pip", "freeze", "--python", py], cwd=ROOT, text=True)
    lines = []
    for line in frozen.splitlines():
        if line.startswith("chatterbox-tts @ file:"):
            line = "./vendor/chatterbox"
        elif line.startswith("resemble-perth @ file:"):
            line = "./vendor/Perth"
        lines.append(line)
    lock.write_text("\n".join(lines)+"\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("engine", choices=["chatterbox", "cosyvoice"])
    parser.add_argument("--dependencies-only", action="store_true", help="Targeted repair; preserve installed CUDA torch")
    args = parser.parse_args()
    install(args.engine, args.dependencies_only)
