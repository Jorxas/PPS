"""Synthesis cache identity includes reference, version, revisions and context."""
import hashlib
import json
from pathlib import Path
from .audio import hash_file
from .engines import status
from .errors import PPSError


def effective_options(engine, options):
    defaults = {'exaggeration':.5,'cfg_weight':.5,'temperature':.8} if engine=='chatterbox' else {'speed':1}
    return {**defaults,**options}


def cache_key(request, cfg):
    sources = Path(cfg['root'])/'reports'/'sources.lock.json'
    commits = json.loads(sources.read_text(encoding='utf-8')) if sources.exists() else {}
    key = {k:request.get(k) for k in ('engine','voice_id','voice_version','reference_id','reference_hash',
           'text','display_text','language','seed','options','previous_text','next_text')}
    key['options']=effective_options(request['engine'],request.get('options',{}))
    key.update(contract_version=2,model_revision=status(request['engine'],cfg)['model_revision'],
               source_revision=commits.get(request['engine'],{}).get('commit'),
               model_path=cfg[request['engine']]['model'],alignment_config=cfg['alignment'])
    return hashlib.sha256(json.dumps(key,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def lookup(store, key):
    try:
        entry = store.get('synthesis_cache',key)
        audio = store.get('audio',entry['audio_id'])
        if hash_file(audio['wav'])!=audio['pcm']['sha256'] or hash_file(audio['mp3'])!=audio['mp3_sha256']:
            return None
        return audio
    except (PPSError,OSError):
        return None
