"""File-based private IPC with process death releasing model and GPU allocations."""
import json
import os
import sys
import time
import traceback
from pathlib import Path


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def guarded_load(adapter, cfg, record):
    from pps.resources import guard
    adapter.import_dependencies()
    record("after_engine_imports_before_weights")
    guard(cfg)
    adapter.load()


def main():
    ipc, engine = Path(sys.argv[1]), sys.argv[2]
    cfg = json.loads((ipc / "config.json").read_text(encoding="utf-8"))
    # A crashed API must not leave a model-owning orphan worker alive.
    import psutil
    import threading
    parents = [psutil.Process(pid) for pid in {int(os.environ.get("PPS_PARENT_PID", os.getppid())), os.getppid()}]

    def watch_parent():
        while True:
            time.sleep(1)
            try:
                if any(not parent.is_running() for parent in parents):
                    os._exit(70)
            except psutil.Error:
                os._exit(70)
    threading.Thread(target=watch_parent, daemon=True).start()
    from pps.resources import ProcessLock, guard
    # Worker owns a second OS lock so even a brief orphan cannot overlap a new model.
    model_lock = ProcessLock(Path(cfg["root"]) / "runtime" / "model.lock")
    try:
        model_lock.acquire()
    except Exception as e:
        atomic_json(ipc / "loaded.json", {"ok": False, "code": "RESOURCE_BUSY", "message": str(e)})
        return
    sys.path.insert(0, cfg[engine]["source"])
    if engine == "cosyvoice":
        sys.path.insert(0, str(Path(cfg[engine]["source"]) / "third_party" / "Matcha-TTS"))
    from pps.engines import ChatterboxAdapter, CosyVoiceAdapter
    adapter = (ChatterboxAdapter if engine == "chatterbox" else CosyVoiceAdapter)(cfg[engine])
    from pps.memory import memory_snapshot
    from pps.resources import snapshot
    stages = []
    def record(stage):
        stages.append({"stage": stage, "at": time.time(), **memory_snapshot(), "resources": snapshot(cfg["root"])})
        atomic_json(ipc / "startup.json", {"engine": engine, "stages": stages})
    try:
        record("before_torch_import")
        import torch
        torch.set_num_threads(4)
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable in isolated engine environment")
        record("after_torch_import")
        guard(cfg)  # Do not begin further imports if torch already used the safety margin.
        started = time.perf_counter()
        torch.cuda.reset_peak_memory_stats()
        guarded_load(adapter, cfg, record)
        torch.cuda.synchronize()
        record("weights_loaded")
        atomic_json(ipc / "loaded.json", {"ok": True, "load_seconds": time.perf_counter() - started,
                    "torch": torch.__version__, "cuda": torch.version.cuda, "startup_stages": stages,
                    "cuda_load_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                    "cuda_load_peak_reserved_bytes": torch.cuda.max_memory_reserved()})
    except Exception as e:
        traceback.print_exc()
        record("load_failed")
        atomic_json(ipc / "loaded.json", {"ok": False, "code": getattr(e, "code", "CUDA_OOM" if "out of memory" in str(e).lower() else "ENGINE_LOAD_FAILED"), "message": str(e), "details": getattr(e, "details", {})})
        return
    last_ref = None
    while True:
        if (ipc / "stop").exists():
            adapter.unload()
            return
        request_path = ipc / "request.json"
        if not request_path.exists():
            time.sleep(.1)
            continue
        req = json.loads(request_path.read_text(encoding="utf-8"))
        request_path.unlink()
        if req.get("action") == "probe":
            atomic_json(ipc / "response.json", {"ok": True})
            continue
        try:
            torch.cuda.reset_peak_memory_stats()
            torch.manual_seed(req.get("seed", 1986))
            start = time.perf_counter()
            ref = req.get("reference")
            ref_key = (ref["id"] if ref else "builtin", req.get('voice_version'),
                       req.get('reference_hash'), req["options"].get("exaggeration", .5))
            cache_hit = ref_key == last_ref
            if not cache_hit:
                adapter.prepare_voice(ref, req["options"])
                last_ref = ref_key
            prepared = time.perf_counter()
            waveform, sr = adapter.synthesize(req["text"], req["language"], req["options"])
            torch.cuda.synchronize()
            finished = time.perf_counter()
            import soundfile as sf
            samples = waveform.detach().cpu().squeeze().numpy()
            float_peak = float(abs(samples).max())
            float_over = int(((samples>=1) | (samples < -1)).sum())
            sf.write(req["output"], samples, sr, subtype="PCM_16")
            atomic_json(ipc / "response.json", {"ok": True, "prepare_seconds": prepared - start,
                "synthesis_seconds": finished - prepared, "cache_hit": cache_hit,
                'pre_pcm_peak_amplitude':float_peak,'pre_pcm_out_of_range_samples':float_over,
                "cuda_peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                "cuda_peak_reserved_bytes": torch.cuda.max_memory_reserved(), "sample_rate": sr})
        except Exception as e:
            traceback.print_exc()
            atomic_json(ipc / "response.json", {"ok": False, "code": "CUDA_OOM" if "out of memory" in str(e).lower() else "SYNTHESIS_FAILED", "message": str(e)})
            # Do not reuse potentially invalid CUDA state after failure.
            adapter.unload()
            return


if __name__ == "__main__":
    main()
