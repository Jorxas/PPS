"""Media contracts use synthetic audio; real VAD/ASR evidence is a separate report."""
import hashlib
import json
import time
import wave
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from pps.api import create_app
from pps.audio import run, hash_file
from pps.media import CHUNK_BYTES, MediaService, public_analysis, probe
from pps.errors import PPSError
from conftest import tone_bytes, FakeManager

@pytest.fixture
def media_client(cfg):
    app=create_app(cfg,FakeManager(),start_jobs=False)
    with TestClient(app) as client: yield client

def begin(client,data,name='test.wav',voice_id=None,**changes):
    body={'filename':name,'size_bytes':len(data),'fingerprint':hashlib.sha256(data).hexdigest(),
        'voice_id':voice_id,'name':'Synthetic media contract','language':'fr','authorized':True,
        'declaration':'Signal synthétique de test, aucune voix humaine.',**changes}
    return client.post('/v1/media/uploads',json=body)

def upload(client,data,name='test.wav',voice_id=None):
    r=begin(client,data,name,voice_id); assert r.status_code==201,r.text
    m=r.json()
    for offset in range(0,len(data),CHUNK_BYTES):
        r=client.put('/v1/media/uploads/'+m['id']+'?offset='+str(offset),content=data[offset:offset+CHUNK_BYTES])
        assert r.status_code==200,r.text
    r=client.post('/v1/media/uploads/'+m['id']+'/complete'); assert r.status_code==200,r.text
    return r.json()

class FixtureWorker:
    """Deterministic orchestration fixture, never claimed as real VAD or human speech."""
    def __init__(self,cfg,cancelled,log): self.peak_rss=12345; self.cpu_seconds=.1
    def call(self,action,path,manual=False):
        with wave.open(str(path)) as f: duration=f.getnframes()/f.getframerate()
        if action=='transcribe': return {'seconds':.01,'result':{'text':'Signal synthétique.','language':'fr','verified':False,'status':'requires_user_verification'}}
        m={'score':75,'speech_seconds':min(5,duration),'speaker_status':'suspected_multiple',
            'embedding':[1.0,0.0],'clone_fidelity_measured':False,'target_speaker_verified':False}
        return {'seconds':.01,'result':{'duration':duration,'speech_seconds':min(5,duration),'candidates':
            [{'start':0,'end':duration if manual else min(8,duration),'metrics':m}] if duration>=3 else []}}
    def close(self): pass

def analyze(client,m,monkeypatch):
    monkeypatch.setattr('pps.media.CPUWorker',FixtureWorker)
    a=client.post('/v1/media/'+m['id']+'/analyses',json={'track_index':m['probe']['audio_tracks'][0]['index']}).json()
    client.app.state.media.process(a['id'])
    return client.get('/v1/media/analyses/'+a['id']).json()

@pytest.mark.parametrize('authorized,declaration',[(False,'test'),(True,''),(False,'')])
def test_media_requires_source_authorization(media_client,authorized,declaration):
    r=begin(media_client,b'test',authorized=authorized,declaration=declaration)
    assert r.status_code==422 and r.json()['error']['code']=='AUTHORIZATION_REQUIRED'
    assert media_client.app.state.store.list('media')==[]

def test_upload_resume_offsets_hash_and_wrong_block_rollback(media_client):
    data=tone_bytes();m=begin(media_client,data).json();url='/v1/media/uploads/'+m['id']
    assert media_client.put(url+'?offset=0',content=data[:100]).status_code==200
    assert media_client.put(url+'?offset=0',content=data[100:]).status_code==409
    assert media_client.put(url+'?offset=100',content=b'x'*(CHUNK_BYTES+1)).status_code==413
    assert media_client.get(url).json()['received_bytes']==100
    assert media_client.post(url+'/complete').status_code==409
    assert media_client.put(url+'?offset=100',content=data[100:]).status_code==200
    r=media_client.post(url+'/complete'); assert r.status_code==200,r.text
    assert r.json()['sha256']==hashlib.sha256(data).hexdigest()
    assert 'path' not in r.json()
    assert media_client.put(url+'?offset='+str(len(data)),content=b'x').status_code==409

def test_invalid_source_retained(media_client):
    data=b'not media';m=begin(media_client,data).json();url='/v1/media/uploads/'+m['id']
    media_client.put(url+'?offset=0',content=data)
    r=media_client.post(url+'/complete'); assert r.status_code==422
    stored=media_client.app.state.store.get('media',m['id'])
    assert stored['status']=='invalid' and Path(stored['path']).read_bytes()==data

