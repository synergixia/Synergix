# ── Synergix — Docker Image (Hetzner CX22 / HF Spaces compatible) ────────────
# Python 3.11-slim + Node.js 20 + Ollama Qwen 2.5-1.5B
# Optimizado para 4 GB RAM / 2 vCPU (amd64)
FROM python:3.11-slim

# ── Sistema base ──────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    supervisor \
    nginx \
    ffmpeg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Instalar Ollama ───────────────────────────────────────────────────────────
RUN curl -fsSL https://ollama.com/install.sh | sh

# ── Directorio de trabajo ─────────────────────────────────────────────────────
WORKDIR /app

# ── Node.js dependencies ──────────────────────────────────────────────────────
COPY package.json package-lock.json* ./
RUN npm install --omit=dev && npm cache clean --force

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Código fuente ─────────────────────────────────────────────────────────────
COPY . .

# ── Crear estructura de directorios locales ───────────────────────────────────
RUN mkdir -p \
    aisynergix/SYNERGIXAI \
    aisynergix/data \
    aisynergix/logs \
    aisynergix/backups \
    && chmod -R 777 aisynergix/

# ── Configurar nginx (HF Spaces: puerto 7860) ─────────────────────────────────
COPY docker/nginx.conf /etc/nginx/nginx.conf
RUN nginx -t

# ── Supervisor ────────────────────────────────────────────────────────────────
COPY docker/supervisord.conf /etc/supervisor/conf.d/synergix.conf

# ── Variables de entorno ──────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    NODE_PATH=/app/node_modules \
    OLLAMA_BASE=http://localhost:11434 \
    OLLAMA_MODEL=qwen2.5:1.5b \
    MAX_TOKENS_CHAT=400 \
    MAX_TOKENS_JUDGE=150 \
    MAX_TOKENS_SUM=60

# Puerto HF Spaces (nginx → bot interno 8080)
EXPOSE 7860

# ── Entrypoint: supervisor gestiona ollama + nginx + bot + keepalive ──────────
CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/synergix.conf"]
