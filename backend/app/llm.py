"""LiteLLM gateway: key-pool failover, response caching, token tracking.

Design notes
------------
* **Failover** is an explicit loop over the configured key pool. For each key we
  call ``litellm.completion`` with that key's secret. Any provider failure (429,
  quota, 5xx, timeouts, connection/auth errors) is caught and we advance to the
  next key. This gives precise control over ``key_id`` and ``fallback_attempts``
  and lets the X-Simulate-Failure hook inject per-key failures.
* **Caching** uses LiteLLM's built-in cache (``litellm.Cache``) as the storage
  backend. The cache key is ``model + full message list`` (system prompt +
  history + new message), exactly as the contract specifies, so the same first
  message in a new session returns LLM_CACHE the second time while a different
  message never does.
* **Tokens** come from the provider's usage for the request (prompt vs
  completion). Cache hits return the tokens stored with the cached response.
"""
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import litellm
from litellm import Cache

log = logging.getLogger("llm")

# Keep LiteLLM quiet and resilient: no internal retries (we handle failover),
# drop unsupported params rather than erroring, no telemetry.
litellm.drop_params = True
litellm.suppress_debug_info = True
litellm.telemetry = False


class AllProvidersUnavailable(Exception):
    """Raised when every key in the pool failed for a request -> HTTP 503."""


class SimulatedProviderError(Exception):
    """A failure injected by the X-Simulate-Failure test hook."""

    def __init__(self, status: int) -> None:
        self.status_code = status
        super().__init__(f"simulated provider error {status}")


@dataclass
class Result:
    source: str  # LIVE_INFERENCE or LLM_CACHE
    model: str
    reply: str
    input_tokens: int
    output_tokens: int
    key_id: Optional[str]
    fallback_attempts: int
    latency_ms: int


