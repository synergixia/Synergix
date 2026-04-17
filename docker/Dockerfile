# syntax=docker/dockerfile:1.8
# Multi-stage build for Synergix on ARM64
# Stage 1: Build llama.cpp for ARM64 (CPU only)
FROM ubuntu:24.04 AS llama-builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    python3 \
    python3-pip \
    pkg-config \
    libcurl4-openssl-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Clone llama.cpp (latest stable)
RUN git clone https://github.com/ggml-org/llama.cpp.git --depth 1 --branch master .

# Build with optimizations for ARM64
RUN cmake -B build -DLLAMA_CURL=ON -DLLAMA_NATIVE=ON -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS="-march=armv8-a -mtune=cortex-a76 -O3" \
    -DCMAKE_CXX_FLAGS="-march=armv8-a -mtune=cortex-a76 -O3"

RUN cmake --build build --config Release -j$(nproc) --target llama-server

# Stage 2: Python environment builder
FROM python:3.12-slim-bookworm AS python-builder

WORKDIR /app

# Install system dependencies for Python packages
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libgomp1 \
    libopenblas-dev \
    libcurl4 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Stage 3: Runtime image
FROM python:3.12-slim-bookworm AS runtime

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    libgomp1 \
    libopenblas0 \
    libcurl4 \
    && rm -rf /var/lib/apt/lists/*

# Copy llama-server binary from builder
COPY --from=llama-builder /app/build/bin/llama-server /usr/local/bin/llama-server

# Copy Python dependencies from python-builder
COPY --from=python-builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=python-builder /usr/local/bin /usr/local/bin

# Create non-root user
RUN useradd -m -u 1000 synergix && \
    chown -R synergix:synergix /app

USER synergix

# Copy application code (context is project root)
COPY --chown=synergix:synergix .. .

# Create necessary directories
RUN mkdir -p /home/synergix/.cache /home/synergix/.local && \
    chown -R synergix:synergix /home/synergix/.cache /home/synergix/.local

# Environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV UVICORN_LOG_LEVEL=warning
ENV TRANSFORMERS_CACHE=/home/synergix/.cache/huggingface

# Health check for llama-server
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Default command (overridden in docker-compose)
CMD ["python", "main.py"]
