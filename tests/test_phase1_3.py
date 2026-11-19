import pytest
from pps.duration import estimate_duration, check_target_duration, post_generation_metrics
from pps.jobs import segment_text
from conftest import tone_bytes, wait_job


def test_duration_estimation_unit():
    # Test short French sentence
    text = "Bonjour, ceci est un test de synthèse vocale locale en français."
    res = estimate_duration(text, language="fr", engine="chatterbox")
    assert res["word_count"] == 11
    assert res["char_count"] == len(text)
    assert res["estimated_duration_seconds"] > 0
    assert res["effective_wpm"] == 123


def test_target_duration_check_too_short():
    # 11 words intended for 60 seconds
    text = "Bonjour, ceci est un test de synthèse vocale locale en français."
    res = check_target_duration(text, target_seconds=60, language="fr", engine="chatterbox")
    assert res["duration_status"] == "too_short"
    assert res["advice"]["action"] == "add_text"
    assert res["advice"]["words_to_add"] > 50
    assert "Ajoutez environ" in res["advice"]["message"]


def test_target_duration_check_compatible():
    # Text of ~120 words for ~60 seconds
    text = " ".join(["mot"] * 123)
    res = check_target_duration(text, target_seconds=60, language="fr", engine="chatterbox")
    assert res["duration_status"] == "compatible"
    assert res["advice"]["action"] == "none"


def test_segment_text_preserves_all_content():
    # Create long text of 5000 characters
    paragraphs = [
        f"Paragraphe {i}. Voici une phrase complète avec plusieurs mots pour tester la segmentation. Est-ce que tout fonctionne bien ?"
        for i in range(40)
    ]
    full_text = "\n\n".join(paragraphs)
    assert len(full_text) > 4000

    segments = segment_text(full_text, max_chars=1200)
    assert len(segments) >= 4
    # All segments must be <= 1200 chars
    for s in segments:
        assert len(s["text"]) <= 1200

    # Ensure no dropped words
    original_words = full_text.split()
    segmented_words = [w for s in segments for w in s["text"].split()]
    assert original_words == segmented_words


def test_duration_estimate_api(client):
    r = client.post("/v1/duration-estimate", json={
        "text": "Bonjour tout le monde. Ceci est un test d'estimation de durée pour MainClip.",
        "language": "fr",
        "engine": "chatterbox",
        "target_duration_seconds": 60
    })
    assert r.status_code == 200
    data = r.json()
    assert "estimated_duration_seconds" in data
    assert data["target_duration_seconds"] == 60
    assert data["duration_status"] == "too_short"
    assert data["advice"]["action"] == "add_text"


def test_synthesis_quick_alignment_mode(client, voice):
    # Test quick mode skips whisper
    r = client.post("/v1/syntheses", json={
        "voice_id": voice["id"],
        "text": "Aperçu rapide sans whisper.",
        "engine": "chatterbox",
        "language": "fr",
        "alignment_mode": "quick"
    })
    assert r.status_code == 202
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "completed"
    alignment = client.get("/v1/audio/" + job["audio_id"] + "/alignment").json()
    assert alignment["status"] == "skipped_quick_mode"
    meta = client.get("/v1/audio/" + job["audio_id"] + "/metadata").json()
    assert meta["metrics"]["alignment_seconds"] == 0.0



def test_synthesis_over_1500_chars(client, voice):
    # Text > 1500 characters (e.g. 3500 chars)
    long_text = " ".join([f"Phrase numéro {i} pour valider le support des narrations longues." for i in range(60)])
    assert len(long_text) > 3000

    r = client.post("/v1/syntheses", json={
        "voice_id": voice["id"],
        "text": long_text,
        "engine": "chatterbox",
        "language": "fr",
        "target_duration_seconds": 120
    })
    assert r.status_code == 202
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "completed"
    assert job["metrics"]["segments_count"] > 1
    assert "duration_meta" in job["metrics"]
    assert job["metrics"]["duration_meta"]["target_duration_seconds"] == 120
