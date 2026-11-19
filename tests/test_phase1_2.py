"""Software contracts use explicit synthetic fixtures; real files are read-only."""
import copy
import json
import math
import struct
import subprocess
import threading
import time
import wave
from pathlib import Path
import pytest
from pps.alignment import evaluate, canonical_tokens, boundary_evidence, valid_bounds
from pps.audio import hash_file, pcm_metadata
from pps.cache import cache_key
from pps.compatibility import parse_breaks, assemble_pcm, character_alignment, cues, result_for_audio
from pps.config import ROOT
from pps.errors import PPSError
from pps.signal import activity, rail_metrics
from pps.storage import Store
from conftest import import_tone, tone_bytes, wait_job


def req(voice,**overrides):
    return {'voice_id':voice['id'],'engine':'chatterbox','text':'Bonjour.',**overrides}


def completed(client,voice,**overrides):
    response=client.post('/v1/syntheses',json=req(voice,**overrides))
    assert response.status_code==202,response.text
    job=wait_job(client,response.json()['job_id'])
    assert job['status']=='completed',job
    return job


def seed_alignment(client,identifier,text='Bonjour.'):
    store=client.app.state.store
    a=store.get('audio',identifier)
    alignment=evaluate(text,[{'word':text,'start':.05,'end':.35}],a['pcm']['duration_seconds'])
    store.update('audio',identifier,alignment=alignment)
    return {'audio_sha256':a['pcm']['sha256'],'alignment_revision':1,'ratings':{},'corrections':[],
            'acoustically_confirmed':False,'notes':'CONTRACT FIXTURE ONLY; not an actual user assessment'}


@pytest.mark.parametrize('apostrophe',["'",'’','ʼ'])
def test_french_apostrophes_and_punctuation(apostrophe):
    assert canonical_tokens('L'+apostrophe+'été, c'+apostrophe+'est français !')==["l'été","c'est",'français']
    result=evaluate("L'été",[{'word':'l’été','start':0,'end':.1}],.1)
    assert result['script_coverage']==1


def test_homophone_is_explicit_not_masked():
    a=evaluate('locale',[{'word':'local','start':0,'end':.2}],.2)
    assert a['script_coverage']==0 and a['orthographic_homophone_candidates']
    assert not a['mainclip_compatible']
    b=evaluate('locale',[{'word':'vocale','start':0,'end':.2}],.2)
    assert not b['orthographic_homophone_candidates']


def test_split_french_contraction_preserves_source_bounds():
    source=[{'word':'L’','start':.1,'end':.2},{'word':'été','start':.2,'end':.4}]
    a=evaluate("L'été",source,.5)
    assert a['script_coverage']==1 and len(a['words'])==1
    w=a['words'][0]
    assert w['start']==.1 and w['end']==.4 and w['method']=='French_explicit_apostrophe_token_merge'
    assert w['provenance']['source_tokens']==source and a['raw_words']==source
    b=evaluate("L'été",[{'word':'L','start':.1,'end':.2},{'word':'été','start':.2,'end':.4}],.5)
    assert b['script_coverage']<1  # No invented apostrophe masks an ASR mismatch.


@pytest.mark.parametrize('start,end',[(float('nan'),.2),(0,float('inf')),(False,.2),(0,0),(.2,.1),(-.1,.2),(0,.2011)])
def test_invalid_uncertain_timestamps_refused(start,end):
    assert not valid_bounds([{'word':'a','start':start,'end':end}],.2)


def test_beginning_and_final_word_exact_audio_bounds():
    a=evaluate('Bonjour fin',[{'word':'Bonjour','start':0,'end':.2},{'word':'fin','start':.3,'end':.5}],.5)
    assert a['timestamps_valid'] and not a['exact'] and not a['mainclip_compatible']
    assert all(set(('text','start','end','method','provenance','confidence_status','uncertainty'))<=w.keys() for w in a['words'])