def test_source_larger_than_old_reference_upload_limit(media_client,tmp_path):
    path=tmp_path/'large-source.wav'
    run(['-f','lavfi','-i','anullsrc=r=16000:cl=mono','-t','600','-c:a','pcm_s16le',path])
    assert path.stat().st_size>media_client.app.state.cfg['service']['max_reference_bytes']
    m=upload(media_client,path.read_bytes(),path.name)
    assert m['status']=='ready' and m['probe']['duration_seconds']==600
    assert m['sha256']==hash_file(path)

def test_multiple_sources_same_voice_and_tracks_not_speakers(media_client):
    a=upload(media_client,tone_bytes(),'first.wav')
    b=upload(media_client,tone_bytes(),'second.wav',a['voice_id'])
    assert a['voice_id']==b['voice_id']
    assert media_client.get('/v1/voices/'+a['voice_id']).json()['references']==[]
    assert a['probe']['track_speaker_identity']=='unverified'
    assert media_client.post('/v1/media/'+a['id']+'/analyses',json={'track_index':999}).status_code==422

@pytest.mark.parametrize('extension,codec',[
    ('wav','pcm_s16le'),('mp3','libmp3lame'),('m4a','aac'),('aac','aac'),('flac','flac'),('ogg','libvorbis'),
    ('mp4','aac'),('mkv','flac'),('mov','aac'),('webm','libopus'),('avi','pcm_s16le')])
def test_ffprobe_supported_audio_video_containers(cfg,tmp_path,extension,codec):
    path=tmp_path/('media.'+extension)
    video=extension in ('mp4','mkv','mov','webm','avi')
    args=['-f','lavfi','-i','sine=frequency=220:sample_rate=24000:duration=3']
    if video:
        args+=['-f','lavfi','-i','color=c=black:s=16x16:r=1:d=3','-c:v','libvpx-vp9' if extension=='webm' else 'mpeg4']
    run(args+['-c:a',codec,'-threads','2',path])
    p=probe(cfg,path)
    assert len(p['audio_tracks'])==1 and p['duration_seconds']>=2.9

def test_multiple_audio_tracks_require_valid_explicit_index(media_client,tmp_path):
    path=tmp_path/'tracks.mkv'
    run(['-f','lavfi','-i','sine=frequency=220:duration=3','-f','lavfi','-i','sine=frequency=440:duration=3',
        '-map','0:a','-map','1:a','-c:a','flac',path])
    m=upload(media_client,path.read_bytes(),'tracks.mkv')
    assert len(m['probe']['audio_tracks'])==2
    assert media_client.post('/v1/media/'+m['id']+'/analyses',json={}).status_code==422

def test_audio_track_offset_keeps_video_clock(media_client,tmp_path):
    path=tmp_path/'delayed-audio.mkv'
    run(['-f','lavfi','-i','color=c=black:s=16x16:r=1:d=10','-itsoffset','4',
        '-f','lavfi','-i','sine=frequency=220:sample_rate=16000:duration=3',
        '-map','0:v','-map','1:a','-c:v','mpeg4','-c:a','pcm_s16le','-t','10',path])
    m=upload(media_client,path.read_bytes(),path.name)
    service=media_client.app.state.media;a=service.analyze(m['id'],m['probe']['audio_tracks'][0]['index'])
    dest=tmp_path/'clip.wav';service.extract(a,3,8,dest,lambda:False)
    with wave.open(str(dest)) as f:
        assert f.getnframes()/f.getframerate()==5
        first=f.readframes(16000);middle=f.readframes(3*16000);tail=f.readframes(16000)
    assert not any(first) and any(middle) and not any(tail)

def test_disk_guard_applies_actual_size_without_duration_cap(media_client,monkeypatch):
    from collections import namedtuple
    usage=namedtuple('usage','total used free')
    monkeypatch.setattr('pps.media.shutil.disk_usage',lambda _:usage(100,99,1))
    r=begin(media_client,b'x')
    assert r.status_code==409 and r.json()['error']['code']=='DISK_SPACE_LOW'
    assert media_client.app.state.store.list('media')==[]

