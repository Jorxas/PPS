"""Durable CPU preparation: import once, propose one reference, confirm once."""
import time

from .errors import PPSError
from .storage import identity


ACTIVE = {'analyzing', 'transcribing'}


def suggestion(analysis, candidate, filename):
    metrics = candidate['metrics']
    issues = [key for key in ('speaker_status', 'music_status', 'noise_status', 'echo_status')
              if (metrics.get(key) or '').startswith('suspected_')]
    return {'analysis_id': analysis['id'], 'candidate_id': candidate['id'], 'filename': filename,
            'start_seconds': candidate['start_seconds'], 'end_seconds': candidate['end_seconds'],
            'score': metrics.get('score') or 0, 'issues': issues}


class VoicePreparationMixin:
    def prepare_voice(self, voice_id, media_ids, tracks=None):
        tracks = tracks or {}
        if len(set(media_ids)) != len(media_ids):
            raise PPSError('MEDIA_DUPLICATE', 'Chaque fichier doit être sélectionné une seule fois.', 422)
        if set(tracks) - set(media_ids):
            raise PPSError('TRACK_INVALID', 'Piste attribuée à un fichier absent de la préparation.', 422)
        self.store.get('voices', voice_id)
        sources = [self.store.get('media', identifier) for identifier in media_ids]
        chosen, missing = {}, []
        # Validate the whole batch before enqueuing any analysis.
        for source in sources:
            if source['voice_id'] != voice_id:
                raise PPSError('VOICE_SOURCE_MISMATCH', 'Ces fichiers doivent appartenir à la même voix.', 422)
            if not source['authorization']['granted']:
                raise PPSError('AUTHORIZATION_REQUIRED', 'Clonage non autorisé pour ce fichier.', 422)
            if source['status'] != 'ready':
                raise PPSError('MEDIA_NOT_READY', 'Terminez l’import avant de préparer la voix.', 409)
            indices = [track['index'] for track in source['probe']['audio_tracks']]
            if source['id'] in tracks:
                if tracks[source['id']] not in indices:
                    raise PPSError('TRACK_INVALID', 'Piste audio inconnue.', 422)
                chosen[source['id']] = tracks[source['id']]
            elif len(indices) == 1:
                chosen[source['id']] = indices[0]
            else:
                missing.append(source['id'])
        with self.preparation_lock:
            previous = next((p for p in reversed(self.store.list('voice_preparations'))
                             if p['voice_id'] == voice_id and set(p['media_ids']) == set(media_ids)
                             and p['track_indices'] == chosen), None)
            if previous:
                return previous
            waiting = next((p for p in reversed(self.store.list('voice_preparations'))
                            if p['voice_id'] == voice_id and set(p['media_ids']) == set(media_ids)
                            and p['status'] == 'needs_track'), None)
            value = {'id': waiting['id'] if waiting else identity('preparation'), 'voice_id': voice_id, 'media_ids': media_ids,
                     'track_indices': chosen, 'missing_track_media_ids': missing,
                     'status': 'needs_track' if missing else 'analyzing', 'created_at': time.time(),
                     'analysis_ids': [], 'owned_analysis_ids': [], 'selection': None,
                     'transcription': None, 'suggestions': [], 'error': None}
            if not missing:
                existing_ids = {a['id'] for a in self.store.list('media_analyses')}
                for source in sources:
                    matches = [a for a in self.store.list('media_analyses')
                               if a['media_id'] == source['id'] and a['track_index'] == chosen[source['id']]]
                    analysis = matches[-1] if matches else self.analyze(source['id'], chosen[source['id']])
                    value['analysis_ids'].append(analysis['id'])
                    if analysis['id'] not in existing_ids:
                        value['owned_analysis_ids'].append(analysis['id'])
            self.store.put('voice_preparations', value)
        self.wakeup.set()
        return value

    def preparation_progress(self, value):
        result = dict(value)
        result.pop('owned_analysis_ids', None)
        analyses = [self.store.get('media_analyses', identifier) for identifier in value['analysis_ids']]
        total = sum(a['total_seconds'] for a in analyses)
        result['progress'] = (sum(a['processed_seconds'] for a in analyses) / total) if total else 0
        result['files_completed'] = sum(a['status'] == 'completed' for a in analyses)
        result['files_total'] = len(value['media_ids'])
        return result

    def recover_preparations(self):
        for value in self.store.list('voice_preparations'):
            if value['status'] in ACTIVE:
                # Analyses recover as paused separately; completed analyses/ASR are reused.
                self.store.update('voice_preparations', value['id'], status='analyzing')

    def advance_preparation(self, identifier):
        value = self.store.get('voice_preparations', identifier)
        if value['status'] not in ACTIVE:
            return
        cancelled = lambda: self.stop.is_set() or self.store.get('voice_preparations', identifier)['status'] == 'cancelled'
        try:
            analyses = [self.store.get('media_analyses', key) for key in value['analysis_ids']]
            interrupted = next((a for a in analyses if a['status'] in ('failed', 'cancelled', 'paused')), None)
            if interrupted:
                with self.preparation_lock:
                    if not cancelled():
                        self.store.update('voice_preparations', identifier, status='paused',
                                          error=interrupted.get('error') or {'message': 'Préparation interrompue. Vous pouvez la reprendre.'})
                return
            if any(a['status'] != 'completed' for a in analyses):
                return
            proposals = []
            for analysis in analyses:
                source = self.store.get('media', analysis['media_id'])
                proposals.extend(suggestion(analysis, c, source['filename']) for c in analysis['candidates'])
            proposals.sort(key=lambda p: p['score'], reverse=True)
            if not value['selection']:
                # Unknown speaker identity is a suggestion, never an automatic confirmation.
                clean = [p for p in proposals if not p['issues']]
                if not clean:
                    with self.preparation_lock:
                        if not cancelled():
                            self.store.update('voice_preparations', identifier, status='needs_selection', suggestions=proposals,
                                              error=None)
                    return
                value['selection'] = clean[0]
            selected = value['selection']
            with self.preparation_lock:
                if cancelled(): return
                self.store.update('voice_preparations', identifier, status='transcribing',
                                  selection=selected, suggestions=proposals, error=None)
            _, candidate = self.candidate(selected['analysis_id'], selected['candidate_id'])
            if not candidate.get('transcription'):
                candidate = self.transcribe(selected['analysis_id'], selected['candidate_id'], cancelled=cancelled)
            with self.preparation_lock:
                if cancelled():
                    return
                self.store.update('voice_preparations', identifier, status='needs_confirmation',
                                  transcription=candidate['transcription'], error=None)
        except Exception as exc:
            with self.preparation_lock:
                if cancelled():
                    return
                retryable = isinstance(exc, PPSError) and exc.retryable
                self.store.update('voice_preparations', identifier, status='paused' if retryable else 'failed',
                                  error=exc.payload() if isinstance(exc, PPSError) else {'message': str(exc)})

    def select_preparation_candidate(self, identifier, analysis_id, candidate_id):
        with self.preparation_lock:
            value = self.store.get('voice_preparations', identifier)
            if value['status'] not in ('needs_selection', 'needs_confirmation', 'failed', 'paused'):
                raise PPSError('PREPARATION_STATE_INVALID', 'Attendez la fin de la préparation pour changer d’extrait.', 409)
            if analysis_id not in value['analysis_ids']:
                raise PPSError('CANDIDATE_UNKNOWN', 'Cet extrait ne fait pas partie de cette voix.', 422)
            analysis, candidate = self.candidate(analysis_id, candidate_id)
            source = self.store.get('media', analysis['media_id'])
            value.update(status='analyzing', selection=suggestion(analysis, candidate, source['filename']),
                         transcription=None, error=None)
            self.store.put('voice_preparations', value)
        self.wakeup.set()
        return value

    def confirm_preparation(self, identifier, body):
        with self.preparation_lock:
            value = self.store.get('voice_preparations', identifier)
            if not body['speaker_confirmed'] or not body['transcription_verified']:
                raise PPSError('VOICE_CONFIRMATION_REQUIRED', 'Confirmez la voix et les mots après écoute.', 422)
            if value['status'] == 'ready':
                return self.store.get('voices', value['voice_id'])
            if value['status'] != 'needs_confirmation':
                raise PPSError('PREPARATION_STATE_INVALID', 'La voix attend la fin de sa préparation.', 409)
            selected = value['selection']
            voice = self.save(selected['analysis_id'], selected['candidate_id'], body)
            _, candidate = self.candidate(selected['analysis_id'], selected['candidate_id'])
            reference_id = candidate['saved_reference_id']
            # A previously saved Chatterbox-only reference may now be verified for CosyVoice.
            with self.store.transaction() as db:
                voice = self.store.get('voices', voice['id'], db)
                reference = next(r for r in voice['references'] if r['id'] == reference_id)
                if not reference['authorization']['granted']:
                    raise PPSError('AUTHORIZATION_REQUIRED', 'Clonage non autorisé pour cet extrait.', 422)
                corrected = body['transcription'].strip()
                if (reference['transcription'] != corrected or reference.get('transcription_verified') is not True
                        or reference['language'] != body['language']):
                    reference.update(transcription=corrected, transcription_verified=True, language=body['language'],
                                     transcription_verification={'verified': True, 'recorded_at': time.time(), 'method': 'user'})
                    reference['preparation']['cosyvoice'] = 'audio_ready_conditioning_pending'
                    voice['version'] += 1
                    self.store.put('voices', voice, db)
            # Keep deliberate engine-specific choices when enriching an existing voice.
            for engine in ('chatterbox', 'cosyvoice'):
                if engine not in voice.get('engine_reference_ids', {}):
                    voice = self.choose(voice['id'], engine, reference_id)
            self.store.update('voice_preparations', identifier, status='ready', reference_id=reference_id,
                              confirmed_at=time.time(), error=None)
            return voice

    def cancel_preparation(self, identifier):
        with self.preparation_lock:
            value = self.store.get('voice_preparations', identifier)
            if value['status'] == 'ready':
                return value
            self.store.update('voice_preparations', identifier, status='cancelled')
            for analysis_id in value['owned_analysis_ids']:
                shared = any(p['id'] != identifier and p['status'] in ACTIVE and analysis_id in p['analysis_ids']
                             for p in self.store.list('voice_preparations'))
                if not shared:
                    self.cancel(analysis_id)
            return self.store.get('voice_preparations', identifier)

    def resume_preparation(self, identifier):
        with self.preparation_lock:
            value = self.store.get('voice_preparations', identifier)
            if value['status'] in ('paused', 'failed', 'cancelled'):
                if value['missing_track_media_ids']:
                    return self.store.update('voice_preparations', identifier, status='needs_track', error=None)
                for analysis_id in value['analysis_ids']:
                    self.resume(analysis_id)
                value = self.store.update('voice_preparations', identifier, status='analyzing', error=None)
        self.wakeup.set()
        return value
