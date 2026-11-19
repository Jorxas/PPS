import hashlib
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from fastapi import FastAPI, File, Form, Header, UploadFile, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import load_config
from .engines import status
from .errors import PPSError
from .jobs import JobService, TERMINAL, public_job
from .resources import snapshot
from .storage import Store
from .voices import register, public_voice
from .review import effective_alignment, latest_review, save_review
from .compatibility import create_plan, render_plan, result_for_audio
from .media import MediaService, options as media_options, public_analysis
from .contract import TTSRequest


class Synthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice_id: str
    reference_id: str | None = None
    voice_version: int | None = Field(default=None,ge=1)
    cache_mode: Literal['use','refresh','bypass','only'] = 'use'
    previous_text: str | None = Field(default=None,max_length=5000)
    next_text: str | None = Field(default=None,max_length=5000)
    text: str = Field(min_length=1, max_length=50000)
    display_text: str | None = Field(default=None, max_length=50000)
    language: Literal["fr", "en", "de"] = "fr"
    engine: Literal["chatterbox", "cosyvoice"]
    options: dict[str, float] = Field(default_factory=dict)
    seed: int = Field(default=1986, ge=0, le=2**32-1)
    target_duration_seconds: float | None = Field(default=None, gt=0, le=3600)
    alignment_mode: Literal['full', 'quick'] = 'full'


class Comparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice_id: str
    reference_id: str | None = None
    engine_reference_ids: dict[str, str] = Field(default_factory=dict)
    text: str = Field(min_length=1, max_length=50000)
    display_text: str | None = Field(default=None, max_length=50000)
    language: Literal["fr", "en"] = "fr"
    engine_options: dict[str, dict[str, float]] = Field(default_factory=dict)
    seed: int = Field(default=1986, ge=0, le=2**32-1)
    target_duration_seconds: float | None = Field(default=None, gt=0, le=3600)
    alignment_mode: Literal['full', 'quick'] = 'full'


class DurationEstimate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=50000)
    language: Literal['fr', 'en', 'de'] = 'fr'
    engine: Literal['chatterbox', 'cosyvoice'] | None = None
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    target_duration_seconds: float | None = Field(default=None, gt=0, le=3600)


class MediaUpload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filename: str = Field(min_length=1,max_length=500)
    size_bytes: int = Field(gt=0,strict=True)
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    voice_id: str | None = None
    name: str = Field(default='',max_length=120)
    language: Literal['fr','en'] = 'fr'
    authorized: bool = False
    declaration: str = Field(default='',max_length=2000)


class ReferenceTrials(BaseModel):
    model_config = ConfigDict(extra='forbid')
    voice_id: str
    reference_ids: list[str] = Field(min_length=2,max_length=8)
    engine: Literal['both','chatterbox','cosyvoice'] = 'both'
    text: str = Field(min_length=1,max_length=50000)
    language: Literal['fr','en'] = 'fr'
    engine_options: dict[str,dict[str,float]] = Field(default_factory=dict)
    seed: int = Field(default=1986,ge=0,le=2**32-1)
    alignment_mode: Literal['full', 'quick'] = 'full'



class MediaTrack(BaseModel):
    model_config = ConfigDict(extra='forbid')
    track_index: int = Field(ge=0,strict=True)
    fresh: bool = False


class MediaRange(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)


class VoicePreparation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    voice_id: str
    media_ids: list[str] = Field(min_length=1)
    track_indices: dict[str, Annotated[int, Field(ge=0, strict=True)]] = Field(default_factory=dict)


class PreparationCandidate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    analysis_id: str
    candidate_id: str


class MediaReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    speaker_confirmed: bool = False
    transcription: str = Field(default='',max_length=5000)
    transcription_verified: bool = False
    language: Literal['fr','en'] = 'fr'


class ReferenceChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    engine: Literal['chatterbox','cosyvoice']
    reference_id: str


class ReferenceTranscript(BaseModel):
    model_config = ConfigDict(extra='forbid')
    transcription: str = Field(min_length=1,max_length=5000)
    verified: bool


