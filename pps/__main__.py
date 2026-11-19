import socket
import uvicorn
from .api import create_app
from .config import load_config


def main():
    cfg = load_config()
    if cfg["service"]["host"] != "127.0.0.1":
        raise SystemExit("Phase 1 binds only to 127.0.0.1")
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", cfg["service"]["port"]))
    except OSError as e:
        raise SystemExit("PPS_PORT_BUSY: existing applications are not stopped or reassigned") from e
    # MainClip reuses keep-alive connections across its local acoustic checks, which can exceed uvicorn's 5 s
    # default while a render runs in parallel: the reused socket was already closed (ECONNRESET on a POST review,
    # 07/11/2026). Keep idle loopback connections open longer than any such gap. (Not part of MainClip's PPS
    # pipeline fingerprint, so cached narrations stay valid.)
    uvicorn.run(create_app(cfg), host="127.0.0.1", port=cfg["service"]["port"], workers=1, log_level="info", timeout_keep_alive=120)


if __name__ == "__main__":
    main()
