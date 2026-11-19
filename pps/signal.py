"""Bounded PCM measurements, independent of ML imports; no signal modification."""
import array
import math
import sys
import wave


def read_pcm(path):
    with wave.open(str(path), 'rb') as f:
        if f.getnchannels() != 1 or f.getsampwidth() != 2 or f.getcomptype() != 'NONE':
            raise ValueError('PCM16 mono required')
        rate = f.getframerate()
        values = array.array('h', f.readframes(f.getnframes()))
    if sys.byteorder != 'little':
        values.byteswap()
    return values, rate


def activity(path, threshold_db=-35, window_seconds=.01, sustained_seconds=.06):
    """Energy activity, NOT a speech VAD. Sustained activity rejects isolated clicks.

    Alignment crops retain 120 ms pre-roll; these measurements do not prove phoneme boundaries.
    """
    values, rate = read_pcm(path)
    size = max(1, round(rate * window_seconds))
    levels = [math.sqrt(sum(v*v for v in values[i:i+size]) / len(values[i:i+size])) / 32768
              for i in range(0, len(values), size)]
    active = [x >= 10**(threshold_db/20) for x in levels]
    required = max(1, math.ceil(sustained_seconds / window_seconds))
    onset = None
    for i in range(len(active) - required + 1):
        if all(active[i:i+required]):
            onset = i * size / rate
            break
    silences, begin = [], None
    for i, on in enumerate(active + [True]):
        if not on and begin is None:
            begin = i
        if on and begin is not None:
            end = min(i * size / rate, len(values)/rate)
            if end - begin * size / rate >= .15:
                silences.append({'start':begin * size / rate, 'end':end})
            begin = None
    return {'method':'PCM_RMS_sustained_energy_not_speech_VAD', 'threshold_db':threshold_db,
            'window_seconds':size/rate, 'sustained_seconds':required*size/rate,
            'sustained_onset_seconds':onset, 'analysis_crop_offset_seconds':max(0, (onset or 0) - .12),
            'silences':silences, 'rms_windows':levels, 'duration_seconds':len(values)/rate}


def rail_metrics(path):
    values, rate = read_pcm(path)
    near = sum(abs(v) >= 32760 for v in values)
    rails = sum(v in (-32768,32767) for v in values)
    runs, current, longest = [], 0, 0
    previous = None
    for i, v in enumerate(values):
        if v in (-32768, 32767):
            current = current + 1 if v == previous else 1
            if current == 2:
                runs.append({'start':(i-1)/rate, 'polarity':1 if v>0 else -1})
            longest = max(longest, current)
        else:
            current = 0
        previous = v
    return {'near_rail_sample_count':near, 'rail_sample_count':rails,
            'flat_rail_run_count':len(runs), 'longest_flat_rail_run_samples':longest,
            'flat_rail_run_starts':runs, 'clipping_evidence':bool(longest >= 2),
            'max_adjacent_step_amplitude':max((abs(a-b)/32768 for a,b in zip(values, values[1:])), default=0),
            'first_sample_amplitude':values[0]/32768, 'last_sample_amplitude':values[-1]/32768,
            'note':'Flat full-scale plateaus are clipping evidence; pre-PCM overshoot and perceptual distortion cannot be recovered.'}
