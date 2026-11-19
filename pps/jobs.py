import hashlib
import json
import queue
import re
import threading
import time
import traceback
import psutil
from pathlib import Path
from .audio import export, concatenate_pcm_files
from .aligner import ResidentAligner
from .alignment import align, align_long_form
from .engines import ENGINE_NAMES, capabilities
from .errors import PPSError
from .manager import EngineManager
from .resources import ProcessLock, loaded_ram_floor
from .storage import identity
from .cache import cache_key, lookup, effective_options
from .duration import post_generation_metrics

TERMINAL = {"completed", "failed", "cancelled"}


def segment_text(text: str, max_chars: int = 1200) -> list[dict]:
    """Split text into sentence/clause-preserving segments <= max_chars.

    Guarantees:
    - Never drops a single character or word.
    - Preserves sentence context and punctuation boundaries (. ! ? ; : \\n).
    - Returns list of {'index': i, 'text': segment_text, 'char_length': len}.
    """
    cleaned = text.strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [{"index": 0, "text": cleaned, "char_length": len(cleaned)}]

    # First split by paragraphs
    paragraphs = re.split(r"(\n+)", cleaned)
    units = []
    for p in paragraphs:
        if not p:
            continue
        # Split paragraph by sentence terminators while keeping delimiters
        sentences = re.split(r"([.!?…]+(?:\s+|$))", p)
        cur = ""
        for part in sentences:
            if not part:
                continue
            cur += part
            if re.search(r"[.!?…]+(?:\s+|$)$", cur):
                units.append(cur)
                cur = ""
        if cur:
            units.append(cur)

    # Now group units into segments under max_chars
    segments = []
    current_segment = ""

    for u in units:
        if len(u) > max_chars:
            # Unit itself is longer than max_chars: split by commas or spaces
            sub_parts = re.split(r"([,;:]\s+|\s+)", u)
            sub_cur = ""
            for sp in sub_parts:
                if not sp:
                    continue
                if len(current_segment) + len(sub_cur) + len(sp) <= max_chars:
                    sub_cur += sp
                else:
                    if current_segment or sub_cur:
                        segments.append((current_segment + sub_cur).strip())
                        current_segment = ""
                        sub_cur = sp
                    else:
                        segments.append(sp.strip())
            if sub_cur:
                current_segment += sub_cur
        elif len(current_segment) + len(u) <= max_chars:
            current_segment += u
        else:
            if current_segment.strip():
                segments.append(current_segment.strip())
            current_segment = u

    if current_segment.strip():
        segments.append(current_segment.strip())

    return [{"index": i, "text": s, "char_length": len(s)} for i, s in enumerate(segments) if s]


def public_job(job):
    job = json.loads(json.dumps(job))
    job["request"].pop("reference", None)
    return job


