# LLM Chatbot — Key Failover & Response Caching

A small, production-shaped full-stack AI app: a React chat UI over a FastAPI +
LiteLLM backend with automatic API-key failover, response caching, per-request
token tracking, and multi-turn history stored in SQLite.

- **Backend:** Python / FastAPI, LiteLLM gateway, SQLite
- **Frontend:** React (Vite), served by nginx
- **Infra:** Multi-stage Docker images, `docker compose`, a `/data` named volume

---

## 1. Live URLs

> Fill these in after deploying (see **Deployment** below).

- **Frontend:** `https://<your-frontend>.vercel.app`
- **Backend:** `https://<your-backend>.onrender.com`
- **Health check:** `https://<your-backend>.onrender.com/health`

The deployed frontend is configured (via `VITE_API_BASE_URL`) to call the
deployed backend, and the backend allows the frontend origin via
`ALLOWED_ORIGINS`.

---

## 2. Run locally

### With Docker (recommended)

```bash
cp .env.example .env          # then put at least 2 real keys in LLM_API_KEYS
make up                       # builds images and starts both services
```

- Frontend → http://localhost:3000
- Backend  → http://localhost:8000 (`GET /health`)

Stop everything with `make down`. The SQLite database lives on the `appdata`
named volume mounted at `/data`, so conversation history survives
`make down` / `make up`.

> **No API keys handy?** Set `LLM_FAKE_MODE=true` in `.env` to run the whole
> stack with deterministic echo replies — every feature (caching, failover,
> tokens, multi-turn) still works, just without a real provider.

### Without Docker

**Backend**

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

export LLM_API_KEYS="key_a,key_b"
export DATABASE_URL="sqlite:///./app.db"      # local relative path
export ALLOWED_ORIGINS="http://localhost:3000"
export ENABLE_FAILURE_SIMULATION="true"
export CACHE_TYPE="local"

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Frontend**

```bash
cd frontend
npm install
echo "VITE_API_BASE_URL=http://localhost:8000" > .env.local
npm run dev            # http://localhost:3000
```

### Run the backend tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest        # or: make test  (from repo root)
```

17 tests cover validation, caching, failover (incl. `X-Simulate-Failure`),
multi-turn history, and the error contract. They run offline — the provider call
is mocked, so no API keys are needed.

---

## 3. Design decisions

### Key fallback

The key pool is built from `LLM_API_KEYS` (comma-separated). Keys get stable,
non-secret ids `key_1`, `key_2`, … in order. On each `/chat` request the backend
loops over the pool and calls `litellm.completion()` with one key at a time:

- Any failure — HTTP 429, quota errors, 5xx, timeouts, connection or auth errors
  — is caught and we advance to the next key, incrementing `fallback_attempts`.
- The first key that succeeds answers the request; its id is reported as
  `key_id` and the number of keys that failed before it as `fallback_attempts`.
- If **every** key fails, the request returns a clean `503
  {"detail": "all providers unavailable"}` and nothing is written to history.

I chose an explicit loop over `litellm.completion` (rather than letting the
Router hide the fallback) specifically so the response can report *which* key
answered and *how many* failed first, and so the `X-Simulate-Failure` hook can
inject a failure for a specific key. LiteLLM still does the heavy lifting:
provider routing, the actual call, usage accounting, and caching.

### Caching

Caching uses **LiteLLM's built-in cache** (`litellm.Cache`, `local` in-memory by
default or `disk`). The cache key is `model + the full message list`
(system prompt + stored history + new message), exactly as the contract
requires:

- The **same first message** in a new session returns `LLM_CACHE` the second
  time (identical reply text, the tokens stored with the cached response, and
  `key_id: null`).
- A **different** message — or the same message with different history or a
  different system prompt — never comes from cache.
- A cache hit does no network call, so it is always faster than a live call.

A request that carries an active `X-Simulate-Failure` header bypasses the cache,
so the failover path is always exercised "for this request only".

### SQLite session storage

History is stored only in SQLite — no Redis, no Postgres. The schema is created
automatically on startup:

- `messages` — the required model (`id, session_id, role, content, metadata,
  created_at`), plus an index on `(session_id, id)`.
- `sessions` — tracks a per-session system-prompt override.

The client sends only the new message; the backend reloads the session's history
from SQLite, builds the full prompt, and (on success) appends both the user and
assistant rows. The system prompt is stored on the session and is **never**
returned by the history endpoint. The DB file lives under the `/data` volume
(`DATABASE_URL=sqlite:////data/app.db`) so it survives container restarts.

---

## 4. Verification

With the stack running (locally or deployed), pointing `$BASE` at the backend:

**Cache hit** — same first message in two new sessions:

```bash
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"v-1","message":"Capital of France?"}'   # LIVE_INFERENCE

curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"v-2","message":"Capital of France?"}'   # LLM_CACHE, same reply, lower latency_ms
```

**Failover** (requires `ENABLE_FAILURE_SIMULATION=true`):

```bash
# key_1 forced to 429 -> key_2 answers, fallback_attempts = 1
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"v-3","message":"hello"}'

# every key fails -> clean 503, nothing stored
curl -s -X POST $BASE/chat -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"v-4","message":"hello"}'
```

**Per-message token usage** — every `/chat` reply includes `input_tokens` and
`output_tokens` for *that request only* (not a running total), shown next to each
reply in the UI. The history endpoint returns the stored per-message metadata:

