"""Streaming imports, recoverable CPU analysis and explicit reference selection."""
import asyncio
import hashlib
import json
import math
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
import psutil
from .audio import ffmpeg, hash_file, prepare_reference
from .errors import PPSError
from .storage import identity
from .config import ROOT
from .voice_preparation import VoicePreparationMixin, ACTIVE

CHUNK_BYTES = 8 * 1024**2
WINDOW_SECONDS = 60
MAX_CANDIDATES = 40
CPU_RAM_GIB = 3

def analysis_contract():
    manifest=ROOT/'tools/media/manifest.json'
    return {'worker_sha256':hash_file(ROOT/'scripts/media_worker.py'),
        'tools_manifest_sha256':hash_file(manifest) if manifest.is_file() else None,'window_seconds':WINDOW_SECONDS}

def options(cfg):
    return {'min_disk_gib':cfg['resources']['min_disk_gib'], 'window_seconds':WINDOW_SECONDS,
        'chunk_bytes':CHUNK_BYTES, 'min_ram_gib':CPU_RAM_GIB, 'cpu_threads':2,
        'source_duration_limit_seconds':None, 'reference_min_seconds':3, 'reference_max_seconds':30,
        'max_retained_candidates':MAX_CANDIDATES, 'fine_tuning_available':False,
        'engine_reference_windows':{'chatterbox':{'speech_tokens_seconds':6,'decoder_seconds':10,'speaker_embedding':'full selected reference'},
            'cosyvoice':{'speech_tokenizer_max_seconds':30}}}

def disk_guard(cfg, additional=0):
    free=shutil.disk_usage(cfg['paths']['data']).free
    reserve=cfg['resources']['min_disk_gib']*1024**3
    if free < reserve+additional:
        raise PPSError('DISK_SPACE_LOW','Espace disque insuffisant ; libérez du disque puis reprenez.',409,
            {'free_bytes':free,'reserve_bytes':int(reserve),'additional_bytes':additional},True)

def public_media(value):
    return {k:v for k,v in value.items() if k not in ('path',)}

def public_candidate(value):
    value=json.loads(json.dumps(value))
    value.pop('path',None)
    value.get('metrics',{}).pop('embedding',None)
    return value

def public_analysis(value):
    result={k:v for k,v in value.items() if k not in ('track_path','candidate_paths')}
    result['candidates']=[public_candidate(c) for c in value.get('candidates',[])]
    return result

def probe(cfg,path):
    executable=Path(os.environ.get('PPS_FFPROBE') or shutil.which('ffprobe') or ROOT/'tools/media/ffprobe.exe')
    if not executable.is_file():
        raise PPSError('FFPROBE_MISSING','FFprobe absent du dossier tools/media.',503)
    with open(path,'rb') as source:
        header=source.read(4096).lstrip().lower()
    if header.startswith((b'#extm3u',b'ffconcat',b'<?xml',b'<mpd')):
        raise PPSError('PLAYLIST_UNSUPPORTED','Importez un conteneur autonome, pas une liste de fichiers externes.',422)
    try:
        result=subprocess.run([str(executable),'-v','error','-protocol_whitelist','file,pipe',
            '-show_entries','format=duration,format_name,start_time:stream=index,codec_type,codec_name,sample_rate,channels,channel_layout,duration,start_time,bits_per_raw_sample:stream_tags=language,title',
            '-of','json',str(path)],capture_output=True,
            timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),check=True)
        data=json.loads(result.stdout)
    except (OSError,subprocess.SubprocessError,ValueError) as exc:
        raise PPSError('MEDIA_INVALID','Fichier illisible ou format non pris en charge.',422) from exc
    streams=[]
    for stream in data.get('streams',[]):
        if stream.get('codec_type')=='audio':
            streams.append({k:stream.get(k) for k in ('index','codec_name','sample_rate','channels','channel_layout','duration','start_time','bits_per_raw_sample')}
                | {'tags':stream.get('tags',{})})
    if not streams:
        raise PPSError('AUDIO_TRACK_MISSING','Aucune piste audio exploitable.',422)
    try: duration=float(data.get('format',{}).get('duration',0))
    except (TypeError,ValueError): duration=0
    if not math.isfinite(duration) or duration<=0:
        raise PPSError('DURATION_UNKNOWN','Durée indéterminée ; remuxez le média en fichier local fini.',422)
    return {'duration_seconds':duration,'format':data.get('format',{}).get('format_name'),
        'audio_tracks':streams, 'track_speaker_identity':'unverified',
        'source_start_time':data.get('format',{}).get('start_time','0')}

