FROM python:3.11-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    pkg-config \
    libopenblas-dev \
    libomp-dev \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools wheel cython numpy

FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.title="Synergix Nodo Fantasma"
LABEL org.opencontainers.image.description="Primera inteligencia colectiva descentralizada sobre BNB Greenfield"
LABEL org.opencontainers.image.version="1.0.0"
LABEL org.opencontainers.image.authors="SynergixAI"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libopenblas0 \
    libomp5 \
    libgomp1 \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=UTC
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd -r synergix && useradd -r -g synergix -d /app -s /sbin/nologin synergix

RUN mkdir -p /app/aisynergix/bot/locales \
    /app/aisynergix/services \
    /app/aisynergix/ai \
    /app/scripts \
    /app/docker \
    /app/models

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

RUN pip install --no-cache-dir \
    faiss-cpu==1.8.0 \
    sentence-transformers==3.2.1 \
    torch==2.4.0 --index-url https://download.pytorch.org/whl/cpu

COPY .env /app/.env
COPY aisynergix/ /app/aisynergix/
COPY scripts/ /app/scripts/

RUN chown -R synergix:synergix /app

RUN chmod 600 /app/.env

USER synergix

WORKDIR /app

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import asyncio; from aisynergix.ai.manager import get_ai_manager; asyncio.run(get_ai_manager().health_check())" || exit 1

EXPOSE 8080 8081

ENTRYPOINT ["python", "-m", "scripts.sync_brain"]
