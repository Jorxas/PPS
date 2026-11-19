"""Record registry metadata for the declared engine packages, before dependency stage."""
import concurrent.futures
import json
import urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def package(line):
    name, _, version = line.partition("==")
    url = "https://pypi.org/pypi/" + name + ("/" + version if version else "") + "/json"
    with urllib.request.urlopen(url, timeout=30) as r:
        info = json.load(r)["info"]
    return {"name": name, "version": info["version"], "source": url,
            "license": info.get("license_expression") or info.get("license"),
            "license_classifiers": [c for c in info.get("classifiers", []) if c.startswith("License")]}


if __name__ == "__main__":
    for engine in ("chatterbox", "cosyvoice"):
        lines = [l.strip() for l in (ROOT/"requirements"/(engine+".txt")).read_text().splitlines() if l.strip() and not l.startswith("#")]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(package, lines))
        (ROOT/"reports"/(engine+"-dependency-preflight.json")).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(engine, len(rows), "registry records saved")