class CPUWorker:
    def __init__(self,cfg,cancelled,log):
        if psutil.virtual_memory().available/1024**3 < CPU_RAM_GIB:
            raise PPSError('RESOURCE_BUSY','Analyse CPU : 3 Gio de RAM libre nécessaires avant les imports.',409,retryable=True)
        self.cancelled=cancelled
        self.messages=queue.Queue()
        self.log=open(log,'ab')
        environment=os.environ.copy()
        environment.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2')
        self.process=subprocess.Popen([cfg['cosyvoice']['python'],str(ROOT/'scripts/media_worker.py')],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,encoding='utf-8',
            env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.peak_rss=0
        self.cpu_seconds=0
        self.worker_pid=self.process.pid
        def read():
            try:
                for line in self.process.stdout:
                    try: self.messages.put(json.loads(line))
                    except ValueError: continue
            finally: self.messages.put({'dead':True})
        threading.Thread(target=read,daemon=True).start()
        try:
            if not self.wait(120).get('ready'):
                raise PPSError('ANALYSIS_WORKER_FAILED','Démarrage de l’analyse CPU impossible ; voir le journal.',503)
        except Exception:
            self.close(); raise

    def wait(self,timeout):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            if self.cancelled(): raise PPSError('CANCELLED','Analyse interrompue ; les fenêtres terminées sont conservées.',409)
            try:
                p=psutil.Process(self.worker_pid)
                self.peak_rss=max(self.peak_rss,p.memory_info().rss)
                t=p.cpu_times(); self.cpu_seconds=t.user+t.system
            except psutil.Error: pass
            try:
                result=self.messages.get(timeout=.15)
                if result.get('dead'):
                    raise PPSError('ANALYSIS_WORKER_FAILED','Worker CPU arrêté ; voir le journal.',503,retryable=True)
                memory=result.get('memory') or result.get('result',{}).get('memory')
                if memory:
                    actual=memory['process']
                    self.worker_pid=actual['pid']
                    self.peak_rss=max(self.peak_rss,actual.get('peak_working_set_bytes') or actual['working_set_bytes'])
                return result
            except queue.Empty: pass
        raise PPSError('ANALYSIS_TIMEOUT','Délai par fenêtre dépassé ; reprise possible.',504,retryable=True)

    def call(self,action,path,manual=False):
        self.process.stdin.write(json.dumps({'action':action,'path':str(path),'manual':manual})+'\n')
        self.process.stdin.flush()
        result=self.wait(180)
        if not result.get('ok'):
            busy='RESOURCE_BUSY' in result.get('error','')
            raise PPSError('RESOURCE_BUSY' if busy else 'ANALYSIS_FAILED',result.get('error','Analyse impossible'),409 if busy else 422,retryable=busy)
        return result

    def close(self):
        # Windows venv python.exe can be a launcher: terminate its owned descendants too.
        try:
            parent=psutil.Process(self.process.pid)
            owned=parent.children(recursive=True)+[parent]
        except psutil.Error: owned=[]
        if self.process.stdin:
            try: self.process.stdin.close()
            except OSError: pass
        for process in owned:
            try: process.terminate()
            except psutil.Error: pass
        _,alive=psutil.wait_procs(owned,timeout=3)
        for process in alive:
            try: process.kill()
            except psutil.Error: pass
        if self.process.poll() is None:
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired: self.process.kill();self.process.wait(timeout=3)
        self.log.close()

class MediaService(VoicePreparationMixin):
    def __init__(self,store,cfg):
        self.store,self.cfg=store,cfg
        self.stop=threading.Event(); self.wakeup=threading.Event(); self.thread=None
        self.upload_locks={}
        self.cpu_lock=threading.Lock()
        self.save_lock=threading.Lock()
        self.preparation_lock=threading.RLock()

    def start(self):
        for m in self.store.list('media'):
            if m['status']=='probing': self.store.update('media',m['id'],status='uploading')
        for a in self.store.list('media_analyses'):
            if a['status']=='running':
                self.store.update('media_analyses',a['id'],status='paused',cancel_requested=False,
                    error={'code':'INTERRUPTED','message':'Service arrêté : reprise depuis la dernière fenêtre terminée.'})
        self.recover_preparations()
        self.thread=threading.Thread(target=self.loop,daemon=True,name='pps-media-cpu')
        self.thread.start()

    def close(self):
        self.stop.set(); self.wakeup.set()
        if self.thread: self.thread.join(timeout=10)

    def begin(self,body):
        if not body['authorized'] or not body['declaration'].strip():
            raise PPSError('AUTHORIZATION_REQUIRED','Une autorisation de clonage et d’utilisation est nécessaire pour chaque source.',422)
        size=body['size_bytes']
        # Physical disk space, including uploads already reserved; never a duration cap.
        reserved=sum(max(0,m['size_bytes']-self.received(m)) for m in self.store.list('media') if m['status']=='uploading')
        disk_guard(self.cfg,size+reserved)
        with self.store.transaction() as db:
            if body.get('voice_id'):
                voice=self.store.get('voices',body['voice_id'],db)
            else:
                if not body['name'].strip(): raise PPSError('VOICE_INVALID','Nom de la voix requis.',422)
                voice={'id':identity('voice'),'name':body['name'].strip(),'language':body['language'],
                    'version':0,'references':[],'active_reference_id':None,'created_at':time.time()}
                self.store.put('voices',voice,db)
            identifier=identity('media')
            directory=Path(self.cfg['paths']['data'])/'media'/identifier
            directory.mkdir(parents=True,exist_ok=True)
            value={'id':identifier,'voice_id':voice['id'],'filename':body['filename'].replace('\\','/').split('/')[-1],
                'size_bytes':size,'fingerprint':body['fingerprint'],'received_bytes':0,'path':str(directory/'source.media'),
                'status':'uploading','created_at':time.time(),
                'authorization':{'granted':True,'declaration':body['declaration'].strip(),'recorded_at':time.time()},
                'probe':None,'sha256':None}
            self.store.put('media',value,db)
        return public_media(value)

    def received(self,m):
        p=Path(m['path']); return p.stat().st_size if p.is_file() else 0

    def get_media(self,identifier):
        m=self.store.get('media',identifier)
        return public_media({**m,'received_bytes':self.received(m)})

    async def append(self,identifier,offset,request):
        lock=self.upload_locks.setdefault(identifier,asyncio.Lock())
        async with lock:
            m=self.store.get('media',identifier)
            if m['status']!='uploading': raise PPSError('UPLOAD_STATE_INVALID','Import déjà terminé ou arrêté.',409)
            actual=self.received(m)
            if actual!=offset: raise PPSError('UPLOAD_OFFSET_CONFLICT','Reprenez à l’offset réellement enregistré.',409,{'received_bytes':actual})
            disk_guard(self.cfg,CHUNK_BYTES)
            used=0
            try:
                with open(m['path'],'ab') as dest:
                    async for block in request.stream():
                        used+=len(block)
                        if used>CHUNK_BYTES or offset+used>m['size_bytes']:
                            raise PPSError('UPLOAD_TOO_LARGE','Bloc de 8 Mio maximum ; taille annoncée respectée.',413)
                        dest.write(block)
                    dest.flush(); os.fsync(dest.fileno())
            except BaseException:
                # Roll back only the interrupted block of this PPS-owned upload.
                with open(m['path'],'r+b') as dest: dest.truncate(offset)
                raise
            self.store.update('media',identifier,received_bytes=actual+used)
            return self.get_media(identifier)

    def complete(self,identifier):
        with self.store.transaction() as db:
            m=self.store.get('media',identifier,db)
            if m['status']=='ready': return public_media(m)
            if m['status'] not in ('uploading','invalid'): raise PPSError('UPLOAD_STATE_INVALID','Import non finalisable.',409)
            if self.received(m)!=m['size_bytes']: raise PPSError('UPLOAD_INCOMPLETE','Le fichier n’est pas entièrement reçu.',409)
            m['status']='probing'; self.store.put('media',m,db)
        try:
            metadata=probe(self.cfg,m['path'])
            m.update(status='ready',probe=metadata,sha256=hash_file(m['path']),received_bytes=self.received(m),error=None)
        except Exception as exc:
            self.store.update('media',identifier,status='invalid',error=exc.payload() if isinstance(exc,PPSError) else {'message':str(exc)})
            raise
        self.store.put('media',m)
        return public_media(m)

    def analyze(self,identifier,track,fresh=False):
        m=self.store.get('media',identifier)
        if m['status']!='ready': raise PPSError('MEDIA_NOT_READY','Finalisez l’import avant l’analyse.',409)
        if track not in [s['index'] for s in m['probe']['audio_tracks']]: raise PPSError('TRACK_INVALID','Choisissez une piste audio identifiée par FFprobe.',422)
        with self.store.transaction() as db:
            existing=next((a for a in self.store.list('media_analyses',db) if a['media_id']==identifier and a['track_index']==track),None)
            if existing and not fresh: return public_analysis(existing)
            a={'id':identity('analysis'),'media_id':identifier,'voice_id':m['voice_id'],'track_index':track,
                'status':'queued','cancel_requested':False,'created_at':time.time(),'processed_seconds':0,
                'analysis_contract':analysis_contract(),
                'total_seconds':m['probe']['duration_seconds'],'progress':0,'phase':'queued','window_count':0,
                'speech_seconds':0,'candidates':[],'metrics':{'analysis_seconds':0,'worker_peak_rss_bytes':0,'worker_cpu_seconds':0,'gpu_used':False},'error':None,
                'track_path':str(Path(m['path']).parent/f'track-{track}.mka'),'track_ready':False}
            self.store.put('media_analyses',a,db)
        self.wakeup.set(); return public_analysis(a)

    def cancel(self,identifier):
        with self.store.transaction() as db:
            a=self.store.get('media_analyses',identifier,db)
            if a['status'] not in ('completed','cancelled'):
                a['cancel_requested']=True
                if a['status']!='running': a['status']='cancelled'
                self.store.put('media_analyses',a,db)
        return public_analysis(a)

    def resume(self,identifier):
        with self.store.transaction() as db:
            a=self.store.get('media_analyses',identifier,db)
            if a['status'] in ('paused','cancelled','failed'):
                if a.get('analysis_contract')!=analysis_contract():
                    raise PPSError('ANALYSIS_REVISION_CHANGED','L’analyseur a changé. Lancez une nouvelle analyse de la piste ; les anciennes données sont conservées.',409)
                a.update(status='queued',cancel_requested=False,error=None)
                self.store.put('media_analyses',a,db)
        self.wakeup.set(); return public_analysis(a)

    def loop(self):
        while not self.stop.is_set():
            pending=[a for a in self.store.list('media_analyses') if a['status']=='queued']
            if pending: self.process(pending[0]['id'])
            for preparation in self.store.list('voice_preparations'):
                if self.stop.is_set(): break
                if preparation['status'] in ACTIVE: self.advance_preparation(preparation['id'])
            if not pending: self.wakeup.wait(.5); self.wakeup.clear()

    def command(self,args,cancelled,progress=None,additional_bytes=0):
        disk_guard(self.cfg,additional_bytes)
        log=Path(self.cfg['paths']['logs'])/'media-ffmpeg.log'
        with log.open('ab') as err:
            proc=subprocess.Popen([ffmpeg(),'-nostdin','-hide_banner','-loglevel','error','-y',
                '-threads','2','-filter_threads','2',*map(str,args)],stdout=subprocess.PIPE,stderr=err,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            lines=queue.Queue()
            def read():
                for line in proc.stdout: lines.put(line.decode('utf-8','replace').strip())
            threading.Thread(target=read,daemon=True).start()
            last=time.monotonic(); last_disk=last
            try:
                while proc.poll() is None:
                    if cancelled(): raise PPSError('CANCELLED','Opération média interrompue ; reprise disponible.',409)
                    if time.monotonic()-last_disk>2: disk_guard(self.cfg,1024**2); last_disk=time.monotonic()
                    while not lines.empty():
                        line=lines.get(); last=time.monotonic()
                        if progress and line.startswith('out_time_us='):
                            try: progress(int(line.split('=',1)[1])/1e6)
                            except ValueError: pass
                    # This is a stall guard, not a total media duration/time limit.
                    if time.monotonic()-last>180: raise PPSError('MEDIA_STALLED','FFmpeg ne progresse plus depuis 180 s.',504,retryable=True)
                    time.sleep(.1)
                while not lines.empty():
                    line=lines.get()
                    if progress and line.startswith('out_time_us='):
                        try: progress(int(line.split('=',1)[1])/1e6)
                        except ValueError: pass
                if proc.returncode: raise PPSError('MEDIA_DECODE_FAILED','Extraction audio impossible ; voir logs/media-ffmpeg.log.',422)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try: proc.wait(timeout=5)
                    except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)

    def extract(self,a,start,end,path,cancelled,rate=16000):
        source=self.store.get('media',a['media_id'])['path']
        # Accurate output seek; bounded output. Original container remains immutable.
        self.command(['-protocol_whitelist','file,pipe','-ss',start,'-i',source,'-t',end-start,
            '-map',f"0:{a['track_index']}",'-vn','-sn','-dn',
            '-af',f'aresample=async=1:first_pts=0,apad=whole_dur={end-start}',
            '-ac','1','-ar',rate,'-c:a','pcm_s16le',
            '-progress','pipe:1',path],cancelled,additional_bytes=int((end-start)*rate*2)+1024**2)

    def process(self,identifier):
        started=time.perf_counter(); worker=None
        a=self.store.get('media_analyses',identifier)
        def cancelled(): return self.stop.is_set() or self.store.get('media_analyses',identifier)['cancel_requested']
        try:
            with self.cpu_lock:
                if cancelled(): raise PPSError('CANCELLED','Analyse annulée.',409)
                if a.get('analysis_contract')!=analysis_contract():
                    raise PPSError('ANALYSIS_REVISION_CHANGED','Version d’analyse différente ; lancez une nouvelle analyse de cette piste.',409)
                self.store.update('media_analyses',identifier,status='running',phase='extracting_audio',error=None)
                m=self.store.get('media',a['media_id']); directory=Path(m['path']).parent
                if not a['track_ready']:
                    part=Path(a['track_path']+'.part.mka')
                    def progress(seconds): self.store.update('media_analyses',identifier,extraction_seconds=min(seconds,a['total_seconds']))
                    # Stream-copy preserves encoded packets, channels and sample rate.
                    disk_guard(self.cfg,8*1024**2)
                    budget=int(shutil.disk_usage(self.cfg['paths']['data']).free-self.cfg['resources']['min_disk_gib']*1024**3-8*1024**2)
                    self.command(['-protocol_whitelist','file,pipe','-i',m['path'],'-map',f"0:{a['track_index']}",
                        '-vn','-sn','-dn','-c:a','copy','-fs',budget,'-progress','pipe:1',part],cancelled,progress,8*1024**2)
                    if part.stat().st_size>=budget-1024**2:
                        raise PPSError('DISK_SPACE_LOW','Extraction audio interrompue à la réserve de disque ; libérez du disque et reprenez.',409,retryable=True)
                    part.replace(a['track_path']); a['track_ready']=True
                    self.store.update('media_analyses',identifier,track_ready=True)
                self.store.update('media_analyses',identifier,phase='detecting_speech_and_ranking')
                worker=CPUWorker(self.cfg,cancelled,directory/'analysis-worker.log')
                position=a['processed_seconds']
                while position<a['total_seconds']-.001:
                    if cancelled(): raise PPSError('CANCELLED','Analyse annulée.',409)
                    end=min(position+WINDOW_SECONDS,a['total_seconds'])
                    # A 2 s analysis overlap helps detect phrases at boundaries; no concatenation.
                    begin=max(0,position-2); wav=directory/'analysis-window.wav'
                    self.extract(a,begin,end,wav,cancelled)
                    response=worker.call('analyze',wav); result=response['result']
                    found=[]
                    for c in result['candidates']:
                        cstart=begin+c['start']; cend=begin+c['end']
                        if cend<=position+.05: continue
                        c={'id':identity('candidate'),'analysis_id':identifier,'media_id':m['id'],
                            'start_seconds':round(cstart,4),'end_seconds':round(cend,4),'track_index':a['track_index'],
                            'source_sha256':m['sha256'],'filename':m['filename'],'metrics':c['metrics'],
                            'transcription':None,'speaker_confirmed':False,'source_timestamps_basis':'seconds relative to start of imported media'}
                        found.append(c)
                    # One durable bounded record per window, not a growing waveform in memory.
                    checkpoint={'id':identifier+f':{a["window_count"]:08d}','analysis_id':identifier,
                        'start_seconds':position,'end_seconds':end,'speech_seconds':result['speech_seconds'],'candidates':found}
                    retained=sorted(a['candidates']+found,key=lambda c:c['metrics']['score'],reverse=True)
                    unique=[]
                    for c in retained:
                        if not any(abs(c['start_seconds']-p['start_seconds'])<1 and abs(c['end_seconds']-p['end_seconds'])<1 for p in unique): unique.append(c)
                        if len(unique)>=MAX_CANDIDATES: break
                    for c in unique:
                        e=c['metrics'].get('embedding')
                        peers=[p['metrics'].get('embedding') for p in unique if p['id']!=c['id'] and p['metrics'].get('embedding')]
                        c['metrics']['peer_consistency_cosine']=max((sum(x*y for x,y in zip(e,p)) for p in peers),default=None) if e else None
                    with self.store.transaction() as db:
                        current=self.store.get('media_analyses',identifier,db)
                        current.update(processed_seconds=end,progress=end/a['total_seconds'],window_count=a['window_count']+1,
                            speech_seconds=a['speech_seconds']+result['speech_seconds']*max(0,end-position)/max(.001,end-begin),candidates=unique)
                        self.store.put('media_windows',checkpoint,db); self.store.put('media_analyses',current,db)
                        a=current
                    position=end
                with self.store.transaction() as db:
                    current=self.store.get('media_analyses',identifier,db)
                    current.update(status='cancelled' if current['cancel_requested'] or self.stop.is_set() else 'completed',
                        phase='completed',progress=1,finished_at=time.time())
                    self.store.put('media_analyses',current,db)
        except Exception as exc:
            error=exc.payload() if isinstance(exc,PPSError) else {'code':'ANALYSIS_FAILED','message':str(exc),'retryable':True}
            state='cancelled' if error['code']=='CANCELLED' else 'paused' if error['code'] in ('RESOURCE_BUSY','DISK_SPACE_LOW','ANALYSIS_TIMEOUT','MEDIA_STALLED') else 'failed'
            self.store.update('media_analyses',identifier,status=state,error=error)
        finally:
            if worker: worker.close()
            with self.store.transaction() as db:
                current=self.store.get('media_analyses',identifier,db)
                metrics=current['metrics']; metrics['analysis_seconds']+=time.perf_counter()-started
                if worker:
                    metrics['worker_peak_rss_bytes']=max(metrics['worker_peak_rss_bytes'],worker.peak_rss)
                    metrics['worker_cpu_seconds']+=worker.cpu_seconds
                self.store.put('media_analyses',current,db)

    def candidate(self,analysis_id,candidate_id):
        a=self.store.get('media_analyses',analysis_id)
        c=next((c for c in a['candidates'] if c['id']==candidate_id),None)
        if c is None: raise PPSError('CANDIDATE_UNKNOWN','Extrait absent de la sélection courante.',404)
        return a,c

    def clip(self,analysis_id,candidate_id):
        a,c=self.candidate(analysis_id,candidate_id)
        media=self.store.get('media',a['media_id'])
        path=Path(media['path']).parent/(candidate_id+'.wav')
        if not path.exists(): self.extract(a,c['start_seconds'],c['end_seconds'],path,lambda:self.stop.is_set(),24000)
        return path

    def manual(self,analysis_id,start,end):
        a=self.store.get('media_analyses',analysis_id)
        if not math.isfinite(start) or not math.isfinite(end) or not (0<=start<end<=a['total_seconds']+.001) or not 3<=end-start<=30:
            raise PPSError('REFERENCE_DURATION_INVALID','Choisissez un passage entre 3 et 30 s à l’intérieur du média ; la durée source reste illimitée.',422)
        if a['status'] in ('queued','running'): raise PPSError('ANALYSIS_BUSY','Annulez ou terminez l’analyse avant d’éditer les candidats.',409)
        with self.cpu_lock:
            m=self.store.get('media',a['media_id']); cid=identity('candidate'); path=Path(m['path']).parent/(cid+'-analysis.wav')
            self.extract(a,start,end,path,lambda:self.stop.is_set())
            w=CPUWorker(self.cfg,lambda:self.stop.is_set(),Path(m['path']).parent/'analysis-worker.log')
            try: result=w.call('analyze',path,True)['result']
            finally: w.close()
            metrics=result['candidates'][0]['metrics'] if result['candidates'] else {'score':0,'speaker_status':'unverified','embedding':None}
        c={'id':cid,'analysis_id':analysis_id,'media_id':m['id'],'track_index':a['track_index'],
            'start_seconds':start,'end_seconds':end,'source_sha256':m['sha256'],'filename':m['filename'],
            'metrics':metrics,'manual':True,'speaker_confirmed':False,'transcription':None}
        with self.store.transaction() as db:
            current=self.store.get('media_analyses',analysis_id,db)
            current['candidates']=[c]+current['candidates'][:MAX_CANDIDATES-1]
            self.store.put('media_analyses',current,db)
        return public_candidate(c)

    def transcribe(self,analysis_id,candidate_id,cancelled=None):
        cancelled=cancelled or (lambda:self.stop.is_set())
        with self.cpu_lock:
            if cancelled(): raise PPSError('CANCELLED','Préparation interrompue.',409)
            a,c=self.candidate(analysis_id,candidate_id); m=self.store.get('media',a['media_id'])
            if a['status'] in ('running','queued'): raise PPSError('ANALYSIS_BUSY','Terminez ou annulez l’analyse avant la transcription.',409)
            path=Path(m['path']).parent/(candidate_id+'-asr.wav')
            self.extract(a,c['start_seconds'],c['end_seconds'],path,cancelled)
            w=CPUWorker(self.cfg,cancelled,Path(m['path']).parent/'analysis-worker.log')
            try: result=w.call('transcribe',path)
            finally: w.close()
        with self.store.transaction() as db:
            if cancelled(): raise PPSError('CANCELLED','Préparation interrompue.',409)
            current=self.store.get('media_analyses',analysis_id,db)
            target=next(p for p in current['candidates'] if p['id']==candidate_id)
            target['transcription']={**result['result'],'seconds':result['seconds'],
                'resource_metrics':{'worker_peak_rss_bytes':w.peak_rss,'worker_cpu_seconds':w.cpu_seconds,'gpu_used':False,'cpu_threads':2}}
            probabilities=[w['probability'] for s in result['result'].get('segments',[]) for w in s.get('words',[]) if 'probability' in w]
            target['metrics']['asr_mean_word_probability']=sum(probabilities)/len(probabilities) if probabilities else None
            target['metrics']['clarity_status']='asr_proxy_requires_user_listening'
            self.store.put('media_analyses',current,db)
        return public_candidate(target)

    def save(self,analysis_id,candidate_id,body):
        with self.save_lock:
            return self._save(analysis_id,candidate_id,body)

    def _save(self,analysis_id,candidate_id,body):
        a,c=self.candidate(analysis_id,candidate_id); m=self.store.get('media',a['media_id'])
        if not body['speaker_confirmed']:
            raise PPSError('SPEAKER_CONFIRMATION_REQUIRED','Écoutez l’extrait et confirmez qu’il contient uniquement le locuteur autorisé.',422)
        if not m['authorization']['granted']:
            raise PPSError('AUTHORIZATION_REQUIRED','Clonage non autorisé.',422)
        if body['transcription_verified'] and not body['transcription'].strip():
            raise PPSError('TRANSCRIPTION_REQUIRED','Une transcription vide ne peut être vérifiée.',422)
        if a['status'] in ('queued','running'): raise PPSError('ANALYSIS_BUSY','Terminez ou annulez l’analyse avant l’enregistrement.',409)
        if c.get('saved_reference_id'):
            return self.store.get('voices',m['voice_id'])
        refid=identity('ref'); directory=Path(self.cfg['paths']['data'])/'voices'/m['voice_id']/refid
        directory.mkdir(parents=True,exist_ok=True)
        original=directory/'original.wav'
        self.extract(a,c['start_seconds'],c['end_seconds'],original,lambda:self.stop.is_set(),24000)
        prepared=prepare_reference(original,directory)
        with self.store.transaction() as db:
            voice=self.store.get('voices',m['voice_id'],db)
            known=[r.get('media_metrics',{}).get('embedding') for r in voice['references'] if r.get('media_metrics',{}).get('embedding')]
            e=c['metrics'].get('embedding')
            consistency=max((sum(x*y for x,y in zip(e,p)) for p in known),default=None) if e else None
            ref={'id':refid,'filename':m['filename']+f"-{c['start_seconds']:.2f}-{c['end_seconds']:.2f}.wav",'original':str(original),
                'transcription':body['transcription'].strip(),'transcription_verified':body['transcription_verified'],
                'language':body['language'],'authorization':m['authorization'],**prepared,
                'speaker_confirmation':{'confirmed':True,'recorded_at':time.time(),'method':'user_listening'},
                'media_metrics':{**c['metrics'],'consistency_with_saved_voice_cosine':consistency},
                'provenance':{'media_id':m['id'],'source_filename':m['filename'],'source_sha256':m['sha256'],
                    'track_index':a['track_index'],'start_seconds':c['start_seconds'],'end_seconds':c['end_seconds'],
                    'candidate_id':candidate_id,'analysis_id':analysis_id,'asr':c.get('transcription'),
                    'extraction':'PCM16 mono 24 kHz without gain; immutable original media retained'},
                'preparation':{'chatterbox':'audio_ready_conditioning_pending',
                    'cosyvoice':'audio_ready_conditioning_pending' if body['transcription_verified'] else 'verified_transcription_required'}}
            voice['version']+=1; voice['references'].append(ref)
            if not voice.get('active_reference_id'): voice['active_reference_id']=refid
            self.store.put('voices',voice,db)
            current=self.store.get('media_analyses',analysis_id,db)
            for candidate in current['candidates']:
                if candidate['id']==candidate_id: candidate.update(saved_reference_id=refid,speaker_confirmed=True)
            self.store.put('media_analyses',current,db)
        return voice

    def choose(self,voice_id,engine,reference_id):
        with self.store.transaction() as db:
            v=self.store.get('voices',voice_id,db)
            r=next((r for r in v['references'] if r['id']==reference_id),None)
            if not r: raise PPSError('REFERENCE_UNKNOWN','Référence inconnue.',404)
            if not r['authorization']['granted']:raise PPSError('AUTHORIZATION_REQUIRED','Référence non autorisée.',422)
            if engine=='cosyvoice' and (not r['transcription'] or r.get('transcription_verified') is False):
                raise PPSError('TRANSCRIPTION_UNVERIFIED','CosyVoice exige une transcription vérifiée.',422)
            v.setdefault('engine_reference_ids',{})[engine]=reference_id; v['version']+=1
            self.store.put('voices',v,db)
        return v