def test_initial_silence_and_click_diagnostics(tmp_path):
    wav=tmp_path/'signal.wav'
    data=bytearray(tone_bytes(1.4))
    # Valid WAV fixture: 1s silence, one isolated click, then 0.4s tone.
    with wave.open(str(wav),'wb') as out:
        out.setparams((1,2,24000,0,'NONE','not compressed'))
        silent=bytearray(48000);silent[4800:4802]=struct.pack('<h',30000)
        out.writeframes(silent)
        with wave.open(__import__('io').BytesIO(tone_bytes(.4)),'rb') as f: out.writeframes(f.readframes(9600))
    evidence=activity(wav,threshold_db=-40)
    assert evidence['sustained_onset_seconds']==pytest.approx(1,abs=.02)
    assert evidence['analysis_crop_offset_seconds']==pytest.approx(.88,abs=.02)
    a=evaluate('Bonjour',[{'word':'Bonjour','start':0,'end':1.3}],1.4,'dtw')
    boundary_evidence(a,evidence)
    w=a['review_alignment']['words'][0]
    assert 'initial_boundary_precedes_sustained_energy' in w['uncertainty']
    assert not a['review_alignment']['mainclip_compatible']


def test_rail_counts_distinguish_near_fullscale(tmp_path):
    wav=tmp_path/'rails.wav'
    with wave.open(str(wav),'wb') as out:
        out.setparams((1,2,24000,0,'NONE','not compressed'))
        out.writeframes(struct.pack('<7h',0,32760,32766,32767,32767,-32768,0))
    a=rail_metrics(wav)
    assert a['near_rail_sample_count']==5 and a['rail_sample_count']==3
    assert a['clipping_evidence'] and a['longest_flat_rail_run_samples']==2


def test_incomplete_transcript_never_passes_gate(client,voice):
    job=completed(client,voice,text='Bonjour au monde.')
    seed_alignment(client,job['audio_id'])
    assert client.get('/v1/audio/'+job['audio_id']+'/mainclip').status_code==422
    audio_only=client.get('/v1/audio/'+job['audio_id']+'/mainclip?require_alignment=false').json()
    assert audio_only['audio_base64'] and not audio_only['words'] and audio_only['cues']==[]


def test_review_append_only_persistence_and_source_values(client,voice):
    job=completed(client,voice)
    body=seed_alignment(client,job['audio_id'])
    body['corrections']=[{'word_index':0,'start':.07,'end':.34}]
    body['ratings']={'naturalness':3}
    uri='/v1/audio/'+job['audio_id']+'/review'
    assert client.post(uri,json=body).status_code==201
    a=client.get(uri).json()
    assert a['effective_alignment']['words'][0]['start']==.07
    assert a['effective_alignment']['words'][0]['automatic']['start']==.05
    assert not a['effective_alignment']['mainclip_compatible']
    body['acoustically_confirmed']=True
    assert client.post(uri,json=body).status_code==201
    assert len(Store(client.app.state.store.path).list('reviews'))==2
    assert client.get(uri).json()['effective_alignment']['mainclip_compatible']
    automatic=client.get('/v1/audio/'+job['audio_id']+'/alignment').json()
    assert automatic['words'][0]['start']==.05 and not automatic['mainclip_compatible']


@pytest.mark.parametrize('changes',[{'ratings':{'naturalness':6}},{'ratings':{'fidelity':True}},
    {'corrections':[{'word_index':3,'start':.1,'end':.2}]},
    {'corrections':[{'word_index':0,'start':.3,'end':.2}]},
    {'corrections':[{'word_index':0,'start':.1,'end':.5}]},
    {'alignment_revision':2},{'audio_sha256':'0'*64}])
def test_bad_review_and_stale_audio_refused(client,voice,changes):
    job=completed(client,voice)
    body=seed_alignment(client,job['audio_id'])
    result=client.post('/v1/audio/'+job['audio_id']+'/review',json={**body,**changes})
    assert result.status_code in (409,422)
    assert not client.app.state.store.list('reviews')


