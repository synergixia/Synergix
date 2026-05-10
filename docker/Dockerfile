FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.title="Synergix Nodo Fantasma"
LABEL org.opencontainers.image.description="Primera inteligencia colectiva descentralizada sobre BNB Greenfield"

# ── 1. Dependencias del sistema y Herramientas de Compilación ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    libopenblas0 \
    libomp5 \
    libgomp1 \
    ca-certificates \
    tzdata \
    git \
    build-essential \
    cmake \
    autoconf \
    automake \
    libtool \
    pkg-config \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── 2. Instalación de Go 1.22 ──
RUN ARCH=$(dpkg --print-architecture) && \
    case "${ARCH}" in \
        amd64)  GO_ARCH="amd64" ;; \
        arm64)  GO_ARCH="arm64" ;; \
        *)      echo "Arquitectura no soportada: ${ARCH}"; exit 1 ;; \
    esac && \
    curl -OL https://golang.org/dl/go1.22.0.linux-${GO_ARCH}.tar.gz && \
    tar -C /usr/local -xzf go1.22.0.linux-${GO_ARCH}.tar.gz && \
    rm go1.22.0.linux-${GO_ARCH}.tar.gz

ENV PATH=$PATH:/usr/local/go/bin

ENV TZ=UTC
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

# ── 3. Instalación de dependencias de Python ──
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt --break-system-packages

# ── 4. Compilación de la librería Bridge de Greenfield ──
RUN git clone --depth 1 https://github.com/bnb-chain/data-redundancy-generator-bridge /tmp/bridge \
    && cd /tmp/bridge \
    && go mod download \
    && MAIN_DIR=$(find . -name "main.go" -printf '%h' | head -1) \
    && if [ -z "$MAIN_DIR" ]; then echo "No se encontró main.go"; exit 1; fi \
    && echo "Compilando desde $MAIN_DIR" \
    && CGO_ENABLED=1 go build -o /tmp/bridge/libredgenerator.so -buildmode=c-shared ./${MAIN_DIR} \
    && SITE_PACKAGES=$(python -c "import site; print(site.getsitepackages()[0])") \
    && TARGET_DIR=$SITE_PACKAGES/greenfield_python_sdk/go_library \
    && mkdir -p $TARGET_DIR \
    && cp /tmp/bridge/libredgenerator.so $TARGET_DIR/libredgenerator.so \
    && cp /tmp/bridge/libredgenerator.so $TARGET_DIR/libobject.so \
    && cp /tmp/bridge/libredgenerator.so $TARGET_DIR/libreedsolec.so \
    && cp /tmp/bridge/libredgenerator.so $TARGET_DIR/main.so \
    && cd /app && rm -rf /tmp/bridge

# 🔁 NUEVA LÍNEA: Hacer visible la librería Go para Python
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.11/site-packages/greenfield_python_sdk/go_library:$LD_LIBRARY_PATH

# ── 5. Configuración de Usuario y Estructura ──
RUN groupadd -r synergix && useradd -r -g synergix -d /app -s /sbin/nologin synergix

RUN mkdir -p /app/aisynergix/bot/locales \
    /app/aisynergix/services \
    /app/aisynergix/ai \
    /app/scripts \
    /app/models

# ── 6. Copia de la Aplicación ──
COPY .env /app/.env
COPY aisynergix/ /app/aisynergix/
COPY scripts/ /app/scripts/

RUN chown -R synergix:synergix /app
RUN chmod 600 /app/.env

# ── 7. Gestión de Cachés para Modelos de IA ──
ENV HF_HOME=/tmp/hf_cache
RUN mkdir -p /tmp/hf_cache && chown -R synergix:synergix /tmp/hf_cache && chmod 777 /tmp/hf_cache

USER synergix

# ── 8. Healthcheck y Ejecución ──
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import asyncio; from aisynergix.ai.manager import get_ai_manager; asyncio.run(get_ai_manager().health_check())" || exit 1

EXPOSE 8080 8081

ENTRYPOINT ["bash", "-c", "python -m scripts.sync_brain & python -m aisynergix.bot.bot & wait -n"]
