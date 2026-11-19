"""Append-only human reviews tied to immutable automatic alignment and PCM hash."""
import copy
import time
from .alignment import canonical_tokens, valid_bounds, annotated
from .errors import PPSError
from .storage import identity

RATINGS = ('fidelity','naturalness','pronunciation','expressiveness','artifacts','audio_quality')


def effective_alignment(audio, review=None):
    base = copy.deepcopy(audio['alignment'].get('review_alignment') or
                         audio['alignment'].get('forced_alignment') or audio['alignment'])
    base['words']=annotated(base.get('words',[]),base.get('timestamp_provenance','unknown'),
                            base.get('timestamp_provenance','unknown'))
    composition = (base.get('status') in ('source_reviewed_composition', 'source_automated_composition') and
                   base.get('audio_sha256')==audio['pcm']['sha256'] and
                   valid_bounds(base.get('words',[]),audio['pcm']['duration_seconds']) and
                   all(w.get('confidence_status') in ('human_attested','human_corrected','automatic_acoustic_verified') for w in base['words']))
    base.update(audio_sha256=audio['pcm']['sha256'], mainclip_compatible=composition, exact=False)
    if review and review['audio_sha256']==audio['pcm']['sha256'] and review['alignment_revision']==audio.get('alignment_revision',1):
        for correction in review['corrections']:
            w = base['words'][correction['word_index']]
            w['automatic'] = copy.deepcopy(w)
            w.update(start=correction['start'],end=correction['end'],method='human_boundary_correction',
                     provenance={'review_id':review['id'],'automatic':w['automatic']['provenance']},
                     confidence_status='human_corrected',uncertainty=['human_estimate_not_sample_exact'])
        base['review_id'] = review['id']
        expected = canonical_tokens(audio['text'])
        observed = [t for w in base['words'] for t in canonical_tokens(w.get('text',w.get('word','')))]
        if review['acoustically_confirmed'] and expected==observed and valid_bounds(base['words'],audio['pcm']['duration_seconds']):
            is_human = review.get('author') not in ('automated_script', 'automated_script_phase2c', 'synthetic', 'system', 'script', 'auto_verifier', 'local_user')
            status = 'human_reviewed' if is_human else 'automatic_acoustic_verified'
            quality = 'human_attested_word_boundaries' if is_human else 'automatic_verified_word_boundaries'
            base.update(mainclip_compatible=True,status=status,quality=quality)
            for w in base['words']:
                if w['confidence_status']!='human_corrected':
                    w['confidence_status']='human_attested' if is_human else 'automatic_acoustic_verified'
    return base


def latest_review(store, audio_id):
    return next((r for r in reversed(store.list('reviews')) if r['audio_id']==audio_id), None)


def save_review(store, audio_id, body):
    with store.transaction() as db:
        audio = store.get('audio',audio_id,db)
        if body['audio_sha256'] != audio['pcm']['sha256'] or body['alignment_revision'] != audio.get('alignment_revision',1):
            raise PPSError('REVIEW_STALE','Audio ou alignement modifié ; rechargez les frontières.',409)
        automatic = effective_alignment(audio)
        indexes = [c['word_index'] for c in body['corrections']]
        if len(set(indexes))!=len(indexes) or any(i<0 or i>=len(automatic['words']) for i in indexes):
            raise PPSError('REVIEW_INVALID','Indices de mots invalides ou répétés.',422)
        author = body.get('author') or 'local_user'
        record = {**body,'id':identity('review'),'audio_id':audio_id,'created_at':time.time(),
                  'automatic_alignment_snapshot':automatic,'author':author}
        candidate = effective_alignment(audio, record)
        if not valid_bounds(candidate['words'],audio['pcm']['duration_seconds']):
            raise PPSError('REVIEW_INVALID','Frontières non finies, chevauchantes ou hors audio.',422)
        if body['acoustically_confirmed'] and not candidate['mainclip_compatible']:
            raise PPSError('SCRIPT_MISMATCH','La transcription ne couvre pas exactement le script.',422)
        store.put('reviews',record,db)
    return record
