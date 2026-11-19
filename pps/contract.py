"""PPS Universal TTS Contract V1.2 implementation.

Provides Pydantic models, validation helpers, capabilities descriptor,
and result serializers conforming strictly to TTS_UNIVERSAL_CONTRACT_CANDIDATE_V1_2.md.
"""
from __future__ import annotations

import math
import time
import unicodedata
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

from .alignment import canonical_tokens, valid_bounds, tokens
from .errors import PPSError


# ==============================================================================
# 1. REQUEST MODELS (TTSRequest)
# ==============================================================================

class SegmentItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    segmentId: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=50000)
    pauseAfterSeconds: float | None = Field(default=None, ge=0.0, le=10.0)
    targetDurationHintSeconds: float | None = Field(default=None, gt=0.0, le=3600.0)


class Requirements(BaseModel):
    model_config = ConfigDict(extra="ignore")
    alignmentMode: Literal["none", "words", "characters"] | None = "words"
    requireVerifiedAlignment: bool | None = False
    strictNativeCharacters: bool | None = False
    speechToSpeech: bool | None = False
    sourceAudioPath: str | None = None
    priority: Literal["normal", "interactive"] | None = "normal"


class AudioFormatOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")
    container: Literal["wav", "mp3"] | None = "wav"
    sampleRate: Literal[22050, 24000, 44100, 48000] | None = 24000
    channels: Literal[1, 2] | None = 1
    bitrateKbps: Literal[128, 192, 320] | None = 128


class ChatterboxOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")
    exaggeration: float | None = Field(default=0.5, ge=0.0, le=1.0)
    cfgWeight: float | None = Field(default=0.5, ge=0.0, le=1.0)
    temperature: float | None = Field(default=0.7, ge=0.1, le=1.5)


class CosyVoiceOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")
    speed: float | None = Field(default=1.0, ge=0.5, le=2.0)


class ProviderSpecificOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")
    chatterbox: ChatterboxOptions | None = None
    cosyvoice: CosyVoiceOptions | None = None


class VoiceOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")
    speed: float | None = Field(default=1.0, ge=0.5, le=2.0)
    seed: int | None = Field(default=1986, ge=0, le=2**32 - 1)
    providerSpecific: ProviderSpecificOptions | None = None


class ContextOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")
    previousText: str | None = Field(default=None, max_length=5000)
    nextText: str | None = Field(default=None, max_length=5000)
    emotionContext: str | None = Field(default=None, max_length=500)


