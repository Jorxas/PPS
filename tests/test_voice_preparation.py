"""CPU orchestration contracts. Synthetic signals never impersonate a speaker."""
from pathlib import Path

import pytest

from pps.audio import run
from pps.errors import PPSError
from test_media import media_client, upload, FixtureWorker
from conftest import tone_bytes


class ClearWorker(FixtureWorker):
    asr_calls = 0

    def call(self, action, path, manual=False):
        result = super().call(action, path, manual)
        if action == 'transcribe':
            type(self).asr_calls += 1
        else:
            for candidate in result['result']['candidates']:
                candidate['metrics']['speaker_status'] = 'unknown'
        return result


def start(client, sources, tracks=None):
    return client.post('/v1/voice-preparations', json={
        'voice_id': sources[0]['voice_id'], 'media_ids': [m['id'] for m in sources],
        'track_indices': tracks or {}})


def finish_analysis(client, preparation, worker, monkeypatch):
    monkeypatch.setattr('pps.media.CPUWorker', worker)
    service = client.app.state.media
    for identifier in preparation['analysis_ids']:
        service.process(identifier)
    service.advance_preparation(preparation['id'])
    return client.get('/v1/voice-preparations/'+preparation['id']).json()


def confirmation():
    return {'speaker_confirmed': True, 'transcription_verified': True,
            'transcription': 'Signal synthétique.', 'language': 'fr'}


def test_import_prepare_once_confirm_once_and_reuse_both_engines(media_client, monkeypatch):
    ClearWorker.asr_calls = 0
    source = upload(media_client, tone_bytes())
    preparation = start(media_client, [source]).json()
    assert preparation['status'] == 'analyzing'
    repeated = start(media_client, [source]).json()
    assert repeated['id'] == preparation['id'] and len(repeated['analysis_ids']) == 1
    completed = finish_analysis(media_client, preparation, ClearWorker, monkeypatch)
    assert completed['status'] == 'needs_confirmation' and completed['progress'] == 1
    assert completed['transcription']['text'] == 'Signal synthétique.'
    assert completed['transcription']['verified'] is False
    assert ClearWorker.asr_calls == 1
    voice = media_client.get('/v1/voices/'+source['voice_id']).json()
    assert voice['references'] == []  # Suggested speaker/ASR never become human-confirmed automatically.
    url = '/v1/voice-preparations/'+preparation['id']+'/confirm'
    assert media_client.post(url, json={}).status_code == 422
    assert media_client.post(url, json={**confirmation(), 'transcription': ''}).status_code == 422
    result = media_client.post(url, json=confirmation())
    assert result.status_code == 201, result.text
    voice = result.json()
    reference = voice['references'][0]
    assert voice['engine_reference_ids'] == {'chatterbox': reference['id'], 'cosyvoice': reference['id']}
    assert reference['provenance']['source_sha256'] == source['sha256']
    assert reference['speaker_confirmation']['method'] == 'user_listening'
    assert reference['transcription_verified'] is True
    again = media_client.post(url, json=confirmation()).json()
    assert len(again['references']) == 1 and again['version'] == voice['version']
    assert start(media_client, [source]).json()['status'] == 'ready'
    for engine in ('chatterbox', 'cosyvoice'):
        for text in ('Bonjour.', 'Un autre texte.'):
            r = media_client.post('/v1/syntheses', json={'voice_id':voice['id'], 'engine':engine, 'text':text})
            assert r.status_code == 202
    assert ClearWorker.asr_calls == 1 and len(media_client.app.state.store.list('media_analyses')) == 1
    assert 'original' not in reference and 'track_path' not in completed


def test_batch_sources_pick_one_reference_without_concatenation(media_client, monkeypatch):
    first = upload(media_client, tone_bytes(), 'first.wav')
    second = upload(media_client, tone_bytes(), 'second.wav', first['voice_id'])
    prep = start(media_client, [first, second]).json()
    completed = finish_analysis(media_client, prep, ClearWorker, monkeypatch)
    assert completed['files_completed'] == completed['files_total'] == 2
    assert len(completed['suggestions']) == 2 and completed['selection']['filename'] == 'first.wav'
    result = media_client.post('/v1/voice-preparations/'+prep['id']+'/confirm',json=confirmation()).json()
    assert len(result['references']) == 1
    assert result['references'][0]['pcm']['duration_seconds'] <= 8


def test_multiple_speakers_not_selected_automatically(media_client, monkeypatch):
    source = upload(media_client, tone_bytes())
    prep = start(media_client, [source]).json()
    done = finish_analysis(media_client, prep, FixtureWorker, monkeypatch)
    assert done['status'] == 'needs_selection' and done['selection'] is None
    assert done['transcription'] is None
    proposal = done['suggestions'][0]
    assert proposal['issues'] == ['speaker_status']
    url = '/v1/voice-preparations/'+prep['id']
    assert media_client.post(url+'/confirm',json=confirmation()).status_code == 409
    selected = media_client.put(url+'/candidate',json={
        'analysis_id':proposal['analysis_id'],'candidate_id':proposal['candidate_id']})
    assert selected.status_code == 200
    media_client.app.state.media.advance_preparation(prep['id'])
    assert media_client.get(url).json()['status'] == 'needs_confirmation'
    assert media_client.get('/v1/voices/'+source['voice_id']).json()['references'] == []


