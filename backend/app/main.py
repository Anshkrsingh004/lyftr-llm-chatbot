"""FastAPI application exposing the three-endpoint contract.

Endpoints:
    GET  /health
    POST /chat
    GET  /sessions/{session_id}/messages
"""
import json
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .db import Database, now_iso
from .llm import AllProvidersUnavailable, LLMGateway
from .models import ChatRequest
from .simulate import parse_sim_header

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

settings = Settings()
db = Database(settings.database_url)
gateway = LLMGateway(settings)

app = FastAPI(title="LLM Chatbot with Key Failover and Response Caching")

_origins = settings.allowed_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    # Credentials cannot be combined with a wildcard origin.
    allow_credentials="*" not in _origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    """Liveness + readiness: app running, DB reachable, key pool loaded."""
    if not db.ping():
        raise HTTPException(status_code=503, detail="database unavailable")
    return {
        "status": "ok",
        "keys_configured": len(settings.keys),
        "cache_enabled": gateway.cache_enabled,
    }


@app.post("/chat")
def chat(req: ChatRequest, request: Request):
    """Single conversational turn with failover + caching + token tracking."""
    sim_header = request.headers.get("x-simulate-failure")
    sim_map = parse_sim_header(sim_header, settings.enable_failure_simulation)

    # Resolve the effective system prompt for this session:
    #   explicit override > previously stored prompt > default.
    session = db.get_session(req.session_id)
    if req.system_prompt and req.system_prompt.strip():
        effective_system_prompt = req.system_prompt
    elif session and session.get("system_prompt"):
        effective_system_prompt = session["system_prompt"]
    else:
        effective_system_prompt = settings.default_system_prompt

    history = db.get_messages(req.session_id)

    try:
        result = gateway.resolve(
            effective_system_prompt, history, req.message, sim_map
        )
    except AllProvidersUnavailable:
        # Clean 503. Nothing is written to history for a failed request.
        raise HTTPException(status_code=503, detail="all providers unavailable")

    # Persist only on success (failed 422/503 requests never touch history).
    db.ensure_session(req.session_id)
    if req.system_prompt and req.system_prompt.strip():
        db.set_system_prompt(req.session_id, req.system_prompt)

    db.add_message(req.session_id, "user", req.message, None, now_iso())

    metadata = {
        "resolution_source": result.source,
        "model": result.model,
        "latency_ms": result.latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "key_id": result.key_id,
        "fallback_attempts": result.fallback_attempts,
    }
    db.add_message(
        req.session_id, "assistant", result.reply, json.dumps(metadata), now_iso()
    )

    return {"session_id": req.session_id, "reply": result.reply, "metadata": metadata}


@app.get("/sessions/{session_id}/messages")
def get_session_messages(session_id: str):
    """Stored history for one session, oldest first, without the system prompt."""
    messages = db.get_messages(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="session not found")

    out = []
    for row in messages:
        if row["role"] == "assistant" and row.get("metadata"):
            try:
                md = json.loads(row["metadata"])
            except Exception:
                md = None
            item = {"role": "assistant", "content": row["content"]}
            if md is not None:
                item["metadata"] = md
            out.append(item)
        else:
            out.append({"role": row["role"], "content": row["content"]})

    return {"session_id": session_id, "messages": out}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Last-resort guard: never leak a stack trace or an HTML error page."""
    log.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "internal server error"})
