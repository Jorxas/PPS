"""Real CPU video→ASR→reference validation in an isolated PPS registry; no TTS."""
import copy
import hashlib
import json
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from pps.api import create_app
from pps.config import ROOT, load_config


class NoInference:
    engine = None

    def ensure(self, *args, **kwargs):
        raise AssertionError('This validation must never load a TTS engine')


def main():
    source = ROOT/'data/media/media_e0beed25b9784beca426526d93a8093a/source.media'
    assert source.is_file(), 'Previously authorized local PPS video fixture missing'
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    cfg = copy.deepcopy(load_config())
    workspace = ROOT/'runtime/voice-preparation-validation'/uuid.uuid4().hex
    for key in ('data', 'logs', 'cache'):
        cfg['paths'][key] = str(workspace/key)
        Path(cfg['paths'][key]).mkdir(parents=True, exist_ok=True)
    app = create_app(cfg, NoInference(), start_jobs=False)
    media = app.state.media
    start = time.monotonic()
    try:
        media.start()
        with TestClient(app) as client:
            data = source.read_bytes()
            r = client.post('/v1/media/uploads', json={'filename':'video-reference-autorisee.mp4',
                'size_bytes':len(data),'fingerprint':original_hash,'name':'Validation isolée voix autorisée',
                'language':'fr','authorized':True,'declaration':'Extrait de validation PPS, dérivé de la référence dont la voix et les mots ont déjà été autorisés et confirmés par l’utilisateur.'})
            assert r.status_code == 201, r.text
            imported = r.json()
            r = client.put('/v1/media/uploads/'+imported['id']+'?offset=0', content=data)
            assert r.status_code == 200, r.text
            r = client.post('/v1/media/uploads/'+imported['id']+'/complete')
            assert r.status_code == 200, r.text
            r = client.post('/v1/voice-preparations',json={'voice_id':imported['voice_id'],'media_ids':[imported['id']]})
            assert r.status_code == 202, r.text
            preparation = r.json()
            identifier = preparation['id']
            states=[]
            while preparation['status'] in ('analyzing','transcribing'):
                if not states or states[-1] != preparation['status']:
                    states.append(preparation['status']); print(preparation['status'], flush=True)
                assert time.monotonic()-start<240, 'CPU validation timed out'
                time.sleep(.25)
                preparation = client.get('/v1/voice-preparations/'+identifier).json()
            assert preparation['status'] == 'needs_confirmation', preparation
            voice = client.get('/v1/voices/'+imported['voice_id']).json()
            assert voice['references'] == []
            denied = client.post('/v1/voice-preparations/'+identifier+'/confirm', json={})
            assert denied.status_code == 422
            # Contract confirmation using the words already explicitly verified by the user.
            exact = 'Comprendre la dépression. Numéro 1. La dépression atypique.'
            r = client.post('/v1/voice-preparations/'+identifier+'/confirm', json={
                'speaker_confirmed':True,'transcription':exact,'transcription_verified':True,'language':'fr'})
            assert r.status_code == 201, r.text
            voice = r.json()
            assert len(voice['references']) == 1
            reference = voice['references'][0]
            assert voice['engine_reference_ids'] == {'chatterbox':reference['id'],'cosyvoice':reference['id']}
            repeated = client.post('/v1/voice-preparations',json={'voice_id':voice['id'],'media_ids':[imported['id']]}).json()
            assert repeated['status']=='ready' and repeated['id']==identifier
            assert client.get('/v1/jobs').json()['jobs']==[]
            assert hashlib.sha256(source.read_bytes()).hexdigest()==original_hash
            analysis = client.get('/v1/media/analyses/'+preparation['analysis_ids'][0]).json()
            report={'checked_at':time.strftime('%Y-%m-%dT%H:%M:%S'),'mode':'isolated real CPU validation, no production records or TTS',
                'workspace':str(workspace),'source_sha256':original_hash,'source_seconds':analysis['total_seconds'],
                'states':states+['needs_confirmation','ready'],'selection':preparation['selection'],
                'asr_proposal':preparation['transcription']['text'],'transcription_verified_automatically':False,
                'confirmation_omitted_http_status':denied.status_code,'reference_id':reference['id'],
                'references_count':len(voice['references']),'engine_reference_ids':voice['engine_reference_ids'],
                'analysis_metrics':analysis['metrics'],'asr_metrics':preparation['transcription'].get('resource_metrics'),
                'elapsed_seconds':time.monotonic()-start,'tts_jobs_created':0,'original_unchanged':True}
            (ROOT/'reports/voice-preparation-real-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps({'states':report['states'],'references':1,'tts_jobs':0,'seconds':round(report['elapsed_seconds'],2)}),flush=True)
    finally:
        media.close()


if __name__ == '__main__':
    main()
