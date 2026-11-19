"""Collect persisted real job measurements; absent measurements remain null."""
import time


def collect(store):
    results = []
    for job in store.list("jobs"):
        m = job["metrics"]
        results.append({"job_id": job["id"], "engine": job["request"]["engine"], "status": job["status"],
            "provenance": "production_job", "source_revision": m.get("environment", {}).get("source_revision"),
            "model_revision": m.get("environment", {}).get("model_revision"),
            **{key: m.get(key) for key in ("load_seconds", "prepare_seconds", "synthesis_seconds", "duration_seconds",
                "real_time_factor", "alignment_seconds", "total_api_seconds", "worker_peak_rss_bytes_observed",
                "cuda_peak_allocated_bytes", "cuda_peak_reserved_bytes", "gpu_peak_used_gib_observed", "cache_hit")},
            "error": job["error"]})
    return {"generated_at": time.time(), "results": results,
            "quality_ranking": None, "note": "Speed is not a voice-quality score. Contract fixtures are excluded."}