def test_synthesis_cache_reuse_and_refresh(client,voice):
    calls=[]
    manager=client.app.state.jobs.manager
    synth=manager.synthesize
    manager.synthesize=lambda *args:(calls.append(True),synth(*args))[1]
    a=completed(client,voice)
    b=completed(client,voice)
    assert a['audio_id']==b['audio_id'] and len(calls)==1
    assert b['metrics']['synthesis_cache_hit'] and b['metrics']['synthesis_seconds']==0
    c=completed(client,voice,cache_mode='refresh')
    assert c['audio_id']!=a['audio_id'] and len(calls)==2
    completed(client,voice,cache_mode='bypass')
    assert len(calls)==3
    assert completed(client,voice)['audio_id']==c['audio_id']


def test_corrupt_cache_not_reused(client,voice):
    a=completed(client,voice)
    audio=client.app.state.store.get('audio',a['audio_id'])
    Path(audio['mp3']).write_bytes(b'corrupt fixture')
    b=completed(client,voice)
    assert b['audio_id']!=a['audio_id'] and not b['metrics']['synthesis_cache_hit']


def test_effective_default_settings_share_cache(client,voice):
    a=completed(client,voice)
    b=completed(client,voice,options={'exaggeration':.5,'cfg_weight':.5,'temperature':.8})
    assert a['audio_id']==b['audio_id'] and b['metrics']['synthesis_cache_hit']


def test_cache_only_never_starts_model_on_miss_or_hit(client,voice):
    a=completed(client,voice)
    manager=client.app.state.jobs.manager
    manager.ensure=lambda *args:pytest.fail('Cache-only must never admit GPU model')
    assert completed(client,voice,cache_mode='only')['audio_id']==a['audio_id']
    r=client.post('/v1/syntheses',json=req(voice,text='Absent.',cache_mode='only'))
    job=wait_job(client,r.json()['job_id'])
    assert job['status']=='failed' and job['error']['code']=='CACHE_MISS'


def test_queued_job_and_ab_survive_api_restart(cfg):
    from fastapi.testclient import TestClient
    from pps.api import create_app
    from conftest import FakeManager
    first=create_app(cfg,manager=FakeManager(),start_jobs=False)
    with TestClient(first) as c:
        v=import_tone(c).json()
        plan=c.post('/v1/comparisons',json={'voice_id':v['id'],'text':'Bonjour.'}).json()
        assert all(c.get('/v1/jobs/'+j).json()['status']=='queued' for j in plan['job_ids'])
    second=create_app(cfg,manager=FakeManager())
    with TestClient(second) as c:
        assert all(wait_job(c,j)['status']=='completed' for j in plan['job_ids'])
        assert c.get('/v1/comparisons/'+plan['comparison_id']).json()['status']=='completed'
        assert len(c.get('/v1/comparisons').json()['comparisons'])==1


def test_reviewed_segment_composition_and_refusal(client,voice):
    payload={'engine':'chatterbox','mode':'segmented','segments':[{'voice_id':voice['id'],'text':'Bonjour.'},
             {'voice_id':voice['id'],'text':'Bonjour.'}],'require_alignment':True}
    plan=client.post('/v1/mainclip/plans',json=payload).json()
    ids=[wait_job(client,p['job_id'])['audio_id'] for p in plan['pieces'] if 'job_id' in p]
    uri='/v1/mainclip/plans/'+plan['id']+'/render'
    assert client.post(uri).status_code==422
    for audio_id in set(ids):
        review=seed_alignment(client,audio_id)
        review['acoustically_confirmed']=True
        assert client.post('/v1/audio/'+audio_id+'/review',json=review).status_code==201
    result=client.post(uri)
    assert result.status_code==200,result.text
    data=result.json()
    assert len(data['words'])==2 and data['words'][1]['start']==pytest.approx(.51,abs=1/44100)
    assert data['quality_status']=='source_reviewed_composition'
    assert data['duration_seconds']==pytest.approx(.86,abs=1/44100)


