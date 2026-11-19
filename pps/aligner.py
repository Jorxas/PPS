"""Resident CPU Whisper aligner, kept between jobs; the one-shot CLI stays the reference behaviour.

The resident process (scripts/whisper_serve.py) re-executes the unchanged whisper_json.py for every
request, with the same interpreter, flags, argv, checkpoint and CPU settings as the one-shot command.
It only saves interpreter start-up, imports and checkpoint loading. Any doubt (script failure,
crash, protocol error) stops the resident process; a crash is retried once on a fresh process, and
otherwise the request runs through the original one-shot subprocess.
"""
import itertools
import json
import queue
import subprocess
import threading
import time
from pathlib import Path
import psutil
from . import alignment


class AlignerCrashed(Exception):
    pass


class ResidentAligner:
    MAX_CONSECUTIVE_FALLBACKS = 2
    FALLBACK_COOLDOWN_SECONDS = 600

    def __init__(self, cfg):
        self.cfg = cfg
        self.process, self.lines, self.log = None, None, None
        self.lock = threading.Lock()  # one request at a time, like the serial one-shot calls
        self.ids = itertools.count(1)
        self.closed = False
        self.disabled_until = 0.0  # after repeated resident failures, one-shot only for a cooldown
        self.consecutive_fallbacks = 0
        self.busy = False
        self.last_used = time.monotonic()
        self.last_mode = None
        self.stats = {"resident_requests": 0, "one_shot_requests": 0, "restarts": 0, "starts": 0}

    @property
    def acfg(self):
        return self.cfg["alignment"]

    def wrapper(self):
        script = self.acfg.get("script")
        return Path(script).with_name("whisper_serve.py") if script else None

    def prefix(self):
        return [self.acfg["command"], "-B", self.acfg["script"]]

    def enabled(self):
        wrapper = self.wrapper()
        return (bool(self.cfg["service"].get("resident_aligner", True)) and bool(self.acfg.get("command"))
                and wrapper is not None and wrapper.is_file() and not self.closed
                and time.monotonic() >= self.disabled_until)

    def __call__(self, args, timeout):
        """Same contract as run_one_shot(args, timeout): returns on success, raises SubprocessError otherwise."""
        args = [str(a) for a in args]
        if self.closed:
            raise subprocess.SubprocessError("Aligneur résident arrêté avec le service.")
        if not self.enabled() or args[:3] != [str(x) for x in self.prefix()]:
            return self._one_shot(args, timeout, "one_shot")
        with self.lock:
            self.busy = True
            try:
                for attempt in (0, 1):
                    try:
                        code = self._request(args[3:], timeout)
                    except AlignerCrashed:
                        self._stop()
                        if self.closed:
                            raise subprocess.SubprocessError("Aligneur résident arrêté avec le service.")
                        if attempt == 0:
                            self.stats["restarts"] += 1
                            continue  # restart once on a fresh process
                        break
                    except subprocess.TimeoutExpired:
                        self._stop()
                        self.last_mode = "resident"
                        raise subprocess.TimeoutExpired(args, timeout)  # same failure as the one-shot call
                    if code == 0:
                        self.consecutive_fallbacks = 0
                        self.stats["resident_requests"] += 1
                        self.last_mode = "resident"
                        return subprocess.CompletedProcess(args, 0)
                    self._stop()  # script-level failure: state is suspect, let the reference path decide
                    break
                self.consecutive_fallbacks += 1
                if self.consecutive_fallbacks >= self.MAX_CONSECUTIVE_FALLBACKS:
                    self.disabled_until = time.monotonic() + self.FALLBACK_COOLDOWN_SECONDS
                    self.consecutive_fallbacks = 0
                return self._one_shot(args, timeout, "resident_fallback_one_shot")
            finally:
                self.busy = False
                self.last_used = time.monotonic()

    def _one_shot(self, args, timeout, mode):
        self.stats["one_shot_requests"] += 1
        self.last_mode = mode
        return alignment.run_one_shot(args, timeout)

    def _start(self):
        logs = Path(self.cfg["paths"]["logs"])
        self.log = open(logs / "aligner-resident.log", "a", encoding="utf-8")
        self.log.write(f"--- start {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        self.log.flush()
        # Same interpreter, flags, cwd and environment as the one-shot subprocess.run call.
        self.process = subprocess.Popen([self.acfg["command"], "-B", str(self.wrapper())],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.lines = queue.Queue()
        stdout, lines = self.process.stdout, self.lines

        def read():
            for raw in iter(stdout.readline, b""):
                lines.put(raw)
            lines.put(None)
        threading.Thread(target=read, daemon=True, name="pps-aligner-reader").start()
        self.stats["starts"] += 1

    def _request(self, argv, timeout):
        if self.process is None or self.process.poll() is not None:
            self._stop()
            self._start()
        identifier = next(self.ids)
        try:
            self.process.stdin.write((json.dumps({"id": identifier, "argv": argv}) + "\n").encode("ascii"))
            self.process.stdin.flush()
        except (OSError, ValueError) as e:
            raise AlignerCrashed(str(e)) from e
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            try:
                raw = self.lines.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if self.closed:
                    raise AlignerCrashed("closed")
                continue
            if raw is None:
                raise AlignerCrashed("resident aligner exited")
            try:
                reply = json.loads(raw.decode("ascii"))
            except (UnicodeDecodeError, ValueError) as e:
                raise AlignerCrashed("protocol error") from e
            if reply.get("id") == identifier:
                return int(reply["returncode"])

    def _stop(self):
        process, self.process = self.process, None
        if process is not None:
            try:
                if process.poll() is None:
                    try:
                        process.stdin.close()  # EOF: the wrapper returns after its current request
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        # A venv python.exe may be a launcher whose child is the real interpreter.
                        try:
                            owner = psutil.Process(process.pid)
                            owned = owner.children(recursive=True) + [owner]
                        except psutil.NoSuchProcess:
                            owned = []
                        for proc in owned:
                            try:
                                proc.kill()
                            except psutil.NoSuchProcess:
                                pass
                        psutil.wait_procs(owned, timeout=5)
            finally:
                for stream in (process.stdin, process.stdout):
                    try:
                        if stream:
                            stream.close()
                    except OSError:
                        pass
        if self.log:
            self.log.close()
            self.log = None

    def stop_if_idle(self, idle_seconds, force=False):
        """Release the resident process (its RAM) when idle; it restarts lazily on the next job."""
        if self.process is None or self.busy:
            return
        if force or time.monotonic() - self.last_used > idle_seconds:
            if self.lock.acquire(blocking=False):
                try:
                    if not self.busy:
                        self._stop()
                finally:
                    self.lock.release()

    def close(self):
        self.closed = True
        process = self.process
        if process is not None and self.busy:
            # Shutdown: do not wait for an in-flight request; its job is cancelled by the stop flag.
            try:
                owner = psutil.Process(process.pid)
                for proc in owner.children(recursive=True) + [owner]:
                    try:
                        proc.kill()
                    except psutil.NoSuchProcess:
                        pass
            except psutil.NoSuchProcess:
                pass
        with self.lock:
            self._stop()