class CachePolicy(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: Literal["use", "refresh", "bypass", "only"] | None = "use"


class ExecutionOptions(BaseModel):
    model_config = ConfigDict(extra="ignore")
    sync: bool | None = False
    timeoutMs: int | None = Field(default=None, ge=1000, le=600000)


class TTSRequest(BaseModel):
    """Universal TTS Request conforming to V1.2.0-candidate."""
    model_config = ConfigDict(extra="ignore")

    # Traceability
    requestId: str | None = None
    clientId: str | None = None
    formatId: str | None = None
    projectId: str | None = None
    metadata: dict[str, Any] | None = Field(default_factory=dict)

    # Linguistic content
    text: str = Field(min_length=1, max_length=50000)
    language: Literal["fr", "en"] = "fr"
    segments: list[SegmentItem] | None = None

    # Voice & engine selection
    voiceIdentityId: str | None = None
    voiceId: str | None = None
    voiceVersion: int | str | None = None
    referenceHash: str | None = None
    providerPreference: str | None = None
    engine: Literal["chatterbox", "cosyvoice", "auto"] | None = "auto"

    # Requirements
    requirements: Requirements | None = Field(default_factory=Requirements)

    # Audio format
    audioFormat: AudioFormatOptions | None = Field(default_factory=AudioFormatOptions)

    # Engine parameters
    voiceOptions: VoiceOptions | None = Field(default_factory=VoiceOptions)

    # Prosodic context
    context: ContextOptions | None = None

    # Cache policy & execution
    cachePolicy: CachePolicy | None = Field(default_factory=CachePolicy)
    execution: ExecutionOptions | None = Field(default_factory=ExecutionOptions)


# ==============================================================================
# 2. RESULT MODELS (TTSResult)
# ==============================================================================

class TimedWord(BaseModel):
    index: int
    text: str
    start: float
    end: float
    confidence: float | None = None
    uncertainty: list[str] | None = Field(default_factory=list)


class TimedCharacter(BaseModel):
    index: int
    char: str
    start: float
    end: float


class TimedCue(BaseModel):
    index: int
    text: str
    start: float
    end: float
    words: list[TimedWord]


class SuspectBoundary(BaseModel):
    wordIndex: int
    issue: Literal["gap_overlap", "excessive_duration", "rms_mismatch"]


class AlignmentReport(BaseModel):
    source: Literal["whisper_asr_dtw", "elevenlabs_native", "mock_deterministic", "none"]
    status: Literal["candidate_requires_review", "automatic_acoustic_verified", "skipped_quick_mode"]
    isAccepted: bool
    coverageRatio: float
    monotonic: bool
    zeroDurationWordsCount: int
    unmatchedScriptWords: list[str]
    suspectBoundaries: list[SuspectBoundary]
    warnings: list[str] = Field(default_factory=list)


class TimingsResult(BaseModel):
    granularity: Literal["none", "words", "characters"]
    words: list[TimedWord]
    characters: list[TimedCharacter] | None = None
    charactersSource: Literal["native_model", "synthetic_words_to_chars"] | None = None
    cues: list[TimedCue] | None = None
    alignmentReport: AlignmentReport


class AudioMetadataResult(BaseModel):
    filePath: str
    url: str | None = None
    container: Literal["wav", "mp3"]
    sampleRate: int
    channels: Literal[1, 2] = 1
    durationSeconds: float
    sha256: str
    byteSize: int


class ProviderResult(BaseModel):
    id: str = "pps"
    engine: str
    engineVersion: str
    isLocal: bool = True


class VoiceResult(BaseModel):
    logicalId: str | None = None
    voiceId: str
    alias: str | None = None
    name: str
    language: str
    version: int | str
    referenceId: str | None = None
    referenceHash: str | None = None


class SpeechMetricsResult(BaseModel):
    totalWords: int
    totalCharacters: int
    wordsPerMinute: float
    charactersPerSecond: float
    durationStatus: Literal["compatible", "too_short", "too_long", "unconstrained"] = "compatible"


class CacheResult(BaseModel):
    isHit: bool = False
    cacheKey: str = ""
    cacheStore: str = "pps_sqlite"


class PerformanceResult(BaseModel):
    totalDurationMs: float = 0.0
    queueDurationMs: float | None = 0.0
    loadDurationMs: float | None = 0.0
    synthesisDurationMs: float | None = 0.0
    alignmentDurationMs: float | None = 0.0
    realTimeFactor: float | None = 0.0
    estimatedCostUsd: float = 0.0


class TTSResult(BaseModel):
    """Universal TTS Result conforming to V1.2.0-candidate."""
    status: Literal["completed", "cached", "failed", "cancelled"]
    requestId: str | None = None
    jobId: str
    createdAt: float
    completedAt: float
    provider: ProviderResult
    voice: VoiceResult
    audio: AudioMetadataResult
    timings: TimingsResult
    speechMetrics: SpeechMetricsResult
    cache: CacheResult
    performance: PerformanceResult


# ==============================================================================
# 3. CAPABILITIES BUILDER
# ==============================================================================

def build_capabilities(cfg: dict[str, Any]) -> dict[str, Any]:
    """Generates the dynamic capabilities descriptor conforming to Contract V1.2 section 4."""
    return {
        "service": "Universal Phonation Service",
        "version": "1.2.0",
        "is_local": True,
        "cost_model": "free_local_compute",
        "hardware": {
            "gpu_name": "NVIDIA GeForce RTX 4060 Laptop GPU",
            "vram_total_gib": 8.0,
            "concurrency_limit": 1
        },
        "engines": [
            {
                "id": "chatterbox",
                "display_name": "Chatterbox Multilingual V3",
                "version": "v3",
                "languages": ["fr", "en"],
                "capabilities": {
                    "word_timestamps": True,
                    "character_timestamps": False,
                    "native_provider_timestamps": False,
                    "alignment_verification": True,
                    "long_form_synthesis": True,
                    "instant_voice_cloning": True,
                    "speech_to_speech": False,
                    "streaming_audio": False,
                    "native_speed_control": False,
                    "emotion_styling": True
                },
                "options": {
                    "exaggeration": [0.0, 1.0],
                    "cfg_weight": [0.0, 1.0],
                    "temperature": [0.1, 1.5],
                    "seed": "integer"
                },
                "limits": {
                    "max_characters_per_request": cfg.get("service", {}).get("max_text_chars", 50000),
                    "max_segment_characters": 450,
                    "max_queue_size": cfg.get("service", {}).get("max_queue", 32)
                },
                "audio_formats": {
                    "containers": ["wav", "mp3"],
                    "sample_rates": [24000, 44100],
                    "default_container": "wav",
                    "default_sample_rate": 24000
                }
            },
            {
                "id": "cosyvoice",
                "display_name": "FunAudioLLM CosyVoice 3",
                "version": "0.5B-2512",
                "languages": ["fr", "en"],
                "capabilities": {
                    "word_timestamps": True,
                    "character_timestamps": False,
                    "native_provider_timestamps": False,
                    "alignment_verification": True,
                    "long_form_synthesis": True,
                    "instant_voice_cloning": True,
                    "speech_to_speech": False,
                    "streaming_audio": False,
                    "native_speed_control": True,
                    "emotion_styling": False
                },
                "options": {
                    "speed": [0.5, 2.0]
                },
                "limits": {
                    "max_characters_per_request": cfg.get("service", {}).get("max_text_chars", 50000),
                    "max_segment_characters": 450,
                    "max_queue_size": cfg.get("service", {}).get("max_queue", 32)
                },
                "audio_formats": {
                    "containers": ["wav", "mp3"],
                    "sample_rates": [24000, 44100],
                    "default_container": "wav",
                    "default_sample_rate": 24000
                }
            }
        ]
    }


# ==============================================================================
# 4. VOICE RESOLUTION LOGIC
# ==============================================================================

def resolve_voice(store, voice_identifier: str | None, logical_identity: str | None, language: str = "fr", db=None):
    """Resolves physical voice profile from physical ID, alias, or logical identity.

    Guarantees:
    - Never changes voice physical IDs or audio references.
    - Preserves 'webtooonrecapfr' (FR) and 'webtoonrecap' (EN).
    - Rejects unknown voices with TTS_ERR_VOICE_NOT_FOUND (404).
    """
    voices = store.list("voices", db)
    target = voice_identifier or logical_identity

    if not target:
        raise PPSError("TTS_ERR_VOICE_NOT_FOUND", "Aucun identifiant de voix ou identité logique fourni.", 404)

    # 1. Exact match on voice id
    for v in voices:
        if v.get("id") == target:
            return v

    # 2. Exact match on voice name / alias
    for v in voices:
        if v.get("name", "").strip().lower() == target.strip().lower():
            return v

    # 3. Logical identity resolution for Webtoon recap
    normalized_target = target.strip().lower().replace("_", "-")
    if normalized_target in ("webtoon-recap", "webtoonrecap", "narrateur-webtoon"):
        if language == "en":
            for v in voices:
                if v.get("name") == "webtoonrecap":
                    return v
        else:  # fr or other
            for v in voices:
                if v.get("name") == "webtooonrecapfr":
                    return v

    raise PPSError("TTS_ERR_VOICE_NOT_FOUND", f"Voix '{target}' introuvable dans le catalogue PPS.", 404,
                   details={"requested_target": target, "language": language})


# ==============================================================================
# 5. CUE GENERATION & METRIC HELPERS
# ==============================================================================

def generate_cues_from_words(words: list[TimedWord], max_words_per_cue: int = 5, max_duration_sec: float = 3.0) -> list[TimedCue]:
    """Groups timed words into readable caption cues without dropping words."""
    if not words:
        return []
    cues = []
    cue_words: list[TimedWord] = []
    cue_idx = 0

    for w in words:
        cue_words.append(w)
        cur_duration = w.end - cue_words[0].start
        is_terminal = any(w.text.endswith(p) for p in (".", "!", "?", "…", ";", ":", ","))
        if len(cue_words) >= max_words_per_cue or cur_duration >= max_duration_sec or is_terminal:
            cues.append(TimedCue(
                index=cue_idx,
                text=" ".join(cw.text for cw in cue_words),
                start=round(cue_words[0].start, 3),
                end=round(cue_words[-1].end, 3),
                words=list(cue_words)
            ))
            cue_idx += 1
            cue_words = []

    if cue_words:
        cues.append(TimedCue(
            index=cue_idx,
            text=" ".join(cw.text for cw in cue_words),
            start=round(cue_words[0].start, 3),
            end=round(cue_words[-1].end, 3),
            words=list(cue_words)
        ))

    return cues


def build_alignment_report(alignment: dict[str, Any], duration_seconds: float, alignment_mode: str) -> AlignmentReport:
    """Constructs structured AlignmentReport conforming to Contract V1.2 section 7 & 19."""
    if alignment_mode in ("none", "quick") or alignment.get("status") == "skipped_quick_mode":
        return AlignmentReport(
            source="none",
            status="skipped_quick_mode",
            isAccepted=True,
            coverageRatio=1.0,
            monotonic=True,
            zeroDurationWordsCount=0,
            unmatchedScriptWords=[],
            suspectBoundaries=[],
            warnings=[]
        )

    words = alignment.get("words", [])
    zero_count = sum(1 for w in words if w.get("end", 0) <= w.get("start", 0))
    monotonic = valid_bounds(words, duration_seconds) if words else True

    unmatched = alignment.get("unmatched_script_words", [])
    suspect_boundaries: list[SuspectBoundary] = []

    for idx, w in enumerate(words):
        uncertainties = w.get("uncertainty", [])
        if "word_overlaps_low_energy_interval" in uncertainties:
            suspect_boundaries.append(SuspectBoundary(wordIndex=idx, issue="gap_overlap"))
        elif "ASR_DTW_boundary_disagreement" in uncertainties:
            suspect_boundaries.append(SuspectBoundary(wordIndex=idx, issue="excessive_duration"))
        elif "initial_boundary_precedes_sustained_energy" in uncertainties:
            suspect_boundaries.append(SuspectBoundary(wordIndex=idx, issue="rms_mismatch"))

    status_val = "automatic_acoustic_verified" if alignment.get("status") == "automatic_acoustic_verified" else "candidate_requires_review"

    return AlignmentReport(
        source="whisper_asr_dtw",
        status=status_val,
        isAccepted=bool(alignment.get("timestamps_valid", monotonic) and zero_count == 0),
        coverageRatio=round(float(alignment.get("script_coverage", 1.0)), 3),
        monotonic=monotonic,
        zeroDurationWordsCount=zero_count,
        unmatchedScriptWords=unmatched,
        suspectBoundaries=suspect_boundaries,
        warnings=[]
    )


# ==============================================================================
# 6. UNIVERSAL TTS RESULT SERIALIZER
# ==============================================================================

def format_universal_result(job: dict[str, Any], audio: dict[str, Any], voice: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Builds a full TTSResult dictionary from PPS job, audio, and voice records."""
    req = job.get("request", {})
    metrics = job.get("metrics", {})
    pcm = audio.get("pcm", {})
    duration = float(pcm.get("duration_seconds", 0.0))
    alignment = audio.get("alignment", {})

    raw_words = alignment.get("words", [])
    alignment_mode = req.get("alignment_mode", "full")
    is_quick = alignment_mode in ("none", "quick") or alignment.get("status") == "skipped_quick_mode"

    timed_words: list[TimedWord] = []
    if not is_quick and raw_words:
        for i, w in enumerate(raw_words):
            timed_words.append(TimedWord(
                index=i,
                text=w.get("text") or w.get("word") or "",
                start=round(float(w.get("start", 0.0)), 3),
                end=round(float(w.get("end", 0.0)), 3),
                confidence=round(float(w.get("confidence") or w.get("probability") or 0.95), 2),
                uncertainty=list(w.get("uncertainty", []))
            ))

    cues = generate_cues_from_words(timed_words) if timed_words else []
    report = build_alignment_report(alignment, duration, "none" if is_quick else "words")

    script_text = req.get("text", "")
    word_tokens = tokens(script_text)
    total_words = len(word_tokens)
    total_chars = len(script_text)

    wpm = round((total_words / (duration / 60.0)), 1) if duration > 0 else 0.0
    cps = round((total_chars / duration), 1) if duration > 0 else 0.0

    wav_path = str(Path(audio.get("wav", "")).resolve())
    status_str = "cached" if metrics.get("synthesis_cache_hit") else "completed"

    result_payload = {
        "status": status_str,
        "requestId": job.get("request_id") or req.get("requestId"),
        "jobId": job["id"],
        "createdAt": job.get("created_at", time.time()),
        "completedAt": job.get("finished_at", time.time()),
        "provider": {
            "id": "pps",
            "engine": req.get("engine", "chatterbox"),
            "engineVersion": "v3" if req.get("engine") == "chatterbox" else "0.5B-2512",
            "isLocal": True
        },
        "voice": {
            "logicalId": job.get("universal_request", {}).get("voiceIdentityId") or "webtoon-recap",
            "voiceId": voice.get("id", audio.get("voice_id", "")),
            "alias": voice.get("name", audio.get("voice_id", "")),
            "name": voice.get("name", audio.get("voice_id", "")),
            "language": req.get("language", "fr"),
            "version": voice.get("version", audio.get("voice_version", 1)),
            "referenceId": audio.get("reference_id"),
            "referenceHash": audio.get("reference_hash")
        },
        "audio": {
            "filePath": wav_path,
            "url": f"/v1/tts/audio/{audio['id']}?format=wav",
            "container": "wav",
            "sampleRate": int(pcm.get("sample_rate", 24000)),
            "channels": 1,
            "durationSeconds": round(duration, 3),
            "sha256": pcm.get("sha256", ""),
            "byteSize": int(pcm.get("size_bytes", 0)) or (Path(wav_path).stat().st_size if Path(wav_path).is_file() else 0)
        },
        "timings": {
            "granularity": "none" if is_quick else "words",
            "words": [w.model_dump() for w in timed_words],
            "cues": [c.model_dump() for c in cues] if cues else None,
            "alignmentReport": report.model_dump()
        },
        "speechMetrics": {
            "totalWords": total_words,
            "totalCharacters": total_chars,
            "wordsPerMinute": wpm,
            "charactersPerSecond": cps,
            "durationStatus": "compatible"
        },
        "cache": {
            "isHit": bool(metrics.get("synthesis_cache_hit", False)),
            "cacheKey": str(metrics.get("cache_key", "")),
            "cacheStore": "pps_sqlite"
        },
        "performance": {
            "totalDurationMs": round(float(metrics.get("total_api_seconds", 0.0)) * 1000, 1),
            "queueDurationMs": round(float(metrics.get("queue_seconds", 0.0)) * 1000, 1),
            "loadDurationMs": round(float(metrics.get("load_seconds", 0.0)) * 1000, 1),
            "synthesisDurationMs": round(float(metrics.get("synthesis_seconds", 0.0)) * 1000, 1),
            "alignmentDurationMs": round(float(metrics.get("alignment_seconds", 0.0)) * 1000, 1),
            "realTimeFactor": round(float(metrics.get("real_time_factor", 0.0)), 2),
            "estimatedCostUsd": 0.0
        }
    }
    return result_payload