@pytest.mark.parametrize('field,value',[('engine','cosyvoice'),('voice_id','different'),('voice_version',2),
    ('reference_hash','new'),('reference_id','other'),('seed',12),('options',{'exaggeration':.8}),
    ('previous_text','changed'),('next_text','changed'),('display_text','changed')])
def test_cache_separation(cfg,field,value):
    request={'engine':'chatterbox','voice_id':'v','voice_version':1,'reference_id':'r','reference_hash':'h',
             'text':'Bonjour.','language':'fr','seed':1986,'options':{}}
    assert cache_key(request,cfg)!=cache_key({**request,field:value},cfg)


def test_cache_model_and_source_revisions(cfg,tmp_path):
    model=tmp_path/'isolated-model';model.mkdir()
    cfg['chatterbox']['model']=str(model)
    reports=Path(cfg['root'])/'reports';reports.mkdir()
    source=reports/'sources.lock.json'
    source.write_text(json.dumps({'chatterbox':{'commit':'source1'}}))
    manifest=model/'manifest.json';manifest.write_text(json.dumps({'revision':'weights1'}))
    request={'engine':'chatterbox','options':{}}
    first=cache_key(request,cfg)
    manifest.write_text(json.dumps({'revision':'weights2'}))
    second=cache_key(request,cfg)
    assert first!=second
    source.write_text(json.dumps({'chatterbox':{'commit':'source2'}}))
    assert second!=cache_key(request,cfg)


def test_character_reader_short_word_refused():
    with pytest.raises(PPSError):character_alignment([{'text':'à','start':.1,'end':.12}])


def test_hot_model_revision_change_unloads_before_new_admission(cfg,monkeypatch):
    from pps.manager import EngineManager
    from test_manager import LiveProcess
    manager=EngineManager(cfg);manager.engine='chatterbox';manager.process=LiveProcess()
    manager.loaded={'model_revision':'old','source_revision':None}
    events=[]
    monkeypatch.setattr('pps.manager.status',lambda *args:{'model_revision':'new'})
    def unload():
        events.append('unload');manager.process=None;manager.engine=None
    def guard(*args):
        events.append('guard');raise PPSError('RESOURCE_BUSY','fixture insufficient RAM',503)
    monkeypatch.setattr(manager,'unload',unload);monkeypatch.setattr('pps.manager.guard',guard)
    with pytest.raises(PPSError):manager.ensure('chatterbox',lambda:False)
    assert events[0]=='unload' and events[-1]=='guard'


def test_reference_player_and_version_conflict(client,voice):
    ref=voice['active_reference_id']
    assert client.get('/v1/voices/'+voice['id']+'/references/'+ref+'/audio').content[:4]==b'RIFF'
    response=client.post('/v1/syntheses',json=req(voice,voice_version=2))
    assert response.status_code==409


def test_no_double_generation_and_engine_switch(client,voice):
    state={'active':0,'max':0,'calls':[]}
    manager=client.app.state.jobs.manager
    original=manager.synthesize
    def synth(request,*args):
        state['active']+=1;state['max']=max(state['max'],state['active'])
        state['calls'].append(request['engine'])
        try:return original(request,*args)
        finally:state['active']-=1
    manager.synthesize=synth
    ids=[client.post('/v1/syntheses',json=req(voice,engine=e,text='Texte '+str(i))).json()['job_id']
         for i,e in enumerate(('chatterbox','cosyvoice','chatterbox','cosyvoice'))]
    assert all(wait_job(client,i)['status']=='completed' for i in ids)
    assert state['max']==1 and state['calls']==['chatterbox','cosyvoice','chatterbox','cosyvoice']