def _estimate_tokens(text: str) -> int:
    """Cheap offline token estimate (~4 chars/token). Only used as a fallback."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def call_provider(model: str, messages: List[Dict], api_key: str, timeout: float):
    """Single provider call through LiteLLM.

    Kept at module scope so tests can monkeypatch it without hitting the network.
    """
    return litellm.completion(
        model=model,
        messages=messages,
        api_key=api_key,
        timeout=timeout,
        num_retries=0,
    )


class LLMGateway:
    def __init__(self, settings) -> None:
        self.settings = settings
        self._init_cache()

    # ----- caching ---------------------------------------------------------
    def _init_cache(self) -> None:
        try:
            if self.settings.cache_type == "disk":
                os.makedirs(self.settings.cache_dir, exist_ok=True)
                litellm.cache = Cache(type="disk", disk_cache_dir=self.settings.cache_dir)
            else:
                litellm.cache = Cache(type="local")
        except Exception:  # pragma: no cover - fall back to in-memory
            litellm.cache = Cache(type="local")

    @property
    def cache_enabled(self) -> bool:
        return litellm.cache is not None

    def _backend(self):
        # The concrete storage backend (InMemoryCache / DiskCache) behind the
        # LiteLLM Cache object. Both expose get_cache(key) / set_cache(key, val).
        return litellm.cache.cache

    def _cache_get(self, key: str) -> Optional[Dict]:
        try:
            return self._backend().get_cache(key)
        except Exception:
            return None

    def _cache_set(self, key: str, value: Dict) -> None:
        try:
            self._backend().set_cache(key, value)
        except Exception:
            pass

    def clear_cache(self) -> None:
        """Reset the cache (used by the test suite)."""
        self._init_cache()

    def _cache_key(self, model: str, messages: List[Dict]) -> str:
        payload = json.dumps(
            {"model": model, "messages": messages}, sort_keys=True, ensure_ascii=False
        )
        return "llmchat:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ----- message assembly -----------------------------------------------
    def _build_messages(
        self, system_prompt: Optional[str], history: List[Dict], user_message: str
    ) -> List[Dict]:
        messages: List[Dict] = []
        if system_prompt and system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})

        limit = self.settings.max_history_messages
        limited = history[-limit:] if limit and limit > 0 else history
        for item in limited:
            role = item.get("role")
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": item.get("content", "")})

        messages.append({"role": "user", "content": user_message})
        return messages

    # ----- token counting --------------------------------------------------
    def _count(
        self, model: str, messages: Optional[List[Dict]] = None, text: Optional[str] = None
    ) -> int:
        try:
            if messages is not None:
                return int(litellm.token_counter(model=model, messages=messages))
            return int(litellm.token_counter(model=model, text=text or ""))
        except Exception:
            if messages is not None:
                return sum(_estimate_tokens(m.get("content", "")) for m in messages)
            return _estimate_tokens(text or "")

    def _fake(
        self, messages: List[Dict], user_message: str
    ) -> Tuple[str, int, int]:
        reply = f"[FAKE MODE] Echo: {user_message}"
        input_tokens = sum(_estimate_tokens(m.get("content", "")) for m in messages)
        output_tokens = _estimate_tokens(reply)
        return reply, input_tokens, output_tokens

    # ----- main entry point -----------------------------------------------
    def resolve(
        self,
        system_prompt: Optional[str],
        history: List[Dict],
        user_message: str,
        sim_map: Dict[str, int],
    ) -> Result:
        """Produce a reply for one /chat request.

        Returns a Result on success. Raises AllProvidersUnavailable if every key
        fails. A request with an active failure simulation bypasses the cache so
        the failover path is always exercised "for this request only".
        """
        messages = self._build_messages(system_prompt, history, user_message)
        model = self.settings.default_model
        cache_key = self._cache_key(model, messages)
        simulation_active = bool(sim_map)
        start = time.perf_counter()

        # 1) Cache lookup (skipped when a failure is being simulated).
        if not simulation_active:
            cached = self._cache_get(cache_key)
            if cached:
                latency_ms = max(0, int((time.perf_counter() - start) * 1000))
                return Result(
                    source="LLM_CACHE",
                    model=cached["model"],
                    reply=cached["reply"],
                    input_tokens=int(cached["input_tokens"]),
                    output_tokens=int(cached["output_tokens"]),
                    key_id=None,
                    fallback_attempts=0,
                    latency_ms=latency_ms,
                )

        # 2) Live inference with automatic failover across the key pool.
        fallback_attempts = 0
        for entry in self.settings.keys:
            sim_status = sim_map.get(entry.key_id)
            if sim_status is None:
                sim_status = sim_map.get("*")
            try:
                if sim_status is not None:
                    raise SimulatedProviderError(sim_status)

                if self.settings.fake_mode:
                    reply, input_tokens, output_tokens = self._fake(messages, user_message)
                else:
                    response = call_provider(
                        entry.model, messages, entry.secret, self.settings.request_timeout
                    )
                    reply = response.choices[0].message.content or ""
                    usage = getattr(response, "usage", None)
                    input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
                    output_tokens = getattr(usage, "completion_tokens", None) if usage else None
                    if input_tokens is None:
                        input_tokens = self._count(entry.model, messages=messages)
                    if output_tokens is None:
                        output_tokens = self._count(entry.model, text=reply)

                input_tokens = int(input_tokens)
                output_tokens = int(output_tokens)
                latency_ms = max(0, int((time.perf_counter() - start) * 1000))

                # Store in cache for future identical requests (not under sim).
                if not simulation_active:
                    self._cache_set(
                        cache_key,
                        {
                            "reply": reply,
                            "model": entry.model,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        },
                    )

                return Result(
                    source="LIVE_INFERENCE",
                    model=entry.model,
                    reply=reply,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    key_id=entry.key_id,
                    fallback_attempts=fallback_attempts,
                    latency_ms=latency_ms,
                )
            except Exception as exc:  # noqa: BLE001 - any provider failure -> failover
                fallback_attempts += 1
                log.warning(
                    "key %s failed (%s): %s", entry.key_id, type(exc).__name__, exc
                )
                continue

        # 3) Every key failed.
        raise AllProvidersUnavailable()
