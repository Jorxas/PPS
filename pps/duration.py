"""Pre-generation duration estimation and target duration control.

Estimates speech duration from text characteristics and observed engine
performance. Does NOT modify synthesis parameters silently — only advises.
"""
import re
import math

# Empirical average words-per-minute for narration
DEFAULT_WPM_FR = 150
DEFAULT_WPM_EN = 160

# Per-engine observed WPM from benchmarks
ENGINE_WPM = {
    'chatterbox': {'fr': 123, 'en': 135},
    'cosyvoice': {'fr': 113, 'en': 125},
}


def count_words(text: str) -> int:
    """Count words in text, handling French apostrophes and contractions."""
    if not text:
        return 0
    # Clean text and split on whitespace and punctuation marks that separate words
    cleaned = re.sub(r"[^\w\s'’-]", " ", text, flags=re.UNICODE)
    words = [w for w in re.split(r"\s+", cleaned) if w.strip()]
    return len(words)


def estimate_duration(text: str, language: str = 'fr', engine: str | None = None, speed: float = 1.0) -> dict:
    """Estimate speech duration in seconds from text.

    Returns min/expected/max estimates based on typical speaking rates.
    """
    word_count = count_words(text)
    char_count = len(text.strip()) if text else 0

    if engine and engine in ENGINE_WPM:
        base_wpm = ENGINE_WPM[engine].get(language, ENGINE_WPM[engine]['fr'])
    else:
        base_wpm = DEFAULT_WPM_FR if language == 'fr' else DEFAULT_WPM_EN

    # Adjust for speed parameter
    speed = max(0.5, min(2.0, speed or 1.0))
    effective_wpm = base_wpm * speed

    # Base duration from word count
    expected_seconds = (word_count / effective_wpm) * 60 if effective_wpm > 0 else 0

    # Add estimates for pauses at sentence boundaries (. ! ? …)
    sentence_count = len(re.findall(r'[.!?…]+', text)) if text else 0
    pause_seconds = sentence_count * 0.35  # ~350ms per sentence break

    # Paragraph breaks add longer pauses
    paragraph_count = len(re.findall(r'\n\s*\n', text)) if text else 0
    pause_seconds += paragraph_count * 0.50  # ~500ms per paragraph break

    expected_seconds += pause_seconds

    return {
        'word_count': word_count,
        'char_count': char_count,
        'sentence_count': sentence_count,
        'paragraph_count': paragraph_count,
        'estimated_duration_seconds': round(expected_seconds, 1),
        'estimated_duration_min_seconds': round(expected_seconds * 0.80, 1),
        'estimated_duration_max_seconds': round(expected_seconds * 1.25, 1),
        'effective_wpm': round(effective_wpm, 1),
        'engine': engine,
        'language': language,
        'speed': speed,
    }


def check_target_duration(text: str, target_seconds: float, language: str = 'fr', engine: str | None = None, speed: float = 1.0) -> dict:
    """Compare estimated duration against a target.

    Returns analysis with status and advisory recommendations.
    Modes / Actions:
    - compatible: within ±15% of target
    - too_short: script should be expanded
    - too_long: script should be shortened
    """
    est = estimate_duration(text, language, engine, speed)
    estimated = est['estimated_duration_seconds']
    word_count = est['word_count']
    effective_wpm = est['effective_wpm']

    diff = round(estimated - target_seconds, 1)
    ratio = round(estimated / target_seconds, 2) if target_seconds > 0 else 1.0

    if 0.85 <= ratio <= 1.15:
        status = 'compatible'
        advice = {
            'action': 'none',
            'message': f'La durée estimée ({estimated}s) est compatible avec la cible ({target_seconds}s).',
            'recommended_word_count': word_count,
            'recommended_wpm': effective_wpm,
        }
    elif ratio < 0.85:
        status = 'too_short'
        target_words = math.ceil((target_seconds / 60) * effective_wpm)
        words_to_add = max(1, target_words - word_count)
        chars_to_add_approx = words_to_add * 6  # ~6 chars per word in French
        advice = {
            'action': 'add_text',
            'words_to_add': words_to_add,
            'chars_to_add_approx': chars_to_add_approx,
            'recommended_word_count': target_words,
            'recommended_wpm': effective_wpm,
            'message': (
                f'Le script est trop court pour atteindre {target_seconds}s (estimé à {estimated}s, ratio {ratio}). '
                f'Ajoutez environ {words_to_add} mots (~{chars_to_add_approx} caractères) au scénario.'
            ),
        }
    else:
        status = 'too_long'
        target_words = max(1, math.floor((target_seconds / 60) * effective_wpm))
        words_to_remove = max(1, word_count - target_words)
        chars_to_remove_approx = words_to_remove * 6
        advice = {
            'action': 'remove_text',
            'words_to_remove': words_to_remove,
            'chars_to_remove_approx': chars_to_remove_approx,
            'recommended_word_count': target_words,
            'recommended_wpm': effective_wpm,
            'message': (
                f'Le script est trop long pour {target_seconds}s (estimé à {estimated}s, ratio {ratio}). '
                f'Retirez environ {words_to_remove} mots (~{chars_to_remove_approx} caractères).'
            ),
        }

    return {
        **est,
        'target_duration_seconds': target_seconds,
        'duration_difference_seconds': diff,
        'duration_ratio': ratio,
        'duration_status': status,
        'advice': advice,
    }


def post_generation_metrics(text: str, actual_duration: float, target_duration: float | None = None) -> dict:
    """Compute post-generation duration metrics for metadata inspection."""
    word_count = count_words(text)
    effective_wpm = round((word_count / actual_duration) * 60, 1) if actual_duration > 0 else 0.0

    result = {
        'spoken_word_count': word_count,
        'actual_duration_seconds': round(actual_duration, 2),
        'effective_words_per_minute': effective_wpm,
        'text_char_count': len(text.strip()) if text else 0,
        'text_coverage': 1.0,
    }

    if target_duration is not None and target_duration > 0:
        diff = round(actual_duration - target_duration, 2)
        ratio = round(actual_duration / target_duration, 2)
        if 0.85 <= ratio <= 1.15:
            d_status = 'on_target'
        elif actual_duration < target_duration:
            d_status = 'too_short'
        else:
            d_status = 'too_long'

        result.update({
            'target_duration_seconds': target_duration,
            'duration_difference_seconds': diff,
            'duration_ratio': ratio,
            'duration_status': d_status,
        })
    else:
        result['duration_status'] = 'unconstrained'

    return result

