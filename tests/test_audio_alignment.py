from pathlib import Path
from pps.audio import export, pcm_metadata
from pps.alignment import evaluate, tokens
from pps.resources import guard
from pps.errors import PPSError
from conftest import tone_bytes


def test_pcm_and_mp3_decode(tmp_path):
    raw = tmp_path / "raw.wav"
    raw.write_bytes(tone_bytes(.5))
    output = export(raw, tmp_path / "exports")
    assert output["pcm"]["frames"] == 12000
    assert output["mp3_decoded_pcm"]["sample_rate"] == 44100
    assert abs(output["mp3_decoded_pcm"]["duration_seconds"] - .5) < .002


def test_alignment_not_exact_even_full_coverage():
    a = evaluate("Bonjour à tous.", [{"word":"Bonjour", "start":0, "end":.2}, {"word":"à", "start":.2, "end":.3}, {"word":"tous", "start":.3, "end":.5}], .5)
    assert a["script_coverage"] == 1
    assert a["status"] == "asr_unverified"
    assert not a["mainclip_compatible"] and not a["exact"]
    assert a["interpolated_word_count"] == 0


def test_incomplete_and_invalid_alignment():
    a = evaluate("Bonjour à tous.", [{"word":"Bonjour", "start":0, "end":.2}], 1)
    assert a["status"] == "incomplete" and a["script_coverage"] < 1
    b = evaluate("Bonjour", [{"word":"Bonjour", "start":1, "end":3}], 1)
    assert not b["timestamps_valid"] and b["status"] == "incomplete"
    assert tokens("L'été, c’est français !") == ["l'été", "c’est", "français"]


def test_real_asr_orthographic_difference_stays_explicit():
    result = evaluate("vocale locale", [{"word":"vocale", "start":0,"end":.2},
                                        {"word":"local", "start":.2,"end":.4}], .5)
    assert result["unmatched_script_words"] == ["locale"]
    assert result["unmatched_recognized_words"] == ["local"]
    assert result["word_differences"][0]["operation"] == "replace"
    assert result["status"] == "incomplete" and result["interpolated_word_count"] == 0
    assert not result["mainclip_compatible"]


def test_forced_coverage_does_not_prove_valid_timing(cfg, tmp_path, monkeypatch):
    import json
    from pps.alignment import align
    audio = tmp_path / "master.wav"
    audio.write_bytes(tone_bytes(.5))
    (tmp_path / "base.pt").touch()
    cfg["alignment"].update(command="private-python", script="local-asr.py", model_dir=str(tmp_path))
    def execute(args, **kwargs):
        assert (tmp_path / "alignment-script.txt").read_text(encoding="utf-8") == "Bonjour"
        (tmp_path / "master.json").write_text(json.dumps({"segments":[{"words":[{"word":"Bonjour", "start":0,"end":.4}]}],
            "pps_forced_alignment":{"words":[{"word":"Bonjour","start":0,"end":2}],
                "provenance":"whisper_cross_attention_dtw", "note":"Imposed text is not acoustic proof"}}))
    monkeypatch.setattr("pps.alignment.subprocess.run", execute)
    result = align(audio, "Bonjour", "fr", .5, cfg["alignment"], tmp_path)
    assert result["forced_alignment"]["script_coverage"] == 1
    assert result["forced_alignment"]["status"] == "failed"
    assert not result["forced_alignment"]["mainclip_compatible"]
    assert result["status"] == "asr_unverified"


def test_guard_blocks_before_launch(cfg, monkeypatch):
    monkeypatch.setattr("pps.resources.snapshot", lambda p:{"ram_available_gib":1, "disk_free_gib":100, "gpu":{"free_gib":7}})
    try:
        guard(cfg)
        assert False
    except PPSError as e:
        assert e.code == "RESOURCE_BUSY" and e.retryable


def test_alignment_script_receives_final_wav_and_local_checkpoint(cfg, tmp_path, monkeypatch):
    import json
    from pps.alignment import align
    audio = tmp_path / "master.wav"
    audio.write_bytes(tone_bytes(.5))
    checkpoint = tmp_path / "base.pt"
    checkpoint.touch()
    cfg["alignment"].update(command="private-python", script="local-asr.py", model="base", model_dir=str(tmp_path))
    def execute(args, **kwargs):
        assert args[:4] == ["private-python", "-B", "local-asr.py", str(audio)]
        assert args[args.index("--model")+1] == str(checkpoint)
        assert args[args.index("--device")+1] == "cpu"
        (tmp_path / "master.json").write_text(json.dumps({"segments":[{"words":[{"word":"Bonjour", "start":0,"end":.4}]}]}))
    monkeypatch.setattr("pps.alignment.subprocess.run", execute)
    result = align(audio, "Bonjour", "fr", .5, cfg["alignment"], tmp_path)
    assert result["status"] == "asr_unverified" and result["script_coverage"] == 1
    assert not result["mainclip_compatible"]
