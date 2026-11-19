import os
from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parent.parent


def load_config(path=None):
    path = Path(path or os.environ.get("PPS_CONFIG", ROOT / "config.toml")).resolve()
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    cfg["root"] = str(ROOT)
    cfg["config_file"] = str(path)
    for key, value in cfg["paths"].items():
        cfg["paths"][key] = str((path.parent / value).resolve())
        Path(cfg["paths"][key]).mkdir(parents=True, exist_ok=True)
    for engine in ("chatterbox", "cosyvoice"):
        for key in ("python", "source", "model"):
            cfg[engine][key] = str((path.parent / cfg[engine][key]).resolve())
    cfg["alignment"]["model_dir"] = str((path.parent / cfg["alignment"]["model_dir"]).resolve())
    if cfg["alignment"].get("script"):
        cfg["alignment"]["command"] = str((path.parent / cfg["alignment"]["command"]).resolve())
        cfg["alignment"]["script"] = str((path.parent / cfg["alignment"]["script"]).resolve())
    return cfg
