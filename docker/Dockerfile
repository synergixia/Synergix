FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.title="Synergix Nodo Fantasma"
LABEL org.opencontainers.image.description="Inteligencia colectiva descentralizada sobre Irys (Arweave)"

# ── 1. Dependencias del sistema ──────────────────────────────────────────
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

WORKDIR /app

# ── 2. Dependencias Python ───────────────────────────────────────────────
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt --break-system-packages

# ── 3. Usuario no-root ───────────────────────────────────────────────────
RUN groupadd -r synergix && useradd -r -g synergix -d /app -s /sbin/nologin synergix

RUN mkdir -p /app/aisynergix/bot/locales \
    /app/aisynergix/services \
    /app/aisynergix/ai \
    /app/scripts \
    /app/models

# ── 4. Copia de la aplicación ────────────────────────────────────────────
COPY .env /app/.env
COPY aisynergix/ /app/aisynergix/
COPY scripts/ /app/scripts/

RUN chown -R synergix:synergix /app
RUN chmod 600 /app/.env

# ── 5. Cachés de modelos IA ──────────────────────────────────────────────
ENV HF_HOME=/tmp/hf_cache
RUN mkdir -p /tmp/hf_cache && chown -R synergix:synergix /tmp/hf_cache && chmod 777 /tmp/hf_cache

USER synergix

# ── 6. Healthcheck y ejecución ───────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import asyncio; from aisynergix.ai.manager import get_ai_manager; asyncio.run(get_ai_manager().health_check())" || exit 1

EXPOSE 8080 8081

ENTRYPOINT ["bash", "-c", "python -m scripts.sync_brain & python -m aisynergix.bot.bot & wait -n"]