def test_breaks_never_reach_engine(client,voice):
    pieces=parse_breaks('Un. <break time="0.42s" /> Deux. <break time="520ms"/> Trois.')
    assert [p.get('silence_seconds') for p in pieces if 'silence_seconds' in p]==[.42,.52]
    for bad in ('<break time="bad"/>','<break time="12s"/>','Bonjour <script>x</script>'):
        with pytest.raises(PPSError):parse_breaks(bad)
    assert client.post('/v1/syntheses',json=req(voice,text='Un <break time="0.4s"/> deux')).status_code==422
    payload={'engine':'chatterbox','segments':[{'voice_id':voice['id'],'text':'Un. <break time="0.42s"/> Deux.'}],
             'require_alignment':False}
    r=client.post('/v1/mainclip/plans',json=payload,headers={'Idempotency-Key':'plan-fixture'})
    assert r.status_code==202,r.text
    plan=r.json()
    assert len(plan['pieces'])==3
    for p in plan['pieces']:
        if 'job_id' in p:
            j=wait_job(client,p['job_id']);assert '<' not in j['request']['text']
    assert client.post('/v1/mainclip/plans',json=payload,headers={'Idempotency-Key':'plan-fixture'}).json()['id']==plan['id']
    assert client.post('/v1/mainclip/plans',json={**payload,'gap_seconds':.2},headers={'Idempotency-Key':'plan-fixture'}).status_code==409
    out=client.post('/v1/mainclip/plans/'+plan['id']+'/render')
    assert out.status_code==200,out.text
    assert out.json()['duration_seconds']==pytest.approx(.4+.42+.4,abs=1/44100)
    assert client.post('/v1/mainclip/plans/'+plan['id']+'/render').json()['audio_id']==out.json()['audio_id']


def test_dialogue_distinct_voices_timeline(client,voice):
    second=import_tone(client).json()
    payload={'mode':'dialogue','engine':'chatterbox','segments':[{'voice_id':voice['id'],'text':'Bonjour.'},
           {'voice_id':second['id'],'text':'Bonsoir.'}],'require_alignment':False,'gap_seconds':.06}
    plan=client.post('/v1/mainclip/plans',json=payload).json()
    for p in plan['pieces']:
        if 'job_id' in p:wait_job(client,p['job_id'])
    out=client.post('/v1/mainclip/plans/'+plan['id']+'/render').json()
    audio=client.app.state.store.get('audio',out['audio_id'])
    timeline=audio['source_timeline']
    assert timeline[0]['voice_id']==voice['id'] and timeline[-1]['voice_id']==second['id']
    assert timeline[1]['intent']=='segment_gap'
    assert audio['pcm']['duration_seconds']==pytest.approx(.86,abs=1/44100)


def test_atempo_uses_new_final_alignment_not_scaled_words(client,voice,monkeypatch):
    observed=[]
    def align(path,text,language,duration,cfg,directory):
        observed.append({'path':str(path),'hash':hash_file(path),'duration':duration})
        return evaluate(text,[],duration,'test_final_audio_alignment_callback')
    monkeypatch.setattr('pps.compatibility.align',align)
    payload={'engine':'chatterbox','segments':[{'voice_id':voice['id'],'text':'Bonjour.'}],
             'rate_multiplier':1.12,'require_alignment':False}
    plan=client.post('/v1/mainclip/plans',json=payload).json()
    wait_job(client,plan['pieces'][0]['job_id'])
    result=client.post('/v1/mainclip/plans/'+plan['id']+'/render')
    assert result.status_code==200,result.text
    audio=client.app.state.store.get('audio',result.json()['audio_id'])
    assert observed[0]['hash']==audio['pcm']['sha256']
    assert audio['pcm']['duration_seconds']<.4
    assert audio['alignment']['transformation']['prior_alignment_invalidated']
    assert not audio['alignment']['mainclip_compatible']
    assert client.get('/v1/audio/'+audio['id']+'/mainclip').status_code==422


