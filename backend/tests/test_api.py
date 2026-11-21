"""End-to-end tests for the backend contract (sections 3 and 4)."""


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["keys_configured"] == 2
    assert body["cache_enabled"] is True


# ---- Validation (422) -----------------------------------------------------

def test_missing_fields_returns_422(client):
    assert client.post("/chat", json={}).status_code == 422


def test_empty_message_returns_422(client):
    res = client.post("/chat", json={"session_id": "s1", "message": "   "})
    assert res.status_code == 422


def test_empty_session_id_returns_422(client):
    res = client.post("/chat", json={"session_id": "", "message": "hi"})
    assert res.status_code == 422


def test_message_too_long_returns_422(client):
    res = client.post("/chat", json={"session_id": "s1", "message": "x" * 4001})
    assert res.status_code == 422


def test_failed_validation_not_stored(client):
    client.post("/chat", json={"session_id": "s-novalid", "message": "   "})
    res = client.get("/sessions/s-novalid/messages")
    assert res.status_code == 404


# ---- Live inference + metadata shape -------------------------------------

def test_live_inference_shape(client):
    res = client.post("/chat", json={"session_id": "s-live", "message": "Hello"})
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == "s-live"
    assert body["reply"] == "Echo: Hello"
    md = body["metadata"]
    assert md["resolution_source"] == "LIVE_INFERENCE"
    assert md["model"] == "groq/openai/gpt-oss-20b"
    assert isinstance(md["latency_ms"], int)
    assert md["input_tokens"] > 0
    assert md["output_tokens"] > 0
    assert md["key_id"] == "key_1"
    assert md["fallback_attempts"] == 0


# ---- Caching --------------------------------------------------------------

def test_same_first_message_hits_cache_second_time(client):
    first = client.post("/chat", json={"session_id": "c-a", "message": "Capital of France?"}).json()
    second = client.post("/chat", json={"session_id": "c-b", "message": "Capital of France?"}).json()

    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    # Identical reply text and stored tokens on a cache hit.
    assert second["reply"] == first["reply"]
    assert second["metadata"]["input_tokens"] == first["metadata"]["input_tokens"]
    assert second["metadata"]["output_tokens"] == first["metadata"]["output_tokens"]
    # key_id is null on a cache hit, and a cache hit is not slower than the live call.
    assert second["metadata"]["key_id"] is None
    assert second["metadata"]["fallback_attempts"] == 0
    assert second["metadata"]["latency_ms"] <= first["metadata"]["latency_ms"]


def test_different_message_is_not_cached(client):
    client.post("/chat", json={"session_id": "d-a", "message": "First"})
    res = client.post("/chat", json={"session_id": "d-b", "message": "Second"}).json()
    assert res["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_equal_length_messages_report_similar_input_tokens(client):
    a = client.post("/chat", json={"session_id": "t-a", "message": "aaaaaaaa"}).json()
    b = client.post("/chat", json={"session_id": "t-b", "message": "bbbbbbbb"}).json()
    assert a["metadata"]["input_tokens"] == b["metadata"]["input_tokens"]


# ---- Multi-turn state + history endpoint ---------------------------------

def test_multi_turn_history(client):
    client.post("/chat", json={"session_id": "m1", "message": "My name is Bob"})
    client.post("/chat", json={"session_id": "m1", "message": "What is my name?"})

    res = client.get("/sessions/m1/messages")
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == "m1"
    msgs = body["messages"]
    assert len(msgs) == 4
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    # No system prompt leaks into history.
    assert all(m["role"] in ("user", "assistant") for m in msgs)
    # Assistant messages carry metadata; user messages do not.
    assert "metadata" in msgs[1]
    assert "metadata" not in msgs[0]


def test_sessions_separate(client):
    client.post("/chat", json={"session_id": "sep-1", "message": "one"})
    client.post("/chat", json={"session_id": "sep-2", "message": "two"})
    assert len(client.get("/sessions/sep-1/messages").json()["messages"]) == 2
    assert len(client.get("/sessions/sep-2/messages").json()["messages"]) == 2


def test_unknown_session_returns_404(client):
    res = client.get("/sessions/does-not-exist/messages")
    assert res.status_code == 404
    assert res.json()["detail"] == "session not found"


# ---- Failover test hook (section 4) --------------------------------------

def test_simulate_key1_429_fails_over_to_key2(client):
    res = client.post(
        "/chat",
        json={"session_id": "f-1", "message": "unique-failover-1"},
        headers={"X-Simulate-Failure": "key_1=429"},
    )
    assert res.status_code == 200
    md = res.json()["metadata"]
    assert md["key_id"] == "key_2"
    assert md["fallback_attempts"] == 1
    assert md["resolution_source"] == "LIVE_INFERENCE"
    assert res.json()["reply"]  # a real answer, not an error wrapped in 200


def test_simulate_key1_500_fails_over_to_key2(client):
    res = client.post(
        "/chat",
        json={"session_id": "f-2", "message": "unique-failover-2"},
        headers={"X-Simulate-Failure": "key_1=500"},
    )
    assert res.status_code == 200
    assert res.json()["metadata"]["key_id"] == "key_2"


def test_simulate_all_fail_returns_503(client):
    res = client.post(
        "/chat",
        json={"session_id": "f-all", "message": "unique-failover-all"},
        headers={"X-Simulate-Failure": "*=429"},
    )
    assert res.status_code == 503
    assert res.json()["detail"] == "all providers unavailable"
    # Nothing added to history for a failed request.
    assert client.get("/sessions/f-all/messages").status_code == 404


def test_simulation_ignored_when_disabled(client):
    client.app  # keep reference
    import app.main as app_main

    app_main.settings.enable_failure_simulation = False
    try:
        res = client.post(
            "/chat",
            json={"session_id": "f-off", "message": "sim-disabled"},
            headers={"X-Simulate-Failure": "key_1=429"},
        )
        assert res.status_code == 200
        md = res.json()["metadata"]
        assert md["key_id"] == "key_1"
        assert md["fallback_attempts"] == 0
    finally:
        app_main.settings.enable_failure_simulation = True