def test_cancellation_resume_checkpoint_and_long_duration_contract(media_client,monkeypatch,tmp_path):
    path=tmp_path/'long.wav'
    run(['-f','lavfi','-i','anullsrc=r=16000:cl=mono','-t','125','-c:a','pcm_s16le',path])
    m=upload(media_client,path.read_bytes(),'long.wav')
    a=media_client.post('/v1/media/'+m['id']+'/analyses',json={'track_index':0}).json()
    assert media_client.post('/v1/media/analyses/'+a['id']+'/cancel').json()['status']=='cancelled'
    assert media_client.post('/v1/media/analyses/'+a['id']+'/resume').json()['status']=='queued'
    service=media_client.app.state.media
    class CancellingWorker(FixtureWorker):
        def call(self,*args,**kwargs):
            result=super().call(*args,**kwargs);service.cancel(a['id']);return result
    monkeypatch.setattr('pps.media.CPUWorker',CancellingWorker)
    service.process(a['id'])
    stopped=service.store.get('media_analyses',a['id'])
    assert stopped['processed_seconds']==60 and stopped['status']=='cancelled' and stopped['window_count']==1
    service.resume(a['id']); monkeypatch.setattr('pps.media.CPUWorker',FixtureWorker);service.process(a['id'])
    result=service.store.get('media_analyses',a['id'])
    assert result['status']=='completed' and result['processed_seconds']==125 and result['window_count']==3
    assert len(service.store.list('media_windows'))==3 and result['metrics']['gpu_used'] is False
    assert media_client.get('/v1/media/options').json()['source_duration_limit_seconds'] is None

def test_manual_bounds_and_reference_gates_per_engine(media_client,monkeypatch):
    m=upload(media_client,tone_bytes());a=analyze(media_client,m,monkeypatch)
    assert a['status']=='completed' and 'embedding' not in a['candidates'][0]['metrics']
    cid=a['candidates'][0]['id'];base='/v1/media/analyses/'+a['id']+'/candidates/'+cid
    assert media_client.get(base+'/audio').status_code==200
    assert media_client.post(base+'/reference',json={'transcription':'Test','transcription_verified':True}).status_code==422
    assert media_client.post(base+'/reference',json={'speaker_confirmed':True,'transcription_verified':True}).status_code==422
    for start,end in [(0,31),(2,9),(2,3),(-1,4)]:
        assert media_client.post('/v1/media/analyses/'+a['id']+'/candidates',json={'start_seconds':start,'end_seconds':end}).status_code==422
    asr=media_client.post(base+'/transcribe').json()
    assert asr['transcription']['verified'] is False
    r=media_client.post(base+'/reference',json={'speaker_confirmed':True,'transcription':'Test','transcription_verified':False})
    assert r.status_code==201,r.text
    v=r.json();ref=v['references'][0]
    assert ref['provenance']['source_sha256']==m['sha256'] and ref['speaker_confirmation']['confirmed']
    assert ref['transcription_verified'] is False
    for engine,status in [('chatterbox',202),('cosyvoice',422)]:
        request=media_client.post('/v1/syntheses',json={'voice_id':v['id'],'engine':engine,'text':'Bonjour.'})
        assert request.status_code==status,request.text
    edit=media_client.put('/v1/voices/'+v['id']+'/references/'+ref['id']+'/transcription',json={'transcription':'Test exact','verified':True})
    assert edit.status_code==200
    assert media_client.post('/v1/syntheses',json={'voice_id':v['id'],'engine':'cosyvoice','text':'Bonjour.'}).status_code==202
    again=media_client.post(base+'/reference',json={'speaker_confirmed':True})
    assert len(again.json()['references'])==1

def test_engine_specific_refs_and_comparison_atomicity(media_client,monkeypatch):
    m=upload(media_client,tone_bytes());a=analyze(media_client,m,monkeypatch)
    first=a['candidates'][0]['id']
    second=media_client.post('/v1/media/analyses/'+a['id']+'/candidates',json={'start_seconds':1,'end_seconds':7}).json()['id']
    refs=[]
    for cid in (first,second):
        v=media_client.post('/v1/media/analyses/'+a['id']+'/candidates/'+cid+'/reference',json={
            'speaker_confirmed':True,'transcription':'Signal de test','transcription_verified':True}).json()
        refs.append(v['references'][-1]['id'])
    assert len(v['references'])==2
    for engine,ref in zip(('chatterbox','cosyvoice'),refs):
        r=media_client.put('/v1/voices/'+v['id']+'/engine-reference',json={'engine':engine,'reference_id':ref})
        assert r.status_code==200
    result=media_client.post('/v1/comparisons',json={'voice_id':v['id'],'text':'Bonjour.'}).json()
    jobs=[media_client.get('/v1/jobs/'+j).json() for j in result['job_ids']]
    assert [j['request']['reference_id'] for j in jobs]==refs
    assert media_client.get('/v1/comparisons/'+result['comparison_id']).json()['same_reference'] is False
    n=len(media_client.app.state.store.list('jobs'))
    r=media_client.post('/v1/comparisons',json={'voice_id':v['id'],'text':'Bonjour.','engine_reference_ids':{'cosyvoice':'unknown'}})
    assert r.status_code==404 and len(media_client.app.state.store.list('jobs'))==n
    trial=media_client.post('/v1/reference-trials',json={'voice_id':v['id'],'text':'Bonjour.','reference_ids':refs,'engine':'both'},headers={'Idempotency-Key':'trial'}).json()
    assert len(trial['job_ids'])==4
    repeated=media_client.post('/v1/reference-trials',json={'voice_id':v['id'],'text':'Bonjour.','reference_ids':refs,'engine':'both'},headers={'Idempotency-Key':'trial'}).json()
    assert repeated==trial
    record=media_client.get('/v1/reference-trials/'+trial['trial_id']).json()
    assert record['winner'] is None and len(record['jobs'])==4
    assert [j['request']['reference_id'] for j in record['jobs']]==[refs[0],refs[0],refs[1],refs[1]]
    count=len(media_client.app.state.store.list('jobs'))
    for choices in ([refs[0],'unknown'],[refs[0],refs[0]]):
        rejected=media_client.post('/v1/reference-trials',json={'voice_id':v['id'],'text':'Bonjour.','reference_ids':choices})
        assert rejected.status_code in (404,422)
        assert len(media_client.app.state.store.list('jobs'))==count
    cancelled=media_client.post('/v1/reference-trials/'+trial['trial_id']+'/cancel').json()
    assert all(j['status']=='cancelled' for j in cancelled['jobs'])

