"""PPS-only adapter for audited MainClip TTS contracts (Drama STS excluded)."""
import base64
import hashlib
import json
import re
import time
import wave
import threading
from pathlib import Path
from .alignment import align, canonical_tokens, valid_bounds
from .audio import export, hash_file, pcm_metadata, run
from .errors import PPSError
from .review import effective_alignment, latest_review
from .storage import identity

BREAK = re.compile(r'<break\s+time\s*=\s*([\"\'])(\d+(?:\.\d+)?)(ms|s)\1\s*/>',re.I)
RENDER_LOCK = threading.Lock()


def parse_breaks(text):
    """Only MainClip's explicit self-closing break dialect; never forward tags."""
    pieces, previous = [], 0
    for match in BREAK.finditer(text):
        speech = text[previous:match.start()].strip()
        if speech:
            pieces.append({'text':speech})
        pause = float(match[2]) / (1000 if match[3].lower()=='ms' else 1)
        if pause>10:
            raise PPSError('UNSUPPORTED_CAPABILITY','Pause limitée à 10 secondes.',422)
        pieces.append({'silence_seconds':pause})
        previous = match.end()
    if text[previous:].strip():
        pieces.append({'text':text[previous:].strip()})
    if not pieces or any('<' in p.get('text','') or '>' in p.get('text','') for p in pieces):
        raise PPSError('UNSUPPORTED_CAPABILITY','Texte vide ou balise de contrôle non prise en charge.',422)
    return pieces


def cues(words,max_words=4,max_duration=1.6,max_characters=30):
    if max_words<1 or max_duration<=0 or max_characters<1:
        raise PPSError('INVALID_REQUEST','Options de cues invalides.',422)
    result, group = [], []
    def flush():
        if group:
            result.append({'text':' '.join(w['text'] for w in group),'start':group[0]['start'],
                           'end':group[-1]['end'],'words':list(group)})
            group.clear()
    for w in words:
        group.append(w)
        if len(group)>=max_words or group[-1]['end']-group[0]['start']>=max_duration or len(' '.join(x['text'] for x in group))>=max_characters or re.search(r'[.!?…]$',w['text']):
            flush()
    flush()
    return result


def character_alignment(words):
    """Word-span character representation with explicit legacy +40 ms compensation.

    No claim of character/phoneme precision. Direct word cues are preferred.
    """
    chars, starts, ends = [], [], []
    for i,w in enumerate(words):
        if w['end']-w['start']<=.04:
            raise PPSError('ALIGNMENT_FAILED','Mot trop court pour le supplément de 40 ms du lecteur legacy.',422)
        if i:
            chars.append(' ');starts.append(w['start']);ends.append(w['start'])
        for char in w['text']:
            chars.append(char);starts.append(w['start']);ends.append(w['end']-.04)
    return {'characters':chars,'character_start_times_seconds':starts,'character_end_times_seconds':ends,
            'synthesized':True,'temporal_granularity':'word','legacy_reader_end_padding_seconds':.04,
            'note':'Not phoneme timings. End compensation applies ONLY to MainClip alignmentToCues (+0.04 s).'}


def result_for_audio(store, audio_id, require_alignment=True, characters=False):
    audio = store.get('audio',audio_id)
    if hash_file(audio['wav'])!=audio['pcm']['sha256'] or hash_file(audio['mp3'])!=audio['mp3_sha256']:
        raise PPSError('AUDIO_INVALID','Intégrité du cache audio non vérifiée.',422)
    alignment = effective_alignment(audio,latest_review(store,audio_id))
    if require_alignment and not alignment['mainclip_compatible']:
        raise PPSError('ALIGNMENT_FAILED','Frontières acoustiques incertaines ; écoute/correction requise.',422,
                       {'audio_id':audio_id,'quality':alignment.get('quality'),'audio_preserved':True})
    words = alignment['words'] if alignment['mainclip_compatible'] else []
    model = audio['metrics'].get('environment',{}).get('model_revision')
    return {'audio_id':audio_id,'audio_base64':base64.b64encode(Path(audio['mp3']).read_bytes()).decode(),
            'audio_url':'/v1/audio/'+audio_id+'?format=mp3','duration_seconds':audio['mp3_decoded_pcm']['duration_seconds'],
            'engine':audio['engine'],'voice_id':audio['voice_id'],'reference_id':audio['reference_id'],
            'words':words,'cues':cues(words),'alignment':character_alignment(words) if characters and words else alignment,
            'model':model,'source':'pps-local','subtitleSource':alignment.get('timestamp_provenance','none'),
            'cached':audio['metrics'].get('synthesis_cache_hit',False),'performance':audio['metrics'],
            'timebase':'delivered_decoded_audio','quality_status':alignment['status']}


