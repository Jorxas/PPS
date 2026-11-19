"""Bounded CPU-only media analysis. JSON-line IPC; no network, no TTS weights."""
import json
import csv
import os
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(CUDA_VISIBLE_DEVICES='', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
    OMP_NUM_THREADS='2', MKL_NUM_THREADS='2', NUMBA_NUM_THREADS='2')
from pps.memory import memory_snapshot

def guard():
    if memory_snapshot()['physical_available_gib'] < 3:
        raise RuntimeError('RESOURCE_BUSY: analysis requires 3 GiB available physical RAM')

guard()
import numpy as np
import onnxruntime as ort
import torch
import torchaudio
torch.set_num_threads(2)
guard()
options = ort.SessionOptions()
options.intra_op_num_threads = 2
options.inter_op_num_threads = 1
vad = ort.InferenceSession(str(ROOT / 'tools/media/silero_vad_v5.1.2.onnx'),
    sess_options=options, providers=['CPUExecutionProvider'])
speaker = ort.InferenceSession(str(ROOT / 'models/Fun-CosyVoice3-0.5B-2512/campplus.onnx'),
    sess_options=options, providers=['CPUExecutionProvider'])
events = ort.InferenceSession(str(ROOT/'tools/media/yamnet-qualcomm.onnx'),
    sess_options=options,providers=['CPUExecutionProvider'])
with (ROOT/'tools/media/yamnet_class_map.csv').open(encoding='utf-8') as labels:
    class_names={int(row['index']):row['display_name'] for row in csv.DictReader(labels)}
music_index=next(i for i,name in class_names.items() if name=='Music')
noise_indices=[i for i,name in class_names.items() if name in ('Noise','White noise','Pink noise','Static','Hiss','Background noise')]
event_mel=torchaudio.transforms.MelSpectrogram(sample_rate=16000,n_fft=512,win_length=400,
    hop_length=160,f_min=125,f_max=7500,n_mels=64,power=1,norm=None,mel_scale='htk')
asr_model = None

def read_clip(path):
    with wave.open(str(path), 'rb') as f:
        if f.getnchannels() != 1 or f.getsampwidth() != 2 or f.getframerate() != 16000 or f.getnframes() > 62*16000:
            raise ValueError('Only bounded PCM16 mono 16 kHz windows up to 62 seconds')
        return np.frombuffer(f.readframes(f.getnframes()), dtype='<i2').astype(np.float32) / 32768

def probabilities(audio):
    state = np.zeros((2,1,128), dtype=np.float32)
    context = np.zeros((1,64), dtype=np.float32)
    result = []
    for offset in range(0, len(audio), 512):
        chunk = np.pad(audio[offset:offset+512], (0,max(0,offset+512-len(audio))))[None,:]
        probability, state = vad.run(None, {'input': np.concatenate([context,chunk],axis=1),
            'state':state, 'sr': np.array(16000,dtype=np.int64)})
        context = chunk[:,-64:]
        result.append(float(probability[0,0]))
    return np.array(result)

def speech_ranges(probs, duration):
    ranges, start, last = [], None, 0
    for i,p in enumerate(probs):
        t = i*.032
        if p >= .5:
            if start is None: start = t
            last = min(duration,t+.032)
        if start is not None and (t-last >= .35 or i == len(probs)-1):
            if last-start >= .3: ranges.append([max(0,start-.12),min(duration,last+.15)])
            start = None
    return ranges

def embedding(audio):
    if len(audio) < 16000: return None
    features = torchaudio.compliance.kaldi.fbank(torch.from_numpy(audio.copy())[None,:],
        num_mel_bins=80,dither=0,sample_frequency=16000)
    features -= features.mean(dim=0,keepdim=True)
    e = speaker.run(None,{speaker.get_inputs()[0].name:features[None,:].numpy()})[0].flatten()
    return e / max(float(np.linalg.norm(e)),1e-9)

