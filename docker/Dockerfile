FROM arm64v8/python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake gcc g++ libopenblas-dev libomp-dev \                                           pkg-config git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .                                                                              
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

RUN pip install --no-cache-dir -r requirements.txt \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
                                                                                                     FROM arm64v8/python:3.12-slim AS runtime                                                             
RUN apt-get update && apt-get install -y --no-install-recommends \
    libopenblas0 libomp5 libgomp1 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r synergix && useradd -r -g synergix -d /app synergix

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

RUN mkdir -p /app/aisynergix /app/scripts /app/models /tmp/synergix_brains && \
    chown -R synergix:synergix /app /tmp/synergix_brains

COPY --chown=synergix:synergix .env /app/.env
COPY --chown=synergix:synergix aisynergix/ /app/aisynergix/
COPY --chown=synergix:synergix scripts/ /app/scripts/

USER synergix

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1                                                                        ENV PYTHONPATH=/app

HEALTHCHECK --interval=60s --timeout=15s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8080/health 2>/dev/null || curl -f http://localhost:8081/health 2>/dev/null || exit 0

CMD ["python", "-m", "scripts.sync_brain"]
