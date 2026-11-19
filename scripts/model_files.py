"""Plan and download only required checkpoints, pinned to immutable HF revisions."""
import argparse
import hashlib
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "chatterbox": ("ResembleAI/chatterbox", "chatterbox-v3",
        {"ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen.pt", "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "README.md"}),
    "cosyvoice": ("FunAudioLLM/Fun-CosyVoice3-0.5B-2512", "Fun-CosyVoice3-0.5B-2512",
        {"cosyvoice3.yaml", "llm.pt", "flow.pt", "hift.pt", "campplus.onnx", "speech_tokenizer_v3.onnx", "spk2info.pt", "README.md", "LICENSE"}),
}


def get_json(url):
    with urllib.request.urlopen(url, timeout=45) as r:
        return json.load(r)


def plan(engine, revision="main"):
    repo, directory, allowed = MODELS[engine]
    metadata = get_json("https://huggingface.co/api/models/" + repo + "/revision/" + revision + "?blobs=true")
    files = []
    for f in metadata["siblings"]:
        name = f["rfilename"]
        # Upstream Qwen2Encoder calls from_pretrained before llm.pt is loaded.
        # Therefore its BlankEN initialization checkpoint is actually required.
        blank = name.startswith("CosyVoice-BlankEN/") and name.endswith((".json", ".txt", ".jinja", ".safetensors"))
        if name in allowed or (engine == "cosyvoice" and blank):
            files.append({"path": name, "bytes": f.get("size", f.get("lfs", {}).get("size", 0)),
                          "sha256": f.get("lfs", {}).get("sha256"),
                          "git_blob_sha1": f.get("blobId") if not f.get("lfs") else None})
    return {"engine": engine, "repo": repo, "revision": metadata["sha"], "directory": directory,
            "license": metadata.get("cardData", {}).get("license"), "files": files,
            "total_bytes": sum(f["bytes"] for f in files), "timestamp": time.time()}


def download(p):
    import psutil
    if psutil.virtual_memory().available < 2 * 1024**3 or shutil.disk_usage(ROOT).free < p["total_bytes"] + 10 * 1024**3:
        raise RuntimeError("RESOURCE_BUSY: insufficient RAM/disk for bounded checkpoint download")
    target = ROOT / "models" / p["directory"]
    target.mkdir(parents=True, exist_ok=True)
    for f in p["files"]:
        dest = target / f["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size == f["bytes"]:
            with dest.open("rb") as existing:
                sha = hashlib.file_digest(existing, "sha256").hexdigest()
            if f["sha256"] and sha == f["sha256"]:
                f["verified_sha256"] = sha
                print("Verified existing", f["path"], flush=True)
                continue
        print("Downloading", f["path"], f["bytes"], "bytes", flush=True)
        url = "https://huggingface.co/" + p["repo"] + "/resolve/" + p["revision"] + "/" + f["path"]
        part = dest.with_suffix(dest.suffix + ".part")
        h, count = hashlib.sha256(), 0
        with urllib.request.urlopen(url, timeout=90) as source, part.open("wb") as out:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                out.write(block)
                h.update(block)
                count += len(block)
        if count != f["bytes"] or f["sha256"] and h.hexdigest() != f["sha256"]:
            raise RuntimeError("CHECKSUM_MISMATCH " + f["path"])
        if f.get("git_blob_sha1"):
            blob = hashlib.sha1(b"blob " + str(count).encode() + b"\0" + part.read_bytes()).hexdigest()
            if blob != f["git_blob_sha1"]:
                raise RuntimeError("GIT_BLOB_MISMATCH " + f["path"])
        f["verified_sha256"] = h.hexdigest()
        part.replace(dest)
    (target / "manifest.json").write_text(json.dumps(p, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("engine", choices=MODELS)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--refresh-plan", action="store_true", help="Recompute file selection at the already pinned revision")
    args = parser.parse_args()
    file = ROOT / "reports" / (args.engine + "-download-plan.json")
    p = json.loads(file.read_text(encoding="utf-8")) if file.exists() else plan(args.engine)
    if args.refresh_plan:
        p = plan(args.engine, p["revision"])
    file.write_text(json.dumps(p, indent=2), encoding="utf-8")
    print(json.dumps(p, indent=2), flush=True)
    if args.download:
        download(p)
