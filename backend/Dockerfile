FROM python:3.13-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/.venv \
    UV_COMPILE_BYTECODE=1

# `upgrade` picks up security patches published since the base image was built.
# libmagic powers upload content-type sniffing (rag/security.py);
# libgomp is needed by onnxruntime, which backs FlashRank reranking.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
        libmagic1 \
        libgomp1 \
        curl \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN pip install --no-cache-dir uv && \
    uv venv && \
    uv sync --frozen --no-dev

EXPOSE 8000

# --- development (default): sources are bind-mounted, uv sync runs at start ---
FROM base AS development

# --- worker-full: ingestion-only image with Docling/OCR/layout models --------
FROM base AS worker-full
RUN apt-get update && apt-get install -y --no-install-recommends \
        g++ \
        libglib2.0-0 \
        libgl1 \
        libxcb1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-dev --extra docling

# --- production: sources baked in, served by gunicorn ------------------------
FROM base AS production

COPY . .

# Never run as root in production.
RUN useradd --create-home --uid 10001 appuser && \
    chown -R appuser:appuser /app /.venv
USER appuser

# No `|| true`: if the admin/DRF/Swagger assets fail to collect, /static/ would
# 404 at runtime and we would rather find out here than in production.
# SECRET_KEY is only needed to let settings import; it is not baked in.
RUN SECRET_KEY=build-only uv run python manage.py collectstatic --noinput

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health/ || exit 1

# Long timeout: a chat request streams for as long as the model generates.
CMD ["uv", "run", "--", "gunicorn", "api.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--threads", "2", \
     "--timeout", "300", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
