"""Central configuration. Everything overridable by env for dev/prod parity."""
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
VAR = ROOT / "var"
VAR.mkdir(exist_ok=True)


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


DATABASE_URL = env("DATABASE_URL", f"sqlite:///{(VAR / 'dev.db').as_posix()}")
MOCK_SHOP_URL = env("MOCK_SHOP_URL", "http://127.0.0.1:8001")

# Model routing: triage/compliance cheap tier, main decision mid tier, escalation strong tier.
_dash = bool(env("DASHSCOPE_API_KEY"))
MODEL_PROVIDER = env("MODEL_PROVIDER", "mock")  # mock | openai
OPENAI_BASE_URL = env(
    "OPENAI_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1" if _dash else "https://api.openai.com/v1",
)
OPENAI_API_KEY = env("DASHSCOPE_API_KEY") or env("OPENAI_API_KEY")
MODELS = {
    "triage": env("MODEL_TRIAGE", "qwen-flash" if _dash else "gpt-4o-mini"),
    "main": env("MODEL_MAIN", "qwen-plus" if _dash else "gpt-4o"),
    "escalation": env("MODEL_ESC", "qwen-max" if _dash else "gpt-4o"),
    "judge": env("MODEL_JUDGE", "qwen-plus" if _dash else "gpt-4o-mini"),
}
# CNY per 1k tokens (input, output). Configurable estimates, not billing truth.
PRICES = {
    "mock": (0.0, 0.0),
    "qwen-flash": (0.0003, 0.0006),
    "qwen-plus": (0.0008, 0.002),
    "qwen-max": (0.0024, 0.0096),
    "gpt-4o-mini": (0.0011, 0.0044),
    "gpt-4o": (0.018, 0.072),
}
HUMAN_COST_PER_MIN = float(env("HUMAN_COST_PER_MIN", "1.0"))
HANDOFF_EST_MIN = float(env("HANDOFF_EST_MIN", "6"))

MAX_STEPS = int(env("MAX_STEPS", "6"))
TOOL_TIMEOUT = float(env("TOOL_TIMEOUT", "2.0"))
TOOL_RETRIES = int(env("TOOL_RETRIES", "1"))
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN = 30.0
LEASE_SECONDS = int(env("LEASE_SECONDS", "60"))
MAX_JOB_ATTEMPTS = 5
WORKER_THREADS = int(env("WORKER_THREADS", "2"))
HTTP_TIMEOUT = float(env("HTTP_TIMEOUT", "10.0"))

DEFAULT_SPEC = env("DEFAULT_SPEC", "after_sales")
RELEASE_MODE = env("RELEASE_MODE", "ga")  # shadow | canary | ga
CANARY_PERCENT = int(env("CANARY_PERCENT", "100"))
EVAL_GATE_DROP_PP = float(env("EVAL_GATE_DROP_PP", "2.0"))