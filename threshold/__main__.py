"""python -m threshold --mode test (no implicit LIVE fallback)."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .app import Config, create_app


def main():
    parser = argparse.ArgumentParser(description="Threshold: local CSI motion-near-entrance prototype")
    parser.add_argument("--mode", choices=("test", "live", "replay"), default="test")
    parser.add_argument("--port", dest="serial_port", help="Exact USB serial device; LIVE only")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--tx-mac", dest="transmitter_mac", default="02:ca:5c:ad:1a:01")
    parser.add_argument("--channel", type=int, default=6)
    parser.add_argument("--replay", dest="replay_path", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=8765)
    parser.add_argument("--allow-lan", action="store_true")
    parser.add_argument("--no-record", dest="recording", action="store_false")
    parser.add_argument("--area-name", default="Front entrance")
    args = parser.parse_args()
    try:
        config = Config(**vars(args))
        app = create_app(config)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f"Mode: {config.mode.upper()}. Activity threshold is an unvalidated tuning hypothesis.", file=sys.stderr)
    print(f"Control token file (read locally; never share): {config.root / '.local' / 'control-token'}", file=sys.stderr)
    if config.allow_lan:
        print("WARNING: LAN HTTP is not confidential. Prefer a personal hotspot. Auth remains required.", file=sys.stderr)
    import uvicorn
    uvicorn.run(app, host=config.host, port=config.http_port, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
