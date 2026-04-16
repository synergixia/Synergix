# docker/Dockerfile
# Synergix Ghost Node — Dockerfile optimizado para ARM64 (Hetzner 8GB RAM)
# Python 3.11 + Alpine Linux para imagen mínima con soporte ARM64 completo

# ──────────────────────────────────────────────────────────────────────────────
# ETAPA 1: CONSTRUCCIÓN — Instalación de dependencias y compilación
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-alpine AS builder

# Variables de entorno para construcción
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Instalar herramientas de compilación necesarias para dependencias nativas
RUN apk add --no-cache \
    gcc \
    g++ \
    musl-dev \
    linux-headers \
    make \
    cmake \
    git \
    curl \
    && rm -rf /var/cache/apk/*

# Crear directorio de trabajo
WORKDIR /app

# Copiar requirements.txt primero para aprovechar caché de Docker
COPY requirements.txt .

# Instalar dependencias de Python con optimizaciones ARM64
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir \
        --only-binary :none: \
        --extra-index-url https://www.piwheels.org/simple \
        -r requirements.txt

# ──────────────────────────────────────────────────────────────────────────────
# ETAPA 2: IMAGEN FINAL — Imagen mínima con solo lo necesario
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-alpine AS runtime

# Metadatos de la imagen
LABEL maintainer="Synergix Ghost Node <synergix@ghostnode.ai>" \
      version="1.0.0" \
      description="Synergix Ghost Node — Nodo Fantasma Web3 con IA local" \
      architecture="arm64"

# Variables de entorno de producción
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

# Instalar dependencias del sistema necesarias para runtime
# - libgomp: OpenMP para FAISS CPU
# - curl: Health checks y debugging
# - tzdata: Zona horaria UTC
RUN apk add --no-cache \
    libgomp \
    curl \
    tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/cache/apk/*

# Crear usuario no-root para seguridad
RUN addgroup -g 1000 synergix && \
    adduser -u 1000 -G synergix -D -h /app synergix

# Crear directorios necesarios con permisos correctos
RUN mkdir -p /app/brain /app/data /app/logs /app/aisynergix/data && \
    chown -R synergix:synergix /app && \
    chmod 755 /app

# Cambiar al usuario no-root
USER synergix

# Directorio de trabajo
WORKDIR /app

# Copiar dependencias instaladas desde la etapa builder
COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

# Copiar código fuente del proyecto
COPY --chown=synergix:synergix . .

# Verificar estructura de directorios
RUN echo "=== Verificando estructura del proyecto ===" && \
    ls -la && \
    echo "aisynergix/" && ls -la aisynergix/ && \
    echo "docker/" && ls -la docker/ && \
    echo "scripts/" && ls -la scripts/

# ──────────────────────────────────────────────────────────────────────────────
# HEALTH CHECK — Verificación de salud del contenedor
# ──────────────────────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# ──────────────────────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA — Comando principal de ejecución
# ──────────────────────────────────────────────────────────────────────────────
ENTRYPOINT ["python", "-m", "aisynergix.bot.bot"]

# Comando por defecto (puede ser sobrescrito en docker-compose)
CMD []
