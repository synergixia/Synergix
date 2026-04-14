# ETAPA 1: Motor IA (Llama.cpp para ARM64)
FROM debian:11-slim AS builder

RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN git clone https://github.com/ggerganov/llama.cpp.git .
RUN mkdir build && cd build && \
    cmake .. -DLLAMA_CURL=ON && \
    cmake --build . --config Release -j 4

# ETAPA 2: Synergix Bot (Python)
FROM python:3.10-slim

# Dependencias del sistema
RUN apt-get update && apt-get install -y \
    curl \
    libgomp1 \
    libopenblas-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar ejecutables de la etapa 1
COPY --from=builder /app/build/bin/llama-server /usr/local/bin/

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código del proyecto
COPY . .

# Configuración del entorno
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Exponer puertos (Thinker y Judge si corren en el mismo contenedor o via docker-compose)
EXPOSE 8080 8081

# Script de arranque
CMD ["python", "aisynergix/bot/bot.py"]
