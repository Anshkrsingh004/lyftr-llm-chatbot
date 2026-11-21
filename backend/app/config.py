"""Application configuration, loaded entirely from environment variables.

Real secrets never live in the repo. `.env.example` documents every variable.
"""
import os
from dataclasses import dataclass
from typing import List

try:
    # Convenience for running locally without Docker: load a .env if present.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


def _split(value: str) -> List[str]:
    """Split a comma-separated env value into a clean list (drops blanks)."""
    return [part.strip() for part in value.split(",") if part.strip()]


@dataclass
class KeyEntry:
    """One entry in the API key pool.

    key_id is a stable, non-secret identifier (key_1, key_2, ...) derived from
    the position of the key in LLM_API_KEYS. The secret is never exposed.
    """

    key_id: str
    secret: str
    model: str


class Settings:
    """Runtime settings parsed from the environment once at startup."""

    def __init__(self) -> None:
        default_model = os.getenv("LITELLM_MODEL", "groq/openai/gpt-oss-20b").strip()

        key_secrets = _split(os.getenv("LLM_API_KEYS", ""))
        # Optional parallel list of per-key models; falls back to default_model.
        models = _split(os.getenv("LLM_MODELS", ""))

        self.keys: List[KeyEntry] = []
        for index, secret in enumerate(key_secrets):
            model = models[index] if index < len(models) else default_model
            self.keys.append(KeyEntry(key_id=f"key_{index + 1}", secret=secret, model=model))

        self.default_model = default_model
        self.database_url = os.getenv("DATABASE_URL", "sqlite:////data/app.db")
        self.allowed_origins = _split(os.getenv("ALLOWED_ORIGINS", "")) or ["*"]
        self.enable_failure_simulation = (
            os.getenv("ENABLE_FAILURE_SIMULATION", "false").strip().lower() == "true"
        )
        self.default_system_prompt = os.getenv(
            "DEFAULT_SYSTEM_PROMPT", "You are a helpful assistant."
        )
        # Cache backend: "local" (in-memory) or "disk" (persisted under CACHE_DIR).
        self.cache_type = os.getenv("CACHE_TYPE", "local").strip().lower()
        self.cache_dir = os.getenv("CACHE_DIR", "/data/litellm_cache")
        self.request_timeout = float(os.getenv("LLM_REQUEST_TIMEOUT", "30"))
        # Dev/offline convenience: echo replies instead of calling a real provider.
        self.fake_mode = os.getenv("LLM_FAKE_MODE", "false").strip().lower() == "true"
        self.max_history_messages = int(os.getenv("MAX_HISTORY_MESSAGES", "40"))
