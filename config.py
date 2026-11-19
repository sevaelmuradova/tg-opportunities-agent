import os
from pathlib import Path

REQUEST_TIMEOUT = 60.0  # per attempt; a stuck model falls back to the next one instead of waiting longer


def _load_dotenv(path: Path = Path(__file__).parent / ".env") -> None:
    """Load KEY=VALUE lines from .env into os.environ (real env vars take precedence)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()

# Tried in order: the best model first, lighter fallbacks when it is overloaded or out of quota.
# Override with e.g. GEMINI_MODELS=gemini-3.5-flash-lite (comma-separated) in .env.
GEMINI_MODELS = [
    m.strip()
    for m in os.environ.get(
        "GEMINI_MODELS", "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    ).split(",")
    if m.strip()
]


def gemini_api_url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def get_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set (export it or add it to .env)")
    return key