def test_private_worker_measures_real_windows_process_and_closes(cfg,tmp_path):
    from pps.config import ROOT
    if not (ROOT/'tools/media/silero_vad_v5.1.2.onnx').is_file():
        pytest.skip('Optionaler CPU-Integrationstest benötigt lokal installierte VAD/YAMNet-Modelle.')
    from pps.media import CPUWorker
    import psutil
    path=tmp_path/'silence.wav'
    run(['-f','lavfi','-i','anullsrc=r=16000:cl=mono','-t','3','-c:a','pcm_s16le',path])
    worker=CPUWorker(cfg,lambda:False,tmp_path/'worker.log')
    pid=worker.worker_pid
    result=worker.call('analyze',path)
    assert result['result']['candidates']==[]
    assert worker.peak_rss>100*1024**2
    assert result['result']['memory']['process']['pid']==pid
    worker.close()
    assert not psutil.pid_exists(pid)

def test_media_playlists_cannot_read_unrequested_local_files(media_client):
    data=b'#EXTM3U\n#EXTINF:10,\nfile:///C:/private.wav\n';m=begin(media_client,data).json()
    media_client.put('/v1/media/uploads/'+m['id']+'?offset=0',content=data)
    r=media_client.post('/v1/media/uploads/'+m['id']+'/complete')
    assert r.status_code==422 and r.json()['error']['code']=='PLAYLIST_UNSUPPORTED'

def test_restart_recovers_running_analysis_without_restarting_tts(media_client):
    m=upload(media_client,tone_bytes());service=media_client.app.state.media
    a=service.analyze(m['id'],0)
    service.store.update('media_analyses',a['id'],status='running',processed_seconds=4)
    service.start()
    try:
        record=service.store.get('media_analyses',a['id'])
        assert record['status']=='paused' and record['processed_seconds']==4
        assert media_client.app.state.jobs.manager.loads==[]
    finally: service.close()

def test_unbounded_source_is_not_loaded_as_reference(media_client,monkeypatch):
    m=upload(media_client,tone_bytes());s=media_client.app.state.media
    value=s.store.get('media',m['id']);value['probe']['duration_seconds']=3*3600;s.store.put('media',value)
    a=s.analyze(m['id'],0)
    assert a['total_seconds']==10800 and a['candidates']==[]
    assert media_client.get('/v1/voices/'+m['voice_id']).json()['references']==[]

def test_resume_refuses_mixed_analysis_revisions_and_keeps_old_results(media_client):
    m=upload(media_client,tone_bytes());s=media_client.app.state.media;a=s.analyze(m['id'],0)
    s.cancel(a['id']);s.store.update('media_analyses',a['id'],analysis_contract={'worker_sha256':'old'})
    r=media_client.post('/v1/media/analyses/'+a['id']+'/resume')
    assert r.status_code==409 and r.json()['error']['code']=='ANALYSIS_REVISION_CHANGED'
    fresh=media_client.post('/v1/media/'+m['id']+'/analyses',json={'track_index':0,'fresh':True}).json()
    assert fresh['id']!=a['id'] and s.store.get('media_analyses',a['id'])['status']=='cancelled'