def audio_events(audio):
    # Match the upstream torch_audioset frontend used by this Qualcomm export.
    features=torch.log(event_mel(torch.from_numpy(audio.copy()))+.001).T.numpy()
    scores=[]
    for start in range(0,len(features),100):
        patch=features[start:start+96]
        if len(patch)<48 and scores:break
        if len(patch)<96:patch=np.pad(patch,((0,96-len(patch)),(0,0)),constant_values=np.log(.001))
        scores.append(events.run(None,{'audio':patch[None,None,:,:].astype(np.float32)})[0][0])
    values=np.array(scores);mean=values.mean(axis=0)
    music=float(mean[music_index]); fraction=float(np.mean(values[:,music_index]>=.2))
    noise=float(max((mean[i] for i in noise_indices),default=0))
    return {'provider':'qualcomm_yamnet_onnx_cpu','music_mean_score':music,'music_frame_fraction':fraction,
        'noise_mean_score':noise,'scores_calibrated':False,
        'top_events':[{'label':class_names[int(i)],'score':float(mean[i])} for i in np.argsort(mean)[-5:][::-1]]}

def metrics(audio, probs):
    duration = len(audio)/16000
    padded = np.pad(audio,(0,(-len(audio))%512)).reshape(-1,512)
    rms = np.sqrt(np.mean(padded**2,axis=1))
    probs = np.pad(probs,(0,max(0,len(rms)-len(probs))),mode='edge')[:len(rms)]
    mask = probs[:len(rms)] >= .5
    speech = rms[mask]; background = rms[~mask]
    snr = None
    if len(speech) and len(background) >= 5:
        snr = float(np.clip(20*np.log10(max(np.median(speech),1e-8)/max(np.percentile(background,20),1e-8)),-30,80))
    saturation = float(np.mean(np.abs(audio) >= 32760/32768))
    steadiness = float(np.std(speech)/max(np.mean(speech),1e-8)) if len(speech) else 1
    # Narrow spectral dominance warns of tones/music. It is not a music classifier.
    frames = np.pad(audio,(0,(-len(audio))%1024)).reshape(-1,1024)
    spectra = np.abs(np.fft.rfft(frames*np.hanning(1024),axis=1))**2
    tonal = float(np.mean(np.max(spectra,axis=1)/np.maximum(spectra.sum(axis=1),1e-12) > .35))
    event_evidence=audio_events(audio)
    # Embeddings are speaker-change evidence, never trusted identity/diarization.
    vectors = [];speaker_sections=[]
    for start in range(0,max(1,len(audio)-2*16000),3*16000):
        end = min(start+3*16000,len(audio))
        if end-start >= 2*16000 and float(np.mean(probs[start//512:(end+511)//512] >= .5)) > .45:
            e=embedding(audio[start:end])
            if e is not None:
                label=0
                if vectors and float(np.dot(vectors[-1],e))<.65: label=speaker_sections[-1]['hypothesis']+1
                elif speaker_sections:label=speaker_sections[-1]['hypothesis']
                vectors.append(e);speaker_sections.append({'start':start/16000,'end':end/16000,
                    'hypothesis':label,'verified':False,'method':'adjacent_campplus_cosine_heuristic'})
    adjacent = [float(np.dot(a,b)) for a,b in zip(vectors,vectors[1:])]
    multiple = bool(adjacent and min(adjacent) < .65)
    e = embedding(audio) if np.mean(mask) > .4 else None
    # Repetition in the amplitude envelope can arise from echo or natural rhythm.
    envelope = np.sqrt(np.mean(np.pad(audio,(0,(-len(audio))%160)).reshape(-1,160)**2,axis=1))
    centered = envelope-envelope.mean()
    correlations = []
    for lag in range(8,36):
        if len(centered)>lag and np.std(centered[:-lag])>1e-6 and np.std(centered[lag:])>1e-6:
            correlations.append(float(np.corrcoef(centered[:-lag],centered[lag:])[0,1]))
    echo_proxy=max(correlations,default=0)
    components={'speech_density':30*float(np.mean(mask)), 'vad_probability':20*float(np.mean(probs)),
        'snr_proxy':7.5 if snr is None else 15*float(np.clip(snr/30,0,1)),
        'rail_quality':15*max(0,1-saturation*100), 'intensity_regularity':10*max(0,1-steadiness),
        'reference_duration':10*max(0,1-abs(duration-9)/9),
        'music_penalty':-25*event_evidence['music_mean_score'], 'noise_penalty':-10*event_evidence['noise_mean_score'],
        'speaker_change_penalty':-25 if multiple else 0}
    return {'speech_seconds':float(np.sum(mask)*.032), 'speech_fraction':float(np.mean(mask)),
        'snr_estimate_db':snr,'snr_method':'speech/non-speech RMS proxy; not a calibrated SNR',
        'saturation_fraction':saturation,'rms_dbfs':float(20*np.log10(max(np.sqrt(np.mean(audio**2)),1e-9))),
        'intensity_cv':steadiness,'music_status':'suspected_music' if event_evidence['music_mean_score']>=.2 or event_evidence['music_frame_fraction']>=.2 else 'not_detected',
        'audio_events':event_evidence,'noise_status':'suspected_noise' if event_evidence['noise_mean_score']>=.2 else 'not_detected',
        'tonal_fraction':tonal,'echo_status':'suspected_repetition' if echo_proxy>.65 else 'unknown',
        'echo_proxy':echo_proxy,'distortion_status':'rail_saturation' if saturation>.001 else 'unknown',
        'speaker_status':'suspected_multiple' if multiple else 'unverified',
        'minimum_adjacent_speaker_cosine':min(adjacent) if adjacent else None,
        'speaker_sections':speaker_sections,'overlapping_speech_detection':'not_available',
        'clarity_status':'requires_transcription_and_listening','embedding':e.tolist() if e is not None else None,
        'score':round(float(np.clip(sum(components.values()),0,100)),2),'score_components':components,
        'score_version':2,'clone_fidelity_measured':False,'target_speaker_verified':False}

def analyze(path, manual=False):
    audio=read_clip(path); probs=probabilities(audio); duration=len(audio)/16000
    ranges=speech_ranges(probs,duration)
    clips=[]
    if manual: ranges=[[0,duration]]
    else:
        # Group adjacent phrases; retain separate clips instead of concatenating.
        grouped=[]
        for start,end in ranges:
            if grouped and start-grouped[-1][1]<1 and end-grouped[-1][0]<=12:
                grouped[-1][1]=end
            else: grouped.append([start,end])
        ranges=grouped
    for start,end in ranges:
        while end-start>=3:
            stop=min(end,start+12)
            if end-stop<3: stop=end if end-start<=15 else stop
            section=audio[int(start*16000):int(stop*16000)]
            pp=probs[int(start/.032):max(int(stop/.032),int(start/.032)+1)]
            m=metrics(section,pp)
            if manual or m['speech_seconds']>=1.5:
                clips.append({'start':start,'end':stop,'metrics':m})
            start=stop
    return {'duration':duration,'speech_seconds':float(np.sum(probs>=.5)*.032),
        'ranges':ranges,'candidates':clips,'vad':'silero_v5.1.2_onnx_cpu', 'memory':memory_snapshot()}

def transcribe(path):
    global asr_model
    audio=read_clip(path)
    if len(audio)>30*16000: raise ValueError('Transcribe selected reference clips up to 30 seconds')
    import whisper
    guard()
    if asr_model is None: asr_model=whisper.load_model(str(ROOT/'models/whisper/base.pt'),device='cpu')
    result=asr_model.transcribe(audio,language=None,fp16=False,word_timestamps=True,condition_on_previous_text=False)
    return {'text':result['text'].strip(),'language':result.get('language'), 'verified':False,
        'status':'requires_user_verification', 'segments':result['segments'], 'provider':'local_whisper_base_cpu',
        'memory':memory_snapshot()}

print(json.dumps({'ready':True,'memory':memory_snapshot(),'device':'cpu'}),flush=True)
for line in sys.stdin:
    started=time.perf_counter()
    try:
        command=json.loads(line); guard()
        value=transcribe(command['path']) if command['action']=='transcribe' else analyze(command['path'],command.get('manual',False))
        print(json.dumps({'ok':True,'result':value,'seconds':time.perf_counter()-started}),flush=True)
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc),'seconds':time.perf_counter()-started}),flush=True)