def test_plan_invalid_atomicity_cancel_and_no_sts(client,voice):
    base={'engine':'chatterbox','segments':[{'voice_id':voice['id'],'text':'Bonjour.'}]}
    before=len(client.app.state.store.list('jobs'))
    invalid={**base,'segments':base['segments']+[{'voice_id':'absent','text':'x'}]}
    assert client.post('/v1/mainclip/plans',json=invalid).status_code==404
    assert len(client.app.state.store.list('jobs'))==before
    assert client.post('/v1/mainclip/plans',json={**base,'mode':'speech_to_speech'}).status_code==422
    assert client.post('/v1/mainclip/plans',json={**base,'engine':'cosyvoice','options':{'speed':1.2},'rate_multiplier':1.12}).status_code==422
    manager=client.app.state.jobs.manager;manager.delay=.6
    plan=client.post('/v1/mainclip/plans',json=base).json()
    assert client.post('/v1/mainclip/plans/'+plan['id']+'/cancel').status_code==200
    assert client.post('/v1/mainclip/plans/'+plan['id']+'/render').status_code==409


def test_mainclip_character_roundtrip_no_40ms_drift(tmp_path):
    words=[{'text':'Bonjour,','start':.1,'end':.7},{'text':'été.','start':.8,'end':1.2}]
    alignment=character_alignment(words)
    assert alignment['synthesized'] and alignment['temporal_granularity']=='word'
    # Actual audited MainClip consumer, READ ONLY; no MainClip files written.
    module=Path(r'C:\Users\demo\Desktop\Studium\projet tiktok\MainClip-code-source\MainClip-source\lib\subtitle-engine.cjs')
    if not module.is_file():pytest.skip('Audited external consumer absent')
    payload=tmp_path/'character-fixture.json';payload.write_text(json.dumps(alignment),encoding='utf-8')
    code="const fs=require('fs');const m=require(process.argv[1]);console.log(JSON.stringify(m.alignmentToCues(JSON.parse(fs.readFileSync(process.argv[2],'utf8')))))"
    actual=json.loads(subprocess.check_output(['node','-e',code,str(module),str(payload)],text=True,encoding='utf-8'))
    flattened=[w for c in actual for w in c['words']]
    for a,b in zip(flattened,words):
        assert a['text']==b['text'] and a['start']==pytest.approx(b['start']) and a['end']==pytest.approx(b['end'])
    assert cues(words)[-1]['end']==1.2


def test_real_existing_ab_measurements_and_sample_clock(tmp_path):
    root=ROOT/'reports'/'phase1_2'/'offline-analysis.json'
    if not root.is_file():pytest.skip('Actual A/B evidence not present')
    entries=json.loads(root.read_text(encoding='utf-8'))
    parts=[]
    for entry in entries:
        wav=ROOT/'data'/'phase1_1'/'ab'/entry['engine']/'api-download.wav'
        assert hash_file(wav)==entry['quality']['master']['sha256']
        assert rail_metrics(wav)['rail_sample_count']==entry['quality']['rails']['rail_sample_count']
        assert abs(entry['quality']['mp3_duration_error_seconds'])<1/44100
        assert entry['alignment']['review_alignment']['timestamps_valid']
        parts.append({'wav':wav,'audio_id':entry['id'],'voice_id':'authorized_existing_voice',
                      'words':entry['alignment']['review_alignment']['words']})
    assert entries[0]['quality']['rails']['clipping_evidence']
    first=entries[1]['alignment']['review_alignment']['words'][0]
    assert first['start']>1.5 and first['end']<2.5 and first['confidence_status']=='uncertain'
    assembled,timeline,words=assemble_pcm([parts[0],{'silence_seconds':.06},parts[1]],tmp_path)
    assert pcm_metadata(assembled)['duration_seconds']==pytest.approx(5.36+.06+5.84,abs=1/44100)
    assert words[11]['start']==pytest.approx(first['start']+5.42,abs=1/44100)
    assert timeline[-1]['sample_end']==round((5.36+.06+5.84)*44100)
