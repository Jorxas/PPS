"""Tests for PPS Step A alignment fixes: punctuation, candidate selection, long-form routing and bounds."""
import pytest
from pathlib import Path
from pps.alignment import valid_bounds, evaluate, boundary_evidence, align_long_form
from pps.jobs import segment_text

def test_valid_bounds_strictly_positive():
    # zero-duration word must be rejected
    bad_words = [{'word': 'test', 'start': 1.0, 'end': 1.0}]
    assert not valid_bounds(bad_words, 2.0)

    # positive duration word must be accepted
    good_words = [{'word': 'test', 'start': 1.0, 'end': 1.05}]
    assert valid_bounds(good_words, 2.0)

def test_segment_text_whisper_compatible_threshold():
    text_1175 = ("Pourquoi une petite habitude devient-elle si difficile à changer ? "
                 "Imagine que ton cerveau traverse chaque matin le même chemin. "
                 "Au début, tu dois réfléchir à chaque étape. "
                 "Avec la répétition, le trajet devient familier, et tu économises de l’attention. "
                 "C’est utile pour te préparer, travailler ou retrouver tes affaires. "
                 "Mais ce mécanisme conserve aussi les gestes que tu voudrais abandonner. "
                 "Une notification apparaît, tu prends ton téléphone, et quelques minutes disparaissent. "
                 "Pour modifier cette boucle, commence par repérer ce qui la déclenche. "
                 "Est-ce l’ennui, une pause, ou simplement un objet posé devant toi ? "
                 "Ensuite, prépare une autre action, courte et facile à démarrer. "
                 "Tu peux laisser un livre près du canapé, ranger ton téléphone dans une autre pièce, "
                 "ou noter la prochaine tâche sur une feuille. "
                 "Le changement ne repose pas seulement sur la volonté : il dépend aussi de ce que ton environnement rend accessible. "
                 "Choisis une seule modification, observe ce qui fonctionne, puis ajuste. "
                 "Une nouvelle habitude se construit avec des répétitions concrètes, pas avec une promesse parfaite. "
                 "Le prochain petit geste compte davantage que le grand plan que tu repousses.")
    segments = segment_text(text_1175, max_chars=450)
    assert len(segments) >= 3, f"Expected at least 3 segments for 1175 chars, got {len(segments)}"
    for s in segments:
        assert len(s["text"]) <= 450, f"Segment exceeded 450 chars: {len(s['text'])}"

def test_boundary_evidence_candidate_prioritization():
    # If cropped candidate has invalid bounds, forced_alignment with valid bounds must be retained
    analysis = {
        'duration_seconds': 10.0,
        'sustained_onset_seconds': 0.1,
        'silences': []
    }
    result = {
        'forced_alignment': {
            'words': [{'word': 'bonjour', 'start': 0.2, 'end': 0.8}],
            'timestamps_valid': True,
            'timestamp_provenance': 'forced_dtw'
        },
        'audio_sha256': 'dummy'
    }
    invalid_crop_candidate = {
        'words': [{'word': 'bonjour', 'start': 0.2, 'end': 0.2}],
        'timestamps_valid': False,
        'timestamp_provenance': 'cropped_dtw'
    }
    updated = boundary_evidence(result, analysis, invalid_crop_candidate)
    assert updated['review_alignment']['timestamps_valid']
    assert updated['review_alignment']['words'][0]['end'] == 0.8