def create_plan(store,jobs,body,key=None):
    digest = hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with store.lock:
        with store.transaction() as db:
            old = store.remembered('mainclip_plans',key,digest,db)
            if old:
                return old
            pieces = []
            for i,segment in enumerate(body['segments']):
                if i and body['gap_seconds']:
                    pieces.append({'silence_seconds':body['gap_seconds'],'intent':'segment_gap'})
                for p in parse_breaks(segment['text']):
                    if 'text' in p:
                        req = {'engine':body['engine'],'voice_id':segment['voice_id'],'reference_id':segment.get('reference_id'),
                               'text':p['text'],'display_text':None,'language':body['language'],'seed':body['seed'],
                               'options':body['options'],'previous_text':segment.get('previous_text'),
                               'next_text':segment.get('next_text'),'cache_mode':body['cache_mode']}
                        req = jobs.validate(req,body['engine'],db)
                        pieces.append({'request':req,'segment_index':i,'voice_id':segment['voice_id']})
                    else:
                        pieces.append({**p,'intent':'explicit_break'})
            count = sum('request' in p for p in pieces)
            pending = sum(j['status'] not in ('completed','failed','cancelled') for j in store.list('jobs',db))
            if not count or count+pending>jobs.cfg['service']['max_queue']:
                raise PPSError('QUEUE_FULL','Nombre de segments supérieur à la capacité disponible.',429)
            if body['rate_multiplier']!=1 and body['options'].get('speed',1)!=1:
                raise PPSError('UNSUPPORTED_CAPABILITY','Vitesse native et débit commun ne peuvent pas se cumuler.',422)
            identifier = identity('plan')
            for p in pieces:
                if 'request' in p:
                    request = p.pop('request')
                    job = {'id':identity('job'),'request':request,'status':'queued','created_at':time.time(),
                           'comparison_id':None,'audio_id':None,'error':None,'metrics':{},'cancel_requested':False,
                           'attempts':0,'history':[{'status':'queued','at':time.time()}]}
                    store.put('jobs',job,db)
                    p['job_id']=job['id']
            record = {'id':identifier,'pieces':pieces,'request':body,'created_at':time.time(),'status':'queued'}
            store.put('mainclip_plans',record,db)
            store.remember('mainclip_plans',key,digest,record,db)
        jobs.wakeup.set()
        return record


def assemble_pcm(pieces, directory, rate=44100):
    """Use an integer decoded-sample clock; retain all source silences and boundaries."""
    directory = Path(directory);directory.mkdir(parents=True,exist_ok=True)
    target = directory/'assembled-original.wav'
    timeline, words, frame_count = [], [], 0
    with wave.open(str(target),'wb') as out:
        out.setparams((1,2,rate,0,'NONE','not compressed'))
        for i,p in enumerate(pieces):
            start = frame_count/rate
            if 'silence_seconds' in p:
                frames = round(p['silence_seconds']*rate)
                out.writeframes(b'\x00\x00'*frames)
            else:
                decoded = directory/('piece-'+str(i)+'.wav')
                run(['-i',p['wav'],'-ac','1','-ar',str(rate),'-c:a','pcm_s16le',decoded])
                with wave.open(str(decoded),'rb') as f:
                    frames = f.getnframes();out.writeframes(f.readframes(frames))
                for w in p.get('words',[]):
                    words.append({**w,'start':w['start']+start,'end':w['end']+start,
                                  'timeline_provenance':{'source_audio_id':p.get('audio_id'),'offset_samples':frame_count,'rate':rate}})
            frame_count += frames
            timeline.append({'start':start,'end':frame_count/rate,'sample_start':round(start*rate),
                             'sample_end':frame_count,'voice_id':p.get('voice_id'),'segment_index':p.get('segment_index'),
                             'source_audio_id':p.get('audio_id'),'intent':p.get('intent','speech')})
    return target, timeline, words