```bash
curl -s $BASE/sessions/v-1/messages
```

In the **UI**, each assistant reply shows chips for source
(`LIVE_INFERENCE` / `LLM_CACHE`), model, latency, and input/output tokens. The
**Options** panel exposes a system-prompt field and a dev-only
`X-Simulate-Failure` field so you can demonstrate failover from the browser.

---

## 5. Setup Used

- **Editor:** VS Code
- **Key stack:** FastAPI, LiteLLM, SQLite, React + Vite, Docker Compose, nginx

---

## 6. Bonus features

Beyond the required contract:

- **`LLM_FAKE_MODE`** — run the entire stack with deterministic echo replies and
  no real provider, so every feature is demonstrable (and tested) offline.
- **In-browser failure simulation** — an Options panel lets you set the
  `X-Simulate-Failure` header and a per-session system prompt without curl.
- **Richer reply metadata in the UI** — `key_id` and `fallback_attempts` are
  surfaced as chips so failover is visible at a glance.
- **Backend test suite** — 17 offline tests covering the full contract.
- **Health/connection indicator** — the UI shows live backend status, key count,
  and cache state.
- **Disk-cache option** (`CACHE_TYPE=disk`) that persists the response cache on
  the `/data` volume.
- **New-chat control** — start a fresh session without reloading.

---

## Project structure

```
backend/
  app/
    main.py        # FastAPI app + the 3 endpoints
    config.py      # env-driven settings + key pool
    db.py          # SQLite storage (schema auto-created)
    llm.py         # LiteLLM gateway: failover, caching, tokens
    models.py      # request validation
    simulate.py    # X-Simulate-Failure parsing
  tests/           # pytest suite (offline)
  Dockerfile       # multi-stage, small runtime image
  requirements.txt
frontend/
  src/             # React app (App.jsx, api.js, styles.css)
  Dockerfile       # multi-stage build -> nginx
  nginx.conf
docker-compose.yml
Makefile
.env.example
README.md
```

## API contract (summary)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`  | `/health` | `{status, keys_configured, cache_enabled}` |
| `POST` | `/chat` | one turn → `{session_id, reply, metadata{...}}` |
| `GET`  | `/sessions/{id}/messages` | stored history, oldest first |

Errors: `422` (validation / malformed JSON), `503 {"detail":"all providers
unavailable"}`, `404 {"detail":"session not found"}`. Failed requests are never
added to history.

## Environment variables

| Variable | Meaning |
| --- | --- |
| `LLM_API_KEYS` | Comma-separated key pool, ≥ 2 keys → `key_1`, `key_2`, … |
| `LITELLM_MODEL` | LiteLLM model used for the pool (default `groq/openai/gpt-oss-20b`; any LiteLLM model works, e.g. `groq/llama-3.3-70b-versatile`, `gemini/gemini-2.0-flash`) |
| `LLM_MODELS` | Optional per-key models, parallel to `LLM_API_KEYS` |
| `DATABASE_URL` | SQLite location, e.g. `sqlite:////data/app.db` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins |
| `ENABLE_FAILURE_SIMULATION` | `true` enables the `X-Simulate-Failure` hook |
| `DEFAULT_SYSTEM_PROMPT` | Default system prompt |
| `CACHE_TYPE` / `CACHE_DIR` | `local` (in-memory) or `disk` + its directory |
| `LLM_FAKE_MODE` | `true` → echo replies, no real provider |
| `LLM_REQUEST_TIMEOUT` / `MAX_HISTORY_MESSAGES` | Per-call timeout; history cap |
| `VITE_API_BASE_URL` | Frontend build-time backend URL |

## Deployment

Deploy the backend first (to get its URL), then the frontend, then point CORS
back at the frontend.

### 1. Backend → Render (Docker)

A `render.yaml` Blueprint is included. In the Render dashboard: **New + →
Blueprint → pick this repo**. Render reads `render.yaml` and provisions the
Docker web service with `healthCheckPath: /health`. Then set the two secret env
vars in the dashboard:

- `LLM_API_KEYS` = your two Groq keys, comma-separated
- `ALLOWED_ORIGINS` = your Vercel URL (fill in after step 2)

Copy the backend URL, e.g. `https://lyftr-chatbot-backend.onrender.com`, and
confirm `GET /health` returns `{"status":"ok",...}`.

> The Dockerfile honors Render's `$PORT`. Render's **free** plan has no
> persistent disk, so SQLite history resets on redeploy/spin-down (and free
> instances cold-start after ~15 min idle). For durable history, upgrade the
> instance and attach a disk at `/data`. Running locally with `make up` uses a
> named volume and fully satisfies the "survives restarts" requirement.

### 2. Frontend → Vercel

In Vercel: **Add New → Project → import this repo**, and set **Root Directory =
`frontend`** (a `frontend/vercel.json` pins the Vite build). Add one env var:

- `VITE_API_BASE_URL` = your Render backend URL (from step 1)

Deploy, then copy the Vercel URL, e.g. `https://lyftr-chatbot.vercel.app`.

### 3. Wire CORS

Back in Render, set `ALLOWED_ORIGINS` to the Vercel URL and redeploy. Open the
Vercel URL — the header should read `… keys · cache on`, confirming the two
tiers are connected.
