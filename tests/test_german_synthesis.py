from conftest import import_tone, wait_job
from pps.engines import capabilities


def test_german_synthesis_retains_reference_and_language(client):
    voice = import_tone(client).json()
    request = {"voice_id": voice["id"], "engine": "chatterbox", "language": "de", "text": "Der Krieger kehrt zurück."}
    response = client.post('/v1/syntheses', json=request)
    assert response.status_code == 202, response.text
    job = wait_job(client, response.json()['job_id'])
    assert job['status'] == 'completed', job
    assert job['request']['language'] == 'de'
    assert job['request']['reference_id'] == voice['active_reference_id']
    metadata = client.get('/v1/audio/' + job['audio_id'] + '/metadata').json()
    assert metadata['generation_request']['language'] == 'de'
    assert metadata['voice_id'] == voice['id']


def test_german_is_not_advertised_or_accepted_for_cosyvoice(client):
    assert 'de' in capabilities('chatterbox')['languages']
    assert 'de' not in capabilities('cosyvoice')['languages']
    voice = import_tone(client).json()
    response = client.post('/v1/syntheses', json={"voice_id": voice['id'], "engine": "cosyvoice", "language": "de", "text": "Guten Tag."})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'SCRIPT_INVALID'


def test_german_duration_estimate(client):
    response = client.post('/v1/duration-estimate', json={"engine": "chatterbox", "language": "de", "text": "Der Krieger kehrt zurück."})
    assert response.status_code == 200
    assert response.json()['language'] == 'de'