class JobService:
    def __init__(self, store, cfg, manager=None):
        self.store, self.cfg = store, cfg
        self.manager = manager or EngineManager(cfg)
        self.stop = threading.Event()
        self.wakeup = threading.Event()
        self.thread = None
        self.lock = ProcessLock(Path(cfg["root"]) / "runtime" / "gpu.lock")
        # CPU alignment runs off the single synthesis thread: job N aligns while job N+1 synthesizes.
        # A job is still completed only after its own alignment and commit (see complete()).
        self.aligner = ResidentAligner(cfg)
        self.alignments = queue.Queue()
        self.align_thread = None
        self.pending_cv = threading.Condition()
        self.pending = {}  # synthesis cache key -> jobs synthesized but not committed yet

    def start(self):
        self.lock.acquire()
        # Recover persisted jobs: queued can resume; interrupted inference cannot be claimed successful.
        for job in self.store.list("jobs"):
            if job["status"] not in TERMINAL and job["status"] != "queued":
                self.store.update("jobs", job["id"], status="failed", finished_at=time.time(),
                                  error={"code": "INTERRUPTED", "message": "API arrêtée pendant le traitement.", "retryable": True})
        self.align_thread = threading.Thread(target=self.align_loop, daemon=True, name="pps-alignment")
        self.align_thread.start()
        self.thread = threading.Thread(target=self.loop, daemon=True, name="pps-job-queue")
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wakeup.set()
        if self.thread:
            self.thread.join(timeout=self.cfg["service"]["worker_timeout_seconds"] + 10)
        # Ends an in-flight resident alignment at once; its job, like queued ones, ends CANCELLED (stop flag).
        self.aligner.close()
        if self.align_thread:
            self.alignments.put(None)
            self.align_thread.join(timeout=self.cfg["alignment"]["timeout_seconds"] + 10)
        self.manager.unload()
        self.lock.release()

    def validate(self, request, engine, db):
        text = request["text"].strip()
        if engine not in ENGINE_NAMES:
            raise PPSError("ENGINE_UNKNOWN", "Moteur inconnu.", 422)
        if not text or len(text) > self.cfg["service"]["max_text_chars"] or request["language"] not in capabilities(engine)["languages"]:
            raise PPSError("SCRIPT_INVALID", "Texte vide/trop long ou langue non prise en charge.", 422)
        if '<' in text or '>' in text:
            raise PPSError('UNSUPPORTED_CAPABILITY','Balises non transmises aux moteurs ; utilisez le plan MainClip pour les pauses.',422)
        options = request.get("options", {})
        supported = capabilities(engine)["options"]
        for key, value in options.items():
            if key not in supported or isinstance(value, bool) or not isinstance(value, (int, float)) or not supported[key][0] <= value <= supported[key][1]:
                raise PPSError("OPTION_UNSUPPORTED", "Option moteur non prise en charge.", 422, {"option": key, "supported": supported})
        voice = self.store.get("voices", request["voice_id"], db)
        if request.get('voice_version') is not None and request['voice_version']!=voice['version']:
            raise PPSError('VOICE_VERSION_CONFLICT','Version vocale périmée.',409)
        ref_id = request.get("reference_id") or voice.get('engine_reference_ids',{}).get(engine) or voice["active_reference_id"]
        ref = next((r for r in voice["references"] if r["id"] == ref_id), None)
        if not ref:
            raise PPSError("REFERENCE_UNKNOWN", "Référence vocale introuvable.", 404)
        if not ref["authorization"]["granted"]:
            raise PPSError("AUTHORIZATION_REQUIRED", "Clonage non autorisé.", 422)
        if engine == "cosyvoice" and not ref["transcription"]:
            raise PPSError("TRANSCRIPTION_REQUIRED", "CosyVoice exige une transcription corrigée.", 422)
        if engine == 'cosyvoice' and ref.get('transcription_verified') is False:
            raise PPSError('TRANSCRIPTION_UNVERIFIED','Corrigez et confirmez la transcription avant CosyVoice.',422)
        return {**request, "engine": engine, "text": text, "voice_version": voice["version"],
                "reference_id": ref_id, "reference_hash": ref["original_sha256"], "reference": ref}

    def submit(self, request, key=None, comparison=False):
        scope = "comparisons" if comparison else "syntheses"
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.store.transaction() as db:
            old = self.store.remembered(scope, key, digest, db)
            if old:
                return old
            engines = list(ENGINE_NAMES) if comparison else [request["engine"]]
            pending = len(self.store.list_status("jobs", TERMINAL, db, exclude=True))
            if pending + len(engines) > self.cfg["service"]["max_queue"]:
                raise PPSError("QUEUE_FULL", "File d'attente pleine.", 429, retryable=True)
            # Validate both requests before committing either comparison job.
            requests = [self.validate({**request, "reference_id": request.get('engine_reference_ids',{}).get(e) or request.get('reference_id'),
                "options": request.get("engine_options", {}).get(e, {})} if comparison else request, e, db) for e in engines]
            comparison_id = identity("comparison") if comparison else None
            jobs = []
            for req in requests:
                job = {"id": identity("job"), "status": "queued", "created_at": time.time(),
                       "request": req, "comparison_id": comparison_id, "audio_id": None,
                       "error": None, "metrics": {}, "cancel_requested": False, "attempts": 0,
                       "history": [{"status": "queued", "at": time.time()}]}
                self.store.put("jobs", job, db)
                jobs.append(job)
            result = {"comparison_id": comparison_id, "job_ids": [j["id"] for j in jobs]} if comparison else {"job_id": jobs[0]["id"], "status": "queued"}
            if comparison:
                self.store.put("comparisons", {"id": comparison_id, "created_at": time.time(), "job_ids": result["job_ids"],
                    "text": requests[0]["text"], "voice_id": request["voice_id"], "reference_id": requests[0]["reference_id"],
                    "reference_hash": requests[0]["reference_hash"],
                    'engine_reference_ids':{r['engine']:r['reference_id'] for r in requests},
                    'same_reference':len({r['reference_id'] for r in requests})==1}, db)
            self.store.remember(scope, key, digest, result, db)
        self.wakeup.set()
        return result

    def cancel(self, identifier):
        with self.store.transaction() as db:
            job = self.store.get("jobs", identifier, db)
            if job["status"] in TERMINAL:
                return public_job(job)
            job["cancel_requested"] = True
            if job["status"] == "queued":
                job.update(status="cancelled", finished_at=time.time())
                job["history"].append({"status": "cancelled", "at": time.time()})
            self.store.put("jobs", job, db)
        self.wakeup.set()
        return public_job(job)

    def submit_trials(self,request,key=None):
        """Validate the complete candidate experiment before queueing any inference."""
        digest=hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        refs=request['reference_ids']
        if len(set(refs))!=len(refs): raise PPSError('REFERENCE_DUPLICATE','Chaque référence est évaluée séparément une seule fois.',422)
        engines=list(ENGINE_NAMES) if request['engine']=='both' else [request['engine']]
        with self.store.transaction() as db:
            previous=self.store.remembered('reference_trials',key,digest,db)
            if previous: return previous
            validated=[self.validate({**request,'reference_id':ref,'options':request.get('engine_options',{}).get(e,{})},e,db)
                for ref in refs for e in engines]
            pending=len(self.store.list_status('jobs',TERMINAL,db,exclude=True))
            if pending+len(validated)>self.cfg['service']['max_queue']:
                raise PPSError('QUEUE_FULL','File d’attente pleine.',429,retryable=True)
            trial_id=identity('trial'); ids=[]
            for req in validated:
                job={'id':identity('job'),'status':'queued','created_at':time.time(),'request':req,
                    'comparison_id':None,'trial_id':trial_id,'audio_id':None,'error':None,'metrics':{},
                    'cancel_requested':False,'attempts':0,'history':[{'status':'queued','at':time.time()}]}
                self.store.put('jobs',job,db);ids.append(job['id'])
            trial={'id':trial_id,'voice_id':request['voice_id'],'reference_ids':refs,'engines':engines,
                'text':request['text'],'created_at':time.time(),'job_ids':ids,'winner':None,
                'strategy':'separate_short_references_same_text_no_concatenation'}
            self.store.put('reference_trials',trial,db)
            result={'trial_id':trial_id,'job_ids':ids}
            self.store.remember('reference_trials',key,digest,result,db)
        self.wakeup.set(); return result

    def transition(self, identifier, state, **changes):
        with self.store.transaction() as db:
            job = self.store.get("jobs", identifier, db)
            if job["status"] == "cancelled" and state != "cancelled":
                raise PPSError("CANCELLED", "Job annulé.", 409)
            job.update(status=state, **changes)
            job["history"].append({"status": state, "at": time.time()})
            return self.store.put("jobs", job, db)

    def submit_universal(self, tts_request, key=None):
        """Submit a universal TTS request conforming to V1.2.0-candidate."""
        from .contract import resolve_voice
        req_dict = tts_request.model_dump()
        digest = hashlib.sha256(json.dumps(req_dict, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

        with self.store.transaction() as db:
            old = self.store.remembered("universal_syntheses", key, digest, db)
            if old:
                return old

            # Pre-flight capability checks
            reqs = tts_request.requirements
            if reqs:
                if reqs.speechToSpeech:
                    raise PPSError("TTS_ERR_CAPABILITY_UNSUPPORTED", "La synthèse Speech-to-Speech n'est pas supportée localement sur PPS.", 422)
                if reqs.strictNativeCharacters:
                    raise PPSError("TTS_ERR_CAPABILITY_UNSUPPORTED", "Les timestamps de caractères natifs ne sont pas supportés sur les modèles PPS actuels.", 422)

            # Text validation
            text = tts_request.text.strip()
            if not text:
                raise PPSError("TTS_ERR_VALIDATION_FAILED", "Le texte à synthétiser est vide.", 422)
            if len(text) > self.cfg["service"]["max_text_chars"]:
                raise PPSError("TTS_ERR_VALIDATION_FAILED", f"Texte trop volumineux ({len(text)} caractères, max {self.cfg['service']['max_text_chars']}).", 422)
            if tts_request.language not in ("fr", "en"):
                raise PPSError("TTS_ERR_VALIDATION_FAILED", f"Langue '{tts_request.language}' non prise en charge.", 422)
            if "<" in text or ">" in text:
                raise PPSError("TTS_ERR_CAPABILITY_UNSUPPORTED", "Balises non transmises aux moteurs de synthèse.", 422)

            # Engine resolution
            engine = tts_request.engine
            if engine in (None, "auto"):
                engine = "chatterbox"
            if engine not in ENGINE_NAMES:
                raise PPSError("TTS_ERR_VALIDATION_FAILED", f"Moteur inconnu: '{engine}'.", 422)

            # Voice resolution
            voice = resolve_voice(self.store, tts_request.voiceId, tts_request.voiceIdentityId, tts_request.language, db)

            if tts_request.voiceVersion is not None and int(tts_request.voiceVersion) != voice.get("version", 1):
                raise PPSError("VOICE_VERSION_CONFLICT", "Version vocale périmée.", 409)

            ref_id = voice.get("engine_reference_ids", {}).get(engine) or voice.get("active_reference_id")
            ref = next((r for r in voice["references"] if r["id"] == ref_id), None)
            if not ref:
                raise PPSError("REFERENCE_UNKNOWN", "Référence vocale introuvable.", 404)
            if not ref["authorization"]["granted"]:
                raise PPSError("AUTHORIZATION_REQUIRED", "Clonage non autorisé pour cette référence.", 422)
            if engine == "cosyvoice" and not ref.get("transcription"):
                raise PPSError("TRANSCRIPTION_REQUIRED", "CosyVoice exige une transcription corrigée.", 422)

            # Options mapping
            options = {}
            seed = 1986
            if tts_request.voiceOptions:
                if tts_request.voiceOptions.seed is not None:
                    seed = tts_request.voiceOptions.seed
                if engine == "chatterbox" and tts_request.voiceOptions.providerSpecific and tts_request.voiceOptions.providerSpecific.chatterbox:
                    cb = tts_request.voiceOptions.providerSpecific.chatterbox
                    if cb.exaggeration is not None: options["exaggeration"] = cb.exaggeration
                    if cb.cfgWeight is not None: options["cfg_weight"] = cb.cfgWeight
                    if cb.temperature is not None: options["temperature"] = cb.temperature
                elif engine == "cosyvoice":
                    if tts_request.voiceOptions.speed is not None:
                        options["speed"] = tts_request.voiceOptions.speed

            # Validate engine options against boundaries
            supported = capabilities(engine)["options"]
            for opt_key, opt_val in options.items():
                if opt_key not in supported or not (supported[opt_key][0] <= opt_val <= supported[opt_key][1]):
                    raise PPSError("OPTION_UNSUPPORTED", f"Option {opt_key} hors bornes pour {engine}.", 422)

            # Alignment mode
            alignment_mode = "full"
            if reqs and reqs.alignmentMode == "none":
                alignment_mode = "quick"

            # Context & Cache
            prev_text = tts_request.context.previousText if tts_request.context else None
            next_text = tts_request.context.nextText if tts_request.context else None
            cache_mode = tts_request.cachePolicy.mode if tts_request.cachePolicy else "use"

            # Priority
            is_interactive = bool(reqs and reqs.priority == "interactive")
            priority_val = 100 if is_interactive else 0

            # Queue bounds check
            pending = len(self.store.list_status("jobs", TERMINAL, db, exclude=True))
            if pending >= self.cfg["service"]["max_queue"]:
                raise PPSError("TTS_ERR_QUEUE_FULL", "File d'attente pleine.", 429, retryable=True)

            job_id = identity("job")
            req_id = tts_request.requestId or identity("req")
            internal_request = {
                "text": text,
                "voice_id": voice["id"],
                "engine": engine,
                "language": tts_request.language,
                "reference_id": ref_id,
                "reference_hash": ref["original_sha256"],
                "voice_version": voice["version"],
                "seed": seed,
                "options": options,
                "alignment_mode": alignment_mode,
                "cache_mode": cache_mode,
                "previous_text": prev_text,
                "next_text": next_text,
                "reference": ref,
                "requestId": req_id
            }

            job = {
                "id": job_id,
                "status": "queued",
                "created_at": time.time(),
                "priority": priority_val,
                "request_id": req_id,
                "contract_version": "1.2.0",
                "universal_request": req_dict,
                "request": internal_request,
                "comparison_id": None,
                "audio_id": None,
                "error": None,
                "metrics": {},
                "cancel_requested": False,
                "attempts": 0,
                "history": [{"status": "queued", "at": time.time()}]
            }
            self.store.put("jobs", job, db)
            res = {
                "jobId": job_id,
                "status": "queued",
                "requestId": req_id,
                "createdAt": job["created_at"]
            }
            self.store.remember("universal_syntheses", key, digest, res, db)

        self.wakeup.set()
        return res

    def loop(self):
        while not self.stop.is_set():
            jobs = self.store.list_status("jobs", ("queued",))
            if not jobs:
                idle_seconds = self.cfg["service"]["worker_idle_seconds"]
                # A loaded worker is kept down to the lower resident floor; min_ram_gib gates fresh loads only.
                low_ram = psutil.virtual_memory().available/1024**3 < loaded_ram_floor(self.cfg)
                if time.monotonic() - self.manager.last_used > idle_seconds or low_ram:
                    self.manager.unload()
                self.aligner.stop_if_idle(idle_seconds, force=low_ram)
                self.wakeup.wait(.5)
                self.wakeup.clear()
                continue
            # Sort by priority DESC, created ASC with anti-starvation
            jobs.sort(key=lambda j: (-j.get("priority", 0), j["created_at"]))
            target_job = jobs[0]
            if target_job.get("priority", 0) > 0:
                consecutive = getattr(self, "_consecutive_interactive", 0)
                if consecutive >= 3:
                    normal_jobs = [j for j in jobs if j.get("priority", 0) <= 0]
                    if normal_jobs:
                        normal_jobs.sort(key=lambda j: j["created_at"])
                        target_job = normal_jobs[0]
                    self._consecutive_interactive = 0
                else:
                    self._consecutive_interactive = consecutive + 1
            else:
                self._consecutive_interactive = 0
            self.process(target_job)

    def process(self, job):
        identifier = job["id"]
        started = time.perf_counter()
        cancelled = lambda: self.stop.is_set() or self.store.get("jobs", identifier)["cancel_requested"]
        output = Path(self.cfg["paths"]["data"]) / "jobs" / identifier
        output.mkdir(parents=True, exist_ok=True)
        request = job["request"]
        metrics = {"queue_seconds": time.time() - job["created_at"]}
        try:
            if cancelled():
                raise PPSError('CANCELLED','Job annulé.',409)
            synthesis_key = cache_key(request,self.cfg)
            if request.get('cache_mode','use') in ('use','only'):
                # Serial semantics: an identical request waits for its twin's commit and reuses it.
                self.wait_pending(synthesis_key, cancelled)
            hit = lookup(self.store,synthesis_key) if request.get('cache_mode','use') in ('use','only') else None
            if hit:
                metrics.update(synthesis_cache_hit=True,source_audio_id=hit['id'],load_seconds=0,
                               synthesis_seconds=0,prepare_seconds=0,export_seconds=0,alignment_seconds=0,
                               duration_seconds=hit['pcm']['duration_seconds'],real_time_factor=0,
                               processing_seconds=time.perf_counter()-started,total_api_seconds=time.time()-job['created_at'])
                metrics['duration_meta'] = post_generation_metrics(
                    request['text'],
                    metrics['duration_seconds'],
                    request.get('target_duration_seconds')
                )
                with self.store.transaction() as db:
                    current = self.store.get('jobs',identifier,db)
                    if current['cancel_requested'] or self.stop.is_set():
                        raise PPSError('CANCELLED','Job annulé.',409)
                    current.update(status='completed',audio_id=hit['id'],metrics=metrics,finished_at=time.time())
                    current['history'].append({'status':'completed','at':time.time(),'cache_hit':True})
                    self.store.put('jobs',current,db)
                return
            if request.get('cache_mode')=='only':
                raise PPSError('CACHE_MISS','Aucun résultat vérifié en cache ; inférence interdite pour cette requête.',409)
            metrics['synthesis_cache_hit'] = False
            if psutil.virtual_memory().available/1024**3 < loaded_ram_floor(self.cfg):
                # Release an idle resident aligner (restarted lazily) before RAM forces a worker reload.
                self.aligner.stop_if_idle(0, force=True)
            self.transition(identifier, "loading", started_at=time.time())
            for attempt in range(self.cfg["service"]["resource_retries"] + 1):
                if cancelled():
                    raise PPSError("CANCELLED", "Job annulé.", 409)
                self.store.update("jobs", identifier, attempts=attempt + 1)
                try:
                    metrics.update(self.manager.ensure(request["engine"], cancelled))
                    break
                except PPSError as e:
                    if e.code != "RESOURCE_BUSY" or attempt >= self.cfg["service"]["resource_retries"]:
                        raise
                    self.store.update("jobs", identifier, resource_wait=e.payload())
                    for _ in range(self.cfg["service"]["resource_retry_seconds"] * 10):
                        if cancelled():
                            raise PPSError("CANCELLED", "Job annulé.", 409)
                        time.sleep(.1)

            # Segment text if long-form (max 450 chars to guarantee <= 25s segments for Whisper alignment)
            segments = segment_text(request["text"], max_chars=450)
            segments_info = []

            self.transition(identifier, "synthesizing")
            if len(segments) <= 1:
                # Monolithic generation (single segment)
                metrics.update(self.manager.synthesize(request, output / "raw.wav", cancelled))
                if cancelled():
                    raise PPSError("CANCELLED", "Job annulé.", 409)
                self.transition(identifier, "exporting")
                t = time.perf_counter()
                audio = export(output / "raw.wav", output)
                metrics["export_seconds"] = time.perf_counter() - t
            else:
                # Long-form multi-segment synthesis with kept-warm engine
                synth_seconds = 0.0
                seg_wav_paths = []
                progress_list = []
                for s in segments:
                    if cancelled():
                        raise PPSError("CANCELLED", "Job annulé.", 409)
                    idx = s["index"]
                    seg_out = output / f"seg_{idx}.wav"
                    # Segment request inherits options and engine conditioning
                    seg_req = {**request, "text": s["text"]}
                    if idx > 0:
                        seg_req["previous_text"] = segments[idx - 1]["text"]
                    if idx < len(segments) - 1:
                        seg_req["next_text"] = segments[idx + 1]["text"]

                    res = self.manager.synthesize(seg_req, seg_out, cancelled)
                    synth_seconds += res.get("synthesis_seconds", 0.0)
                    seg_wav_paths.append(seg_out)
                    progress_list.append({"index": idx, "text_preview": s["text"][:60], "status": "completed"})
                    self.store.update("jobs", identifier, segments_progress={
                        "total_segments": len(segments),
                        "completed_segments": len(seg_wav_paths),
                        "current_segment": idx + 1,
                        "segments": progress_list
                    })

                metrics["synthesis_seconds"] = synth_seconds
                self.transition(identifier, "exporting")
                t = time.perf_counter()
                concat_res = concatenate_pcm_files(seg_wav_paths, output / "concatenated.wav", gap_seconds=0.06)
                audio = export(output / "concatenated.wav", output)
                metrics["export_seconds"] = time.perf_counter() - t
                for s_meta, s_txt, s_wav in zip(concat_res["segments"], segments, seg_wav_paths):
                    segments_info.append({**s_meta, "text": s_txt["text"], "wav_path": str(s_wav)})

            # Alignment handling: quick vs full
            self.transition(identifier, "aligning")
            context = {"job": job, "identifier": identifier, "request": request, "metrics": metrics, "audio": audio,
                       "segments": segments, "segments_info": segments_info, "output": output,
                       "synthesis_key": synthesis_key, "started": started, "cancelled": cancelled}
            if request.get("alignment_mode", "full") != "quick" and self.cfg["service"].get("background_alignment", True):
                # CPU alignment and the final commit continue on the alignment thread; this thread is
                # free for the next synthesis. The job stays "aligning" until complete() commits it.
                with self.pending_cv:
                    self.pending[synthesis_key] = self.pending.get(synthesis_key, 0) + 1
                context["handed_off_at"] = time.perf_counter()
                self.alignments.put(context)
                return
            self.complete(context)
        except Exception as e:
            self.fail(identifier, e, metrics, started, unload=True)

    def wait_pending(self, key, cancelled):
        with self.pending_cv:
            while self.pending.get(key):
                if cancelled():
                    raise PPSError("CANCELLED", "Job annulé.", 409)
                self.pending_cv.wait(.2)

    def align_loop(self):
        while True:
            context = self.alignments.get()
            if context is None:
                return
            try:
                self.complete(context)
            except Exception as e:
                try:
                    # Alignment and commit never involve the GPU worker: never unload it from this thread.
                    self.fail(context["identifier"], e, context["metrics"], context["started"], unload=False)
                except Exception:
                    traceback.print_exc()
            finally:
                with self.pending_cv:
                    key = context["synthesis_key"]
                    self.pending[key] -= 1
                    if not self.pending[key]:
                        del self.pending[key]
                    self.pending_cv.notify_all()

    def fail(self, identifier, e, metrics, started, unload):
        error = e if isinstance(e, PPSError) else PPSError("INTERNAL_ERROR", str(e), 500)
        # A refused load (RESOURCE_BUSY) or a cancellation leaves no worker state to discard; a worker
        # cancelled mid-inference is already stopped by manager.wait. Other failures still unload.
        if unload and error.code not in ("RESOURCE_BUSY", "CANCELLED"):
            self.manager.unload()
        metrics["processing_seconds"] = time.perf_counter() - started
        self.transition(identifier, "cancelled" if error.code == "CANCELLED" else "failed",
                        error=error.payload(), metrics=metrics, finished_at=time.time())

    def complete(self, context):
        """Alignment, metrics and the final commit: a job is completed only here, after its alignment."""
        job, identifier, request = context["job"], context["identifier"], context["request"]
        metrics, audio, output = context["metrics"], context["audio"], context["output"]
        segments, segments_info, synthesis_key = context["segments"], context["segments_info"], context["synthesis_key"]
        started, cancelled = context["started"], context["cancelled"]
        if "handed_off_at" in context:
            metrics["alignment_queue_seconds"] = time.perf_counter() - context["handed_off_at"]
            if cancelled():
                raise PPSError("CANCELLED", "Job annulé.", 409)
        alignment_mode = request.get("alignment_mode", "full")
        if alignment_mode == "quick":
            alignment = {
                "words": [],
                "status": "skipped_quick_mode",
                "mainclip_compatible": False,
                "quality": "quick_preview_no_whisper_run",
                "note": "Alignement Whisper omis en mode aperçu rapide."
            }
            metrics["alignment_seconds"] = 0.0
        else:
            t = time.perf_counter()
            self.aligner.last_mode = None
            if audio["pcm"]["duration_seconds"] > 30.0 or len(segments_info) > 1:
                alignment = align_long_form(
                    audio["wav"], request.get("display_text") or request["text"],
                    request["language"], audio["pcm"]["duration_seconds"],
                    segments_info, self.cfg["alignment"], output, runner=self.aligner
                )
            else:
                alignment = align(
                    audio["wav"], request.get("display_text") or request["text"],
                    request["language"], audio["pcm"]["duration_seconds"],
                    self.cfg["alignment"], output, runner=self.aligner
                )
            metrics["alignment_seconds"] = time.perf_counter() - t
            if self.aligner.last_mode:
                metrics["alignment_runner"] = self.aligner.last_mode

        metrics["duration_seconds"] = audio["pcm"]["duration_seconds"]
        metrics["real_time_factor"] = (metrics["synthesis_seconds"] / metrics["duration_seconds"]) if metrics["duration_seconds"] > 0 else 0
        metrics["processing_seconds"] = time.perf_counter() - started
        metrics["total_api_seconds"] = time.time() - job["created_at"]
        metrics["duration_meta"] = post_generation_metrics(
            request["text"],
            metrics["duration_seconds"],
            request.get("target_duration_seconds")
        )
        metrics["segments_count"] = len(segments)

        # Commit result and completion together, checking cancellation one final time.
        with self.store.transaction() as db:
            current = self.store.get("jobs", identifier, db)
            if current["cancel_requested"] or self.stop.is_set():
                raise PPSError("CANCELLED", "Job annulé.", 409)
            audio_id = identity("audio")
            self.store.put("audio", {"id": audio_id, "job_id": identifier, "engine": request["engine"],
                "voice_id": request["voice_id"], "reference_id": request["reference_id"],
                "reference_hash": request["reference_hash"], "text": request["text"],
                'effective_engine_options':effective_options(request['engine'],request['options']),
                'voice_version':request['voice_version'],'generation_request':public_job(job)['request'],
                "metrics": metrics, "alignment": alignment, **audio}, db)
            current.update(status="completed", audio_id=audio_id, metrics=metrics, finished_at=time.time())
            current["history"].append({"status": "completed", "at": time.time()})
            self.store.put("jobs", current, db)
            if request.get('cache_mode','use')!='bypass':
                self.store.put('synthesis_cache',{'id':synthesis_key,'audio_id':audio_id,'created_at':time.time()},db)