class Correction(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    word_index: int = Field(ge=0,strict=True)
    start: float = Field(ge=0,strict=True)
    end: float = Field(gt=0,strict=True)


class Ratings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    fidelity: int | None = Field(default=None,ge=1,le=5,strict=True)
    naturalness: int | None = Field(default=None,ge=1,le=5,strict=True)
    pronunciation: int | None = Field(default=None,ge=1,le=5,strict=True)
    expressiveness: int | None = Field(default=None,ge=1,le=5,strict=True)
    artifacts: int | None = Field(default=None,ge=1,le=5,strict=True)
    audio_quality: int | None = Field(default=None,ge=1,le=5,strict=True)


class Review(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audio_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    alignment_revision: int = Field(ge=1)
    ratings: Ratings = Field(default_factory=Ratings)
    corrections: list[Correction] = Field(default_factory=list,max_length=1500)
    acoustically_confirmed: bool = False
    notes: str = Field(default='',max_length=5000)
    author: str = Field(default='local_user',max_length=100)


class Segment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    voice_id: str
    reference_id: str | None = None
    text: str = Field(min_length=1,max_length=50000)
    previous_text: str | None = Field(default=None,max_length=5000)
    next_text: str | None = Field(default=None,max_length=5000)



class MainClipPlan(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    mode: Literal['simple','segmented','dialogue'] = 'simple'
    engine: Literal['chatterbox','cosyvoice']
    language: Literal['fr','en'] = 'fr'
    segments: list[Segment] = Field(min_length=1,max_length=32)
    options: dict[str,float] = Field(default_factory=dict)
    seed: int = Field(default=1986,ge=0,le=2**32-1)
    gap_seconds: float = Field(default=.06,ge=0,le=10)
    rate_multiplier: float = Field(default=1,ge=.85,le=1.18)
    require_alignment: bool = True
    cache_mode: Literal['use','refresh','bypass','only'] = 'use'


class BodyLimit:
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = self.limit + 1
        if length > self.limit:
            return await JSONResponse({"error": {"code": "UPLOAD_TOO_LARGE", "message": "Requête trop volumineuse.", "details": {}, "retryable": False}}, 413)(scope, receive, send)
        used = 0

        async def bounded():
            nonlocal used
            message = await receive()
            used += len(message.get("body", b""))
            if used > self.limit:
                raise PPSError("UPLOAD_TOO_LARGE", "Requête trop volumineuse.", 413)
            return message
        await self.app(scope, bounded, send)


def create_app(cfg=None, manager=None, start_jobs=True):
    cfg = cfg or load_config()
    tmp = Path(cfg["root"]) / "runtime" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(tmp)
    store = Store(Path(cfg["paths"]["data"]) / "pps.sqlite3")
    jobs = JobService(store, cfg, manager)
    media = MediaService(store,cfg)

    @asynccontextmanager
    async def lifespan(app):
        if start_jobs:
            jobs.start()
            media.start()
        yield
        if start_jobs:
            media.close()
            jobs.close()

    app = FastAPI(title="PPS — Synthèse vocale locale", version="0.3.0", lifespan=lifespan, docs_url=None, redoc_url=None,
        description="API autonome. Moteurs isolés, autorisation explicite, une seule génération GPU. Alignement déclaré non validé tant que le forced alignment n'est pas vérifié.")
    app.state.store, app.state.jobs, app.state.cfg = store, jobs, cfg
    app.state.media = media
    # Local assets: docs remain available without a CDN or internet connection.
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    @app.get("/docs", include_in_schema=False)
    def docs():
        return get_swagger_ui_html(openapi_url="/openapi.json", title="PPS — API locale",
            swagger_js_url="/static/swagger-ui-bundle.js", swagger_css_url="/static/swagger-ui.css",
            swagger_favicon_url="data:,", swagger_ui_parameters={"validatorUrl": None})
    app.add_middleware(BodyLimit, limit=cfg["service"]["max_reference_bytes"] + 65536)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        expected = {"http://127.0.0.1:" + str(cfg["service"]["port"]), "http://localhost:" + str(cfg["service"]["port"])}
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin not in expected:
            return JSONResponse({"error": PPSError("ORIGIN_DENIED", "Origine web non autorisée.", 403).payload()}, 403)
        return await call_next(request)

    @app.exception_handler(PPSError)
    async def pps_error(request, exc):
        return JSONResponse({"error": exc.payload()}, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"error": {"code": "VALIDATION_ERROR", "message": "Requête non valide.",
            "details": {"fields": [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]}, "retryable": False}}, 422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": PPSError("HTTP_ERROR", str(exc.detail), exc.status_code).payload()}, exc.status_code)

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        import logging
        logging.getLogger("pps").error("Unhandled API failure", exc_info=exc)
        return JSONResponse({"error": PPSError("INTERNAL_ERROR", "Erreur interne ; consulter les journaux PPS.", 500).payload()}, 500)

    @app.get("/", include_in_schema=False)
    def web():
        return FileResponse(Path(cfg["root"]) / "pps" / "web.html")

    @app.get("/v1/health")
    def health():
        return {"status": "ok", "service": "PPS", "inference_concurrency": 1,
                "active_engine": jobs.manager.engine, "resources": snapshot(cfg["paths"]["data"]),
                "alignment_mainclip_compatible": False}

    @app.get("/v1/engines")
    def engines():
        return {"engines": [status(e, cfg) for e in ("chatterbox", "cosyvoice")], "resource_thresholds": cfg["resources"]}

    @app.get("/v1/capabilities")
    def capabilities_endpoint():
        from .contract import build_capabilities
        return build_capabilities(cfg)

    @app.post("/v1/duration-estimate")
    def duration_estimate(body: DurationEstimate):
        from .duration import estimate_duration, check_target_duration
        if body.target_duration_seconds:
            return check_target_duration(body.text, body.target_duration_seconds, body.language, body.engine, body.speed)
        return estimate_duration(body.text, body.language, body.engine, body.speed)


    @app.get('/v1/media/options')
    def media_limits():
        return media_options(cfg)

    @app.post('/v1/media/uploads',status_code=201)
    def media_begin(body: MediaUpload):
        return media.begin(body.model_dump())

    @app.post('/v1/voice-preparations', status_code=202)
    def voice_prepare(body: VoicePreparation):
        return media.preparation_progress(media.prepare_voice(body.voice_id, body.media_ids, body.track_indices))

    @app.get('/v1/voice-preparations')
    def voice_preparations():
        return {'preparations': [media.preparation_progress(p) for p in store.list('voice_preparations')]}

    @app.get('/v1/voice-preparations/{identifier}')
    def voice_preparation(identifier: str):
        return media.preparation_progress(store.get('voice_preparations', identifier))

    @app.put('/v1/voice-preparations/{identifier}/candidate')
    def preparation_candidate(identifier: str, body: PreparationCandidate):
        return media.preparation_progress(media.select_preparation_candidate(identifier, body.analysis_id, body.candidate_id))

    @app.post('/v1/voice-preparations/{identifier}/confirm', status_code=201)
    def preparation_confirm(identifier: str, body: MediaReference):
        return public_voice(media.confirm_preparation(identifier, body.model_dump()))

    @app.post('/v1/voice-preparations/{identifier}/cancel')
    def preparation_cancel(identifier: str):
        return media.preparation_progress(media.cancel_preparation(identifier))

    @app.post('/v1/voice-preparations/{identifier}/resume')
    def preparation_resume(identifier: str):
        return media.preparation_progress(media.resume_preparation(identifier))

    @app.get('/v1/media')
    def media_list():
        return {'media':[media.get_media(m['id']) for m in store.list('media')],
            'analyses':[public_analysis(a) for a in store.list('media_analyses')]}

    @app.get('/v1/media/uploads/{media_id}')
    def media_upload_status(media_id: str):
        return media.get_media(media_id)

    @app.put('/v1/media/uploads/{media_id}')
    async def media_append(media_id: str,request: Request,offset: int):
        if offset<0: raise PPSError('UPLOAD_OFFSET_INVALID','Offset négatif.',422)
        return await media.append(media_id,offset,request)

    @app.post('/v1/media/uploads/{media_id}/complete')
    def media_complete(media_id: str):
        return media.complete(media_id)

    @app.post('/v1/media/{media_id}/analyses',status_code=202)
    def media_analyze(media_id: str,body: MediaTrack):
        return media.analyze(media_id,body.track_index,body.fresh)

    @app.get('/v1/media/analyses/{analysis_id}')
    def media_analysis(analysis_id: str):
        return public_analysis(store.get('media_analyses',analysis_id))

    @app.post('/v1/media/analyses/{analysis_id}/cancel')
    def media_cancel(analysis_id: str):
        return media.cancel(analysis_id)

    @app.post('/v1/media/analyses/{analysis_id}/resume')
    def media_resume(analysis_id: str):
        return media.resume(analysis_id)

    @app.post('/v1/media/analyses/{analysis_id}/candidates',status_code=201)
    def media_manual(analysis_id: str,body: MediaRange):
        return media.manual(analysis_id,body.start_seconds,body.end_seconds)

    @app.get('/v1/media/analyses/{analysis_id}/candidates/{candidate_id}/audio')
    def media_clip(analysis_id: str,candidate_id: str):
        return FileResponse(media.clip(analysis_id,candidate_id),media_type='audio/wav')

    @app.post('/v1/media/analyses/{analysis_id}/candidates/{candidate_id}/transcribe')
    def media_transcribe(analysis_id: str,candidate_id: str):
        return media.transcribe(analysis_id,candidate_id)

    @app.post('/v1/media/analyses/{analysis_id}/candidates/{candidate_id}/reference',status_code=201)
    def media_save(analysis_id: str,candidate_id: str,body: MediaReference):
        return public_voice(media.save(analysis_id,candidate_id,body.model_dump()))

    @app.put('/v1/voices/{voice_id}/engine-reference')
    def reference_choice(voice_id: str,body: ReferenceChoice):
        return public_voice(media.choose(voice_id,body.engine,body.reference_id))

    @app.post('/v1/reference-trials',status_code=202)
    def reference_trials(body: ReferenceTrials,idempotency_key: Annotated[str|None,Header()]=None):
        if set(body.engine_options)-{'chatterbox','cosyvoice'}:
            raise PPSError('ENGINE_UNKNOWN','Options pour moteur inconnu.',422)
        return jobs.submit_trials(body.model_dump(),idempotency_key)

    @app.get('/v1/reference-trials')
    def reference_trials_history():
        return {'trials':store.list('reference_trials')}

    @app.get('/v1/reference-trials/{trial_id}')
    def reference_trial(trial_id: str):
        trial=store.get('reference_trials',trial_id)
        children=[public_job(store.get('jobs',j)) for j in trial['job_ids']]
        return {**trial,'jobs':children,'finished':all(j['status'] in TERMINAL for j in children)}

    @app.post('/v1/reference-trials/{trial_id}/cancel')
    def reference_trial_cancel(trial_id: str):
        trial=store.get('reference_trials',trial_id)
        return {'jobs':[jobs.cancel(j) for j in trial['job_ids']]}

    @app.put('/v1/voices/{voice_id}/references/{reference_id}/transcription')
    def reference_transcript(voice_id: str,reference_id: str,body: ReferenceTranscript):
        if not body.transcription.strip(): raise PPSError('TRANSCRIPTION_REQUIRED','Transcription vide.',422)
        with store.transaction() as db:
            v=store.get('voices',voice_id,db)
            ref=next((r for r in v['references'] if r['id']==reference_id),None)
            if not ref: raise PPSError('REFERENCE_UNKNOWN','Référence inconnue.',404)
            ref.update(transcription=body.transcription.strip(),transcription_verified=body.verified,
                transcription_verification={'verified':body.verified,'recorded_at':__import__('time').time(),'method':'user'})
            v['version']+=1; store.put('voices',v,db)
        return public_voice(v)

    def import_voice(file, name, language, transcription, authorized, declaration, key=None, voice_id=None):
        if key and (len(key) > 200 or not key.strip()):
            raise PPSError("IDEMPOTENCY_KEY_INVALID", "Clé d'idempotence non valide.", 422)
        # Hash a bounded upload before persisting; rewinding avoids retaining full files in RAM.
        h, size = hashlib.sha256(), 0
        for block in iter(lambda: file.file.read(1024*1024), b""):
            size += len(block)
            if size > cfg["service"]["max_reference_bytes"]:
                raise PPSError("UPLOAD_TOO_LARGE", "Référence trop volumineuse.", 413)
            h.update(block)
        file.file.seek(0)
        digest = hashlib.sha256(json.dumps([h.hexdigest(), name, language, transcription, authorized, declaration, voice_id], ensure_ascii=False).encode()).hexdigest()
        scope = "voices:" + (voice_id or "new")
        with store.lock:
            with store.transaction() as db:
                prior = store.remembered(scope, key, digest, db)
            if prior:
                return public_voice(store.get("voices", prior["voice_id"]))
            voice = register(store, cfg, file, name, language, transcription, authorized, declaration, voice_id)
            with store.transaction() as db:
                store.remember(scope, key, digest, {"voice_id": voice["id"]}, db)
            return public_voice(voice)

    @app.post("/v1/voices", status_code=201)
    def add_voice(file: Annotated[UploadFile, File()], name: Annotated[str, Form()],
                  language: Annotated[str, Form()] = "fr", transcription: Annotated[str, Form()] = "",
                  authorized: Annotated[bool, Form()] = False, declaration: Annotated[str, Form()] = "",
                  idempotency_key: Annotated[str | None, Header()] = None):
        return import_voice(file, name, language, transcription, authorized, declaration, idempotency_key)

    @app.post("/v1/voices/{voice_id}/references", status_code=201)
    def add_reference(voice_id: str, file: Annotated[UploadFile, File()],
                      transcription: Annotated[str, Form()] = "", authorized: Annotated[bool, Form()] = False,
                      declaration: Annotated[str, Form()] = "", language: Annotated[str, Form()] = "fr",
                      idempotency_key: Annotated[str | None, Header()] = None):
        voice = store.get("voices", voice_id)
        return import_voice(file, voice["name"], language, transcription, authorized, declaration, idempotency_key, voice_id)

    @app.get("/v1/voices")
    def voices():
        return {"voices": [public_voice(v) for v in store.list("voices")]}

    @app.get("/v1/voices/{voice_id}")
    def voice(voice_id: str):
        try:
            return public_voice(store.get("voices", voice_id))
        except PPSError:
            target = voice_id.strip().lower()
            v = next((x for x in store.list("voices") if x.get("name", "").strip().lower() == target), None)
            if v:
                return public_voice(v)
            raise

    @app.get('/v1/voices/{voice_id}/references/{reference_id}/audio')
    def reference_audio(voice_id: str,reference_id: str):
        voice = store.get('voices',voice_id)
        reference = next((r for r in voice['references'] if r['id']==reference_id),None)
        if reference is None:
            raise PPSError('REFERENCE_UNKNOWN','Référence introuvable.',404)
        if not reference['authorization']['granted']:
            raise PPSError('AUTHORIZATION_REQUIRED','Référence non autorisée.',403)
        return FileResponse(reference['original'],media_type='audio/wav' if reference['filename'].lower().endswith('.wav') else 'application/octet-stream')

    @app.get('/v1/voices/{voice_id}/references/{reference_id}/alignment')
    def reference_alignment(voice_id: str,reference_id: str):
        voice=store.get('voices',voice_id)
        ref=next((r for r in voice['references'] if r['id']==reference_id),None)
        if ref is None:
            raise PPSError('REFERENCE_UNKNOWN','Référence introuvable.',404)
        return ref.get('alignment',{'words':[],'status':'not_available'})

    @app.post("/v1/syntheses", status_code=202)
    def synthesize(body: Synthesis, idempotency_key: Annotated[str | None, Header()] = None):
        return jobs.submit(body.model_dump(), idempotency_key)

    @app.get("/v1/jobs/{job_id}")
    def job(job_id: str):
        return public_job(store.get("jobs", job_id))

    @app.get("/v1/jobs")
    def job_history():
        return {"jobs": [public_job(j) for j in store.list("jobs")]}

    @app.post("/v1/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        return jobs.cancel(job_id)

    @app.get("/v1/audio/{audio_id}")
    def audio(audio_id: str, format: Literal["wav", "mp3"] = "wav", download: bool = False):
        result = store.get("audio", audio_id)
        path = Path(result[format])
        if not path.is_file():
            raise PPSError("AUDIO_MISSING", "Fichier audio absent du stockage.", 404)
        return FileResponse(path, media_type="audio/wav" if format == "wav" else "audio/mpeg",
                            filename=audio_id + "." + format if download else None)

    @app.get("/v1/audio/{audio_id}/metadata")
    def metadata(audio_id: str):
        value = store.get("audio", audio_id)
        return {k: v for k, v in value.items() if k not in ("wav", "mp3", "alignment")}

    @app.get("/v1/audio/{audio_id}/alignment")
    def alignment(audio_id: str):
        return store.get("audio", audio_id)["alignment"]

    @app.get('/v1/audio/{audio_id}/review')
    def review(audio_id: str):
        audio = store.get('audio',audio_id)
        latest = latest_review(store,audio_id)
        return {'latest':latest,'history':[r for r in store.list('reviews') if r['audio_id']==audio_id],
                'alignment_revision':audio.get('alignment_revision',1),
                'effective_alignment':effective_alignment(audio,latest)}

    @app.post('/v1/audio/{audio_id}/review',status_code=201)
    def add_review(audio_id: str,body: Review):
        return save_review(store,audio_id,body.model_dump())

    @app.get('/v1/audio/{audio_id}/mainclip')
    def mainclip_result(audio_id: str,require_alignment: bool=True,characters: bool=False):
        return result_for_audio(store,audio_id,require_alignment,characters)

    # === UNIVERSAL TTS CONTRACT V1.2 ENDPOINTS ===
    @app.post("/v1/tts/synthesize", status_code=202)
    def tts_synthesize(body: TTSRequest, idempotency_key: Annotated[str | None, Header()] = None):
        return jobs.submit_universal(body, idempotency_key)

    @app.get("/v1/tts/jobs/{job_id}")
    def tts_job(job_id: str):
        from .contract import format_universal_result
        job_data = store.get("jobs", job_id)
        status_val = job_data["status"]
        if status_val == "completed":
            audio_data = store.get("audio", job_data["audio_id"])
            voice_data = store.get("voices", audio_data["voice_id"])
            return format_universal_result(job_data, audio_data, voice_data, cfg)
        elif status_val == "cancelled":
            return {
                "jobId": job_data["id"],
                "status": "cancelled",
                "createdAt": job_data.get("created_at"),
                "cancelledAt": job_data.get("finished_at")
            }
        elif status_val == "failed":
            err = job_data.get("error") or {}
            return {
                "jobId": job_data["id"],
                "status": "failed",
                "createdAt": job_data.get("created_at"),
                "failedAt": job_data.get("finished_at"),
                "error": {
                    "code": err.get("code", "TTS_ERR_INTERNAL"),
                    "message": err.get("message", "Échec de la synthèse vocale."),
                    "details": err.get("details", {}),
                    "retryable": err.get("retryable", False)
                }
            }
        else:
            progress_pct = 0
            if status_val == "loading":
                progress_pct = 15
            elif status_val == "synthesizing":
                progress_pct = 50
            elif status_val == "exporting":
                progress_pct = 80
            elif status_val == "aligning":
                progress_pct = 90
            return {
                "jobId": job_data["id"],
                "status": "queued" if status_val == "queued" else "running",
                "phase": status_val,
                "progress": progress_pct,
                "createdAt": job_data.get("created_at"),
                "cancelRequested": bool(job_data.get("cancel_requested", False)),
                "provider": {
                    "id": "pps",
                    "engine": job_data.get("request", {}).get("engine", "chatterbox"),
                    "isLocal": True
                }
            }

    @app.post("/v1/tts/jobs/{job_id}/cancel")
    def tts_cancel(job_id: str):
        cancelled_job = jobs.cancel(job_id)
        is_cancelled = cancelled_job["status"] == "cancelled"
        return {
            "jobId": job_id,
            "status": "cancelled" if is_cancelled else "cancelling"
        }

    @app.get("/v1/tts/audio/{audio_id}")
    def tts_audio_download(audio_id: str, format: Literal["wav", "mp3"] = "wav", download: bool = False):
        res = store.get("audio", audio_id)
        audio_path = Path(res[format])
        if not audio_path.is_file():
            raise PPSError("TTS_ERR_INTERNAL", "Fichier audio absent du stockage physique.", 404)
        return FileResponse(
            audio_path,
            media_type="audio/wav" if format == "wav" else "audio/mpeg",
            filename=f"{audio_id}.{format}" if download else None
        )

    @app.get("/v1/tts/audio/{audio_id}/timestamps")
    def tts_audio_timestamps(audio_id: str, granularity: Literal["none", "words", "characters"] = "words"):
        from .contract import generate_cues_from_words, build_alignment_report, TimedWord
        res = store.get("audio", audio_id)
        alignment = res.get("alignment", {})
        raw_words = alignment.get("words", [])
        duration = float(res.get("pcm", {}).get("duration_seconds", 0.0))

        timed_words = [
            TimedWord(
                index=i,
                text=w.get("text") or w.get("word") or "",
                start=round(float(w.get("start", 0.0)), 3),
                end=round(float(w.get("end", 0.0)), 3),
                confidence=round(float(w.get("confidence") or w.get("probability") or 0.95), 2),
                uncertainty=list(w.get("uncertainty", []))
            )
            for i, w in enumerate(raw_words)
        ]
        cues = generate_cues_from_words(timed_words) if timed_words else []
        report = build_alignment_report(alignment, duration, granularity)

        return {
            "audioId": audio_id,
            "granularity": granularity,
            "words": [w.model_dump() for w in timed_words],
            "cues": [c.model_dump() for c in cues] if cues else None,
            "alignmentReport": report.model_dump()
        }

    @app.get("/v1/tts/audio/{audio_id}/metadata")
    def tts_audio_metadata(audio_id: str):
        from .contract import tokens
        res = store.get("audio", audio_id)
        pcm = res.get("pcm", {})
        duration = float(pcm.get("duration_seconds", 0.0))
        text = res.get("text", "")
        total_words = len(tokens(text))
        total_chars = len(text)
        wpm = round(total_words / (duration / 60.0), 1) if duration > 0 else 0.0
        cps = round(total_chars / duration, 1) if duration > 0 else 0.0
        return {
            "audioId": audio_id,
            "engine": res.get("engine"),
            "voiceId": res.get("voice_id"),
            "language": res.get("language", "fr"),
            "pcm": pcm,
            "speechMetrics": {
                "totalWords": total_words,
                "totalCharacters": total_chars,
                "wordsPerMinute": wpm,
                "charactersPerSecond": cps,
                "durationStatus": "compatible"
            },
            "metrics": res.get("metrics", {})
        }

    @app.post('/v1/mainclip/plans',status_code=202)
    def mainclip_plan(body: MainClipPlan,idempotency_key: Annotated[str|None,Header()]=None):
        return create_plan(store,jobs,body.model_dump(),idempotency_key)

    @app.get('/v1/mainclip/plans/{plan_id}')
    def get_plan(plan_id: str):
        plan=store.get('mainclip_plans',plan_id)
        return {**plan,'jobs':[public_job(store.get('jobs',p['job_id'])) for p in plan['pieces'] if 'job_id' in p]}

    @app.post('/v1/mainclip/plans/{plan_id}/render')
    def render(plan_id: str):
        return render_plan(store,cfg,plan_id)

    @app.post('/v1/mainclip/plans/{plan_id}/cancel')
    def cancel_plan(plan_id: str):
        with store.lock:
            plan = store.get('mainclip_plans',plan_id)
            for p in plan['pieces']:
                if 'job_id' in p:
                    jobs.cancel(p['job_id'])
            return store.update('mainclip_plans',plan_id,status='cancelled')

    @app.post("/v1/comparisons", status_code=202)
    def compare(body: Comparison, idempotency_key: Annotated[str | None, Header()] = None):
        unknown = (set(body.engine_options) | set(body.engine_reference_ids)) - {"chatterbox", "cosyvoice"}
        if unknown:
            raise PPSError("ENGINE_UNKNOWN", "Options pour moteur inconnu.", 422)
        return jobs.submit(body.model_dump(), idempotency_key, comparison=True)

    @app.get("/v1/comparisons")
    def comparisons():
        return {"comparisons": [comparison(c["id"]) for c in store.list("comparisons")]}

    @app.get("/v1/comparisons/{comparison_id}")
    def comparison(comparison_id: str):
        value = store.get("comparisons", comparison_id)
        children = [public_job(store.get("jobs", j)) for j in value["job_ids"]]
        states = [j["status"] for j in children]
        state = "completed" if all(s == "completed" for s in states) else "partial" if all(s in TERMINAL for s in states) and "completed" in states else "failed" if all(s in TERMINAL for s in states) else "running"
        return {**value, "status": state, "jobs": children}

    @app.get("/v1/metrics")
    def metrics():
        all_jobs = store.list("jobs")
        return {"jobs_total": len(all_jobs), "states": {state: sum(j["status"] == state for j in all_jobs) for state in ("queued", "loading", "synthesizing", "exporting", "aligning", *sorted(TERMINAL))},
                "active_engine": jobs.manager.engine, "resources": snapshot(cfg["paths"]["data"]),
                "benchmarks": [{"job_id": j["id"], "engine": j["request"]["engine"], "status": j["status"], "metrics": j["metrics"], "error": j["error"]} for j in all_jobs]}

    return app
