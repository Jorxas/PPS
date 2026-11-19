"""Dependency-free interface; real engines are imported only by isolated workers."""
from abc import ABC, abstractmethod
from pathlib import Path

ENGINE_NAMES = {"chatterbox": "Chatterbox V3", "cosyvoice": "CosyVoice 3"}
REQUIRED = {
    "chatterbox": ["ve.pt", "t3_mtl23ls_v3.safetensors", "s3gen.pt", "grapheme_mtl_merged_expanded_v1.json", "conds.pt"],
    "cosyvoice": ["cosyvoice3.yaml", "llm.pt", "flow.pt", "hift.pt", "campplus.onnx", "speech_tokenizer_v3.onnx", "CosyVoice-BlankEN/config.json", "CosyVoice-BlankEN/model.safetensors", "CosyVoice-BlankEN/tokenizer_config.json", "CosyVoice-BlankEN/merges.txt", "CosyVoice-BlankEN/vocab.json"],
}


def capabilities(engine):
    return {"id": engine, "name": ENGINE_NAMES[engine], "languages": (["fr", "en", "de"] if engine == "chatterbox" else ["fr", "en"]),
            "voice_cloning": True, "word_timestamps": False,
            'context_conditioning':False,'ssml_native':False,
            'alignment_provider':'PPS offline CPU Whisper base; uncertainty gate required',
            'forced_alignment_max_window_seconds':30,
            "reference_transcription_required": engine == "cosyvoice",
            "options": ({"exaggeration": [0, 1], "cfg_weight": [0, 1], "temperature": [.1, 1.5]}
                        if engine == "chatterbox" else {"speed": [.5, 2]}),
            "checkpoint": "v3" if engine == "chatterbox" else "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"}


def status(engine, cfg):
    import json
    path = Path(cfg[engine]["model"])
    missing = [f for f in REQUIRED[engine] if not (path / f).is_file()]
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    env_marker = Path(cfg["root"]) / "reports" / (engine + "-imports.json")
    imports = json.loads(env_marker.read_text(encoding="utf-8")) if env_marker.exists() else None
    validation_path = Path(cfg["root"]) / "reports" / "phase1_1" / "engine-validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8")).get(engine) if validation_path.exists() else None
    return {**capabilities(engine), "python_available": Path(cfg[engine]["python"]).is_file(),
            "imports_verified": bool(imports and imports.get("ok")),
            "import_diagnostic": {k:v for k,v in imports.items() if k not in ("packages", "traceback")} if imports else None,
            "model_files_available": not missing, "missing_model_files": missing,
            "model_revision": manifest.get("revision") if manifest else None,
            "ready_to_attempt_load": not missing and bool(imports and imports.get("ok")),
            "actual_validation": validation,
            "validation": "See reports/PPS_PHASE1_1_REPORT.md for actual load and synthesis tests" if validation else "See reports/ENGINE_STATUS.md for actual load and synthesis tests"}


class Adapter(ABC):
    def import_dependencies(self):
        """Import modules without instantiating models, before the worker guard."""
        pass
    @abstractmethod
    def capabilities(self): ...
    @abstractmethod
    def load(self): ...
    @abstractmethod
    def prepare_voice(self, reference, options): ...
    @abstractmethod
    def synthesize(self, text, language, options): ...
    def unload(self):
        self.model = None


class ChatterboxAdapter(Adapter):
    def __init__(self, cfg):
        self.cfg, self.model = cfg, None

    def capabilities(self):
        return capabilities("chatterbox")

    def import_dependencies(self):
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        self.factory = ChatterboxMultilingualTTS

    def load(self):
        if not hasattr(self, "factory"):
            self.import_dependencies()
        self.model = self.factory.from_local(self.cfg["model"], device="cuda", t3_model="v3")

    def prepare_voice(self, reference, options):
        if reference:
            self.model.prepare_conditionals(reference["chatterbox"], exaggeration=options.get("exaggeration", .5))

    def synthesize(self, text, language, options):
        return self.model.generate(text, language_id=language, **options), self.model.sr


class CosyVoiceAdapter(Adapter):
    def __init__(self, cfg):
        self.cfg, self.model, self.reference = cfg, None, None

    def capabilities(self):
        return capabilities("cosyvoice")

    def import_dependencies(self):
        from cosyvoice.cli.cosyvoice import AutoModel
        import importlib
        import re
        yaml = (Path(self.cfg["model"]) / "cosyvoice3.yaml").read_text(encoding="utf-8")
        for name in sorted(set(re.findall(r"!(?:new:|name:|apply:)([\w.]+)", yaml))):
            module, _, attribute = name.rpartition(".")
            getattr(importlib.import_module(module), attribute)
        self.factory = AutoModel

    def load(self):
        if not hasattr(self, "factory"):
            self.import_dependencies()
        # CosyVoice3 has no load_jit parameter. No TRT/vLLM/RL fallback.
        self.model = self.factory(model_dir=self.cfg["model"], load_trt=False, load_vllm=False, fp16=False)

    def prepare_voice(self, reference, options):
        if not reference or not reference["transcription"]:
            raise ValueError("CosyVoice3 requires an authorized reference with corrected transcription")
        self.reference = reference
        # Bound the conditioning cache to one authorized voice per persistent worker.
        self.model.frontend.spk2info.clear()
        self.model.add_zero_shot_spk("You are a helpful assistant.<|endofprompt|>" + reference["transcription"],
                                     reference["cosyvoice"], reference["id"])

    def synthesize(self, text, language, options):
        import torch
        chunks = [x["tts_speech"] for x in self.model.inference_zero_shot(
            text, "", "", zero_shot_spk_id=self.reference["id"], stream=False,
            speed=options.get("speed", 1), text_frontend=False)]
        if not chunks:
            raise ValueError("Empty CosyVoice output")
        return torch.cat(chunks, dim=1), self.model.sample_rate
