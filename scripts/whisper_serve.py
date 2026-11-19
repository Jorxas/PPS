"""Resident wrapper around the UNCHANGED whisper_json.py (same file, checkpoint and CPU settings).

Every request re-executes whisper_json.py with its own argv, exactly as a one-shot process would.
Only three process-level effects of a fresh interpreter are reproduced or kept:
- imports stay cached (interpreter, torch, whisper are paid once);
- the Whisper checkpoint loaded by the first successful request is reused while its file is
  unchanged (same path, device and stat), instead of being read again;
- the torch CPU generator is reset to its fresh-process seed before each request, so temperature
  fallback sampling sees the same random stream as a new process.
Protocol (ASCII JSON lines): stdin {"id", "argv"} -> stdout {"id", "returncode"}.
The wrapped script's own output never reaches the protocol channel (fd 1 is moved to stderr).
EOF on stdin (parent closed or died) ends the process.
"""
import json
import os
import runpy
import sys
import traceback
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("whisper_json.py")


def main():
    protocol = os.fdopen(os.dup(1), "w", encoding="ascii", newline="\n")
    os.dup2(2, 1)  # anything the script or a library writes to stdout lands in stderr instead
    sys.stdout = sys.stderr
    state = {"model": None, "key": None, "seed": None, "original_load": None}

    def checkpoint_key(name, device, download_root=None, in_memory=False):
        try:
            stat = os.stat(name)
        except OSError:
            return None
        return (os.path.abspath(name), str(device), download_root, bool(in_memory), stat.st_size, stat.st_mtime_ns)

    def remember(namespace):
        """After a successful run, keep its model and patch whisper.load_model to reuse it."""
        whisper = sys.modules.get("whisper")
        args, model = namespace.get("args"), namespace.get("model")
        if whisper is None or args is None or model is None:
            return
        key = checkpoint_key(args.model, "cpu")
        if key is None:
            return
        if state["original_load"] is None:
            state["original_load"] = whisper.load_model

            def load_model(name, device=None, download_root=None, in_memory=False):
                if state["model"] is not None and checkpoint_key(name, device, download_root, in_memory) == state["key"]:
                    return state["model"]
                return state["original_load"](name, device=device, download_root=download_root, in_memory=in_memory)
            whisper.load_model = load_model
        state["model"], state["key"] = model, key
        torch = sys.modules.get("torch")
        if torch is not None and state["seed"] is None:
            state["seed"] = torch.initial_seed()  # unchanged by consumption: the fresh-process seed

    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return
        request = json.loads(line.decode("ascii"))
        saved_argv, saved_path = sys.argv, list(sys.path)
        torch = sys.modules.get("torch")
        if torch is not None and state["seed"] is not None:
            torch.manual_seed(state["seed"])
        code, namespace = 0, None
        try:
            sys.argv = [str(SCRIPT), *request["argv"]]
            namespace = runpy.run_path(str(SCRIPT), run_name="__main__")
        except SystemExit as e:
            # Same exit status a one-shot interpreter would report for SystemExit.
            if e.code is None:
                code = 0
            elif isinstance(e.code, int):
                code = e.code
            else:
                print(e.code, file=sys.stderr)
                code = 1
        except BaseException:
            traceback.print_exc()
            code = 1
        finally:
            sys.argv, sys.path[:] = saved_argv, saved_path
            sys.stderr.flush()
        if code == 0 and namespace is not None:
            remember(namespace)
        protocol.write(json.dumps({"id": request["id"], "returncode": code}) + "\n")
        protocol.flush()


if __name__ == "__main__":
    main()