def render_plan(store,cfg,identifier):
    # Serialize CPU renders and protect an idempotent finalization against concurrent requests.
    with RENDER_LOCK:
        plan = store.get('mainclip_plans',identifier)
        if plan['status']=='cancelled':
            raise PPSError('CANCELLED','Plan annulé.',409)
        if plan.get('audio_id'):
            return result_for_audio(store,plan['audio_id'],plan['request']['require_alignment'])
        resolved = []
        for p in plan['pieces']:
            if 'job_id' not in p:
                resolved.append(p);continue
            job = store.get('jobs',p['job_id'])
            if job['status']!='completed':
                raise PPSError('PLAN_NOT_READY','Segment non terminé.',409,{'job_id':job['id'],'status':job['status']})
            audio = store.get('audio',job['audio_id'])
            alignment = effective_alignment(audio,latest_review(store,audio['id']))
            if hash_file(audio['wav'])!=audio['pcm']['sha256']:
                raise PPSError('AUDIO_INVALID','Source de segment modifiée.',422)
            if plan['request']['require_alignment'] and not alignment['mainclip_compatible']:
                raise PPSError('ALIGNMENT_FAILED','Segment incertain ; rendu avec timestamps refusé.',422,{'audio_id':audio['id']})
            resolved.append({**p,'wav':audio['wav'],'audio_id':audio['id'],
                             'words':alignment['words'] if alignment['mainclip_compatible'] else []})
        directory = Path(cfg['paths']['data'])/'mainclip'/identifier
        raw,timeline,words = assemble_pcm(resolved,directory)
        speed = plan['request']['rate_multiplier']
        if speed!=1:
            transformed = directory/'atempo.wav'
            run(['-i',raw,'-af','atempo='+str(speed),'-c:a','pcm_s16le',transformed])
            raw = transformed
        audio = export(raw,directory)
        if store.get('mainclip_plans',identifier)['status']=='cancelled':
            raise PPSError('CANCELLED','Plan annulé pendant le rendu ; aucun résultat livré.',409)
        text = ' '.join(p['text'] for p in plan['request']['segments'])
        text = ' '.join(p['text'] for p in parse_breaks(text) if 'text' in p)
        if speed!=1:
            # NEVER divide old timestamps by speed. Realign the measured, final delivered WAV.
            aligned = align(audio['wav'],text,plan['request']['language'],audio['pcm']['duration_seconds'],cfg['alignment'],directory)
            aligned['transformation'] = {'method':'ffmpeg_atempo','rate_multiplier':speed,
                'original_duration_seconds':pcm_metadata(directory/'assembled-original.wav')['duration_seconds'],
                'final_duration_seconds':audio['pcm']['duration_seconds'],'prior_alignment_invalidated':True}
        else:
            accepted = bool(words) and valid_bounds(words,audio['pcm']['duration_seconds']) and canonical_tokens(text)==[t for w in words for t in canonical_tokens(w['text'])]
            aligned = {'words':words,'status':'source_reviewed_composition' if accepted else 'not_available',
                       'timestamp_provenance':'integer_sample_clock_source_word_offsets','mainclip_compatible':accepted,
                       'script_coverage':1 if accepted else 0,'interpolated_word_count':0,
                       'audio_sha256':audio['pcm']['sha256'],'exact':False,'quality':'source_reviews_preserved',
                       'timestamps_valid':valid_bounds(words,audio['pcm']['duration_seconds'])}
        identifier_audio = identity('audio')
        with store.transaction() as db:
            current = store.get('mainclip_plans',identifier,db)
            if current['status']=='cancelled':
                raise PPSError('CANCELLED','Plan annulé ; aucun résultat livré.',409)
            store.put('audio',{'id':identifier_audio,'job_id':None,'engine':plan['request']['engine'],
                'voice_id':plan['request']['segments'][0]['voice_id'],'reference_id':'per_segment',
                'reference_hash':None,'text':text,'alignment':aligned,'metrics':{},'source_timeline':timeline,
                'timeline_timebase':'before_atempo' if speed!=1 else 'delivered_audio','rate_multiplier':speed,**audio},db)
            current.update(audio_id=identifier_audio,status='assembled',
                           render_gate='requires_new_alignment_review' if speed!=1 else 'source_review_gate')
            store.put('mainclip_plans',current,db)
        return result_for_audio(store,identifier_audio,plan['request']['require_alignment'])
