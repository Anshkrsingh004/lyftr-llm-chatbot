# ---- Build stage: install dependencies into an isolated venv ----
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- Runtime stage: small image, only the venv + app code ----
FROM python:3.11-slim

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL=sqlite:////data/app.db

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app ./app

# SQLite file + optional disk cache live on the mounted /data volume.
RUN mkdir -p /data

EXPOSE 8000

# Honor $PORT when the platform sets one (e.g. Render); default to 8000 locally.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://localhost:{p}/health').status==200 else 1)"

# Single worker keeps the in-memory cache and SQLite connection coherent.
# Shell form with exec so uvicorn stays PID 1 (receives SIGTERM) and $PORT expands.
CMD ["/bin/sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
