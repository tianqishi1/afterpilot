"""Test harness: isolated sqlite DB per session + in-process mock-shop on a free port.
Env is set BEFORE any packages.core import so config/engine bind to the test DB."""
import os
import pathlib
import socket
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEST_DB = ROOT / "var" / "test.db"
if TEST_DB.exists():
    TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["MODEL_PROVIDER"] = "mock"
os.environ["LEASE_SECONDS"] = "60"

import httpx  # noqa: E402
import pytest  # noqa: E402
import uvicorn  # noqa: E402

from packages.core import config  # noqa: E402
from packages.core.db import init_db, session_scope, engine  # noqa: E402
from packages.core.models import Base  # noqa: E402
from packages.core import kb  # noqa: E402
from packages.evals.runner import seed_datasets  # noqa: E402
from services.mock_shop.main import app as shop_app  # noqa: E402

_sock = socket.socket()
_sock.bind(("127.0.0.1", 0))
PORT = _sock.getsockname()[1]
_sock.close()
config.MOCK_SHOP_URL = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="session", autouse=True)
def harness():
    init_db()
    cfg = uvicorn.Config(shop_app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        try:
            httpx.get(config.MOCK_SHOP_URL + "/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    yield server
    server.should_exit = True
    t.join(timeout=5)
    engine.dispose()
    if TEST_DB.exists():
        TEST_DB.unlink()


@pytest.fixture(autouse=True)
def clean(harness):
    httpx.post(config.MOCK_SHOP_URL + "/admin/reset", timeout=5)
    httpx.post(config.MOCK_SHOP_URL + "/admin/fault", json={"mode": "none"}, timeout=5)
    with session_scope() as s:
        for table in reversed(Base.metadata.sorted_tables):
            s.execute(table.delete())
    kb.seed(config.ROOT / "agents" / "kb_seed.yaml")
    seed_datasets()
    yield