def test_multiple_tracks_require_one_choice_then_continue_same_preparation(media_client, tmp_path):
    path = tmp_path/'two-tracks.mkv'
    run(['-f','lavfi','-i','sine=frequency=220:duration=3','-f','lavfi','-i','sine=frequency=440:duration=3',
         '-map','0:a','-map','1:a','-c:a','flac',path])
    source = upload(media_client, path.read_bytes(), path.name)
    waiting = start(media_client, [source]).json()
    assert waiting['status'] == 'needs_track' and waiting['analysis_ids'] == []
    assert media_client.app.state.store.list('media_analyses') == []
    invalid = start(media_client, [source], {source['id']:999})
    assert invalid.status_code == 422
    chosen = start(media_client, [source], {source['id']:1}).json()
    assert chosen['id'] == waiting['id'] and chosen['status'] == 'analyzing'
    assert len(media_client.app.state.store.list('voice_preparations')) == 1
    analysis = media_client.get('/v1/media/analyses/'+chosen['analysis_ids'][0]).json()
    assert analysis['track_index'] == 1


def test_preparation_validates_all_sources_before_scheduling(media_client):
    first = upload(media_client, tone_bytes(), 'first.wav')
    other = upload(media_client, tone_bytes(), 'other.wav')
    assert start(media_client, [first, other]).status_code == 422
    assert start(media_client, [first, first]).status_code == 422
    assert start(media_client, [first], {'not-in-batch':0}).status_code == 422
    assert start(media_client, [first], {first['id']:True}).status_code == 422
    assert media_client.app.state.store.list('media_analyses') == []
    assert media_client.app.state.store.list('voice_preparations') == []


def test_cancel_resume_and_restart_reuse_analysis_and_transcript(media_client, monkeypatch):
    ClearWorker.asr_calls = 0
    source = upload(media_client, tone_bytes())
    prep = start(media_client,[source]).json()
    url = '/v1/voice-preparations/'+prep['id']
    assert media_client.post(url+'/cancel').json()['status'] == 'cancelled'
    assert media_client.get('/v1/media/analyses/'+prep['analysis_ids'][0]).json()['status'] == 'cancelled'
    assert media_client.post(url+'/resume').json()['status'] == 'analyzing'
    done = finish_analysis(media_client,prep,ClearWorker,monkeypatch)
    assert done['status'] == 'needs_confirmation'
    service = media_client.app.state.media
    service.store.update('voice_preparations',prep['id'],status='transcribing')
    service.recover_preparations()
    service.advance_preparation(prep['id'])
    assert media_client.get(url).json()['status'] == 'needs_confirmation'
    assert ClearWorker.asr_calls == 1


def test_cancel_during_transcription_does_not_complete_or_save_voice(media_client,monkeypatch):
    source=upload(media_client,tone_bytes())
    prep=start(media_client,[source]).json()
    service=media_client.app.state.media
    class CancellingWorker(ClearWorker):
        def call(self,action,*args,**kwargs):
            if action=='transcribe':service.cancel_preparation(prep['id'])
            return super().call(action,*args,**kwargs)
    done=finish_analysis(media_client,prep,CancellingWorker,monkeypatch)
    assert done['status']=='cancelled' and done['transcription'] is None
    assert media_client.get('/v1/voices/'+source['voice_id']).json()['references']==[]


def test_resource_pause_can_resume_without_reanalysing(media_client,monkeypatch):
    source=upload(media_client,tone_bytes());prep=start(media_client,[source]).json()
    monkeypatch.setattr('pps.media.CPUWorker',ClearWorker)
    service=media_client.app.state.media
    service.process(prep['analysis_ids'][0])
    original=service.transcribe
    def busy(*args,**kwargs):raise PPSError('RESOURCE_BUSY','Libérez de la RAM.',409,retryable=True)
    monkeypatch.setattr(service,'transcribe',busy)
    service.advance_preparation(prep['id'])
    assert service.store.get('voice_preparations',prep['id'])['status']=='paused'
    service.resume_preparation(prep['id']);monkeypatch.setattr(service,'transcribe',original)
    service.advance_preparation(prep['id'])
    assert service.store.get('voice_preparations',prep['id'])['status']=='needs_confirmation'
    assert len(service.store.list('media_analyses'))==1


def test_source_authorization_rechecked_at_confirmation(media_client,monkeypatch):
    source=upload(media_client,tone_bytes());prep=start(media_client,[source]).json()
    finish_analysis(media_client,prep,ClearWorker,monkeypatch)
    service=media_client.app.state.media
    original=service.store.get('media',source['id'])
    original['authorization']['granted']=False;service.store.put('media',original)
    r=media_client.post('/v1/voice-preparations/'+prep['id']+'/confirm',json=confirmation())
    assert r.status_code==422 and r.json()['error']['code']=='AUTHORIZATION_REQUIRED'
    assert media_client.get('/v1/voices/'+source['voice_id']).json()['references']==[]


def test_existing_unverified_reference_is_corrected_without_duplicate(media_client,monkeypatch):
    source=upload(media_client,tone_bytes());prep=start(media_client,[source]).json()
    done=finish_analysis(media_client,prep,ClearWorker,monkeypatch)
    selected=done['selection']
    saved=media_client.post('/v1/media/analyses/'+selected['analysis_id']+'/candidates/'+selected['candidate_id']+'/reference',json={**confirmation(),'transcription':'Ancienne proposition.','transcription_verified':False}).json()
    r=media_client.post('/v1/voice-preparations/'+prep['id']+'/confirm',json=confirmation())
    assert r.status_code==201,r.text
    voice=r.json()
    assert len(voice['references'])==1 and voice['references'][0]['id']==saved['references'][0]['id']
    assert voice['references'][0]['transcription']=='Signal synthétique.'
    assert voice['references'][0]['transcription_verified'] is True
    assert voice['engine_reference_ids']['cosyvoice']==voice['references'][0]['id']
