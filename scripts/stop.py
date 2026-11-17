"""Stop the dev stack started by scripts/dev.py (pids recorded in var/pids.json)."""
import json
import os
import pathlib
import signal

VAR = pathlib.Path(__file__).resolve().parents[1] / "var"
f = VAR / "pids.json"
if not f.exists():
    print("no pids file")
    raise SystemExit(0)
pids = json.loads(f.read_text(encoding="utf-8"))
for name, pid in pids.items():
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"stopped {name} pid={pid}")
    except OSError as exc:
        print(f"{name} pid={pid}: {exc}")
f.unlink()