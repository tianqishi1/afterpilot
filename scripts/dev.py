"""One-command local dev stack: mock-shop(8001) + api/web(8000) + worker. Logs/pids in var/."""
import json
import os
import pathlib
import subprocess
import sys
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
VAR = ROOT / "var"
VAR.mkdir(exist_ok=True)
env = {**os.environ, "PYTHONPATH": str(ROOT)}
env.setdefault("DATABASE_URL", "sqlite:///" + (VAR / "dev.db").as_posix())
env.setdefault("MODEL_PROVIDER", "mock")
SERVICES = [
    ("shop", ["-m", "uvicorn", "services.mock_shop.main:app", "--host", "127.0.0.1", "--port", "8001"]),
    ("api", ["-m", "uvicorn", "apps.api.main:app", "--host", "127.0.0.1", "--port", "8000"]),
    ("worker", ["-m", "apps.worker.main", "--workers", "2"]),
]
pids = {}
for name, args in SERVICES:
    out = open(VAR / f"{name}.log", "ab")
    err = open(VAR / f"{name}.err.log", "ab")
    p = subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env, stdout=out, stderr=err)
    pids[name] = p.pid
    print(f"started {name} pid={p.pid}")
(VAR / "pids.json").write_text(json.dumps(pids), encoding="utf-8")
for _ in range(60):
    try:
        httpx.get("http://127.0.0.1:8000/health", timeout=1)
        break
    except Exception:  # noqa: BLE001
        time.sleep(0.5)
print("console:  http://127.0.0.1:8000/")
print("metrics:  http://127.0.0.1:8000/metrics")
print("shop:     http://127.0.0.1:8001/health")
print("stop:     python scripts/stop.py")