"""Test fixtures.

The environment is configured *before* the app module is imported so that
Settings picks up a two-key pool, a temporary SQLite file, and the in-memory
cache. The real provider call is monkeypatched to a deterministic echo so tests
run offline with no API keys.
"""
import os
import pathlib

# Configure the app via env BEFORE importing it.
_TEST_DIR = pathlib.Path(__file__).parent
_DB_PATH = _TEST_DIR / "test_data" / "app_test.db"
os.environ["LLM_API_KEYS"] = "secret-key-1,secret-key-2"
os.environ["ENABLE_FAILURE_SIMULATION"] = "true"
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH.as_posix()}"
os.environ["CACHE_TYPE"] = "local"
os.environ["LLM_FAKE_MODE"] = "false"
os.environ["ALLOWED_ORIGINS"] = "*"
os.environ["LITELLM_MODEL"] = "groq/openai/gpt-oss-20b"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import llm as llm_module  # noqa: E402
from app import main as app_main  # noqa: E402


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class _FakeResponse:
    def __init__(self, content, prompt_tokens, completion_tokens, model):
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage(prompt_tokens, completion_tokens)
        self.model = model


def _fake_call_provider(model, messages, api_key, timeout):
    """Deterministic stand-in for litellm.completion.

    Reply echoes the last user message; token counts are derived from content
    length so two equal-length first messages report equal input tokens.
    """
    last_user = messages[-1]["content"]
    reply = f"Echo: {last_user}"
    prompt_tokens = sum(len(m["content"]) for m in messages)
    completion_tokens = len(reply)
    return _FakeResponse(reply, prompt_tokens, completion_tokens, model)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(llm_module, "call_provider", _fake_call_provider)
    # Fresh DB + cache for every test.
    app_main.db.reset()
    app_main.gateway.clear_cache()
    app_main.settings.enable_failure_simulation = True
    with TestClient(app_main.app) as test_client:
        yield test_client
