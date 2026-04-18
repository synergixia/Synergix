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

# Stage 2: Python environment builder (Unificado a Ubuntu 24.04)
FROM ubuntu:24.04 AS python-builder

WORKDIR /app

# Instalar Python y herramientas de compilación
RUN apt-get update && apt-get install -y \
    python3.12 \
    python3-pip \
    python3-venv \
    gcc \
    g++ \
    libgomp1 \
    libopenblas-dev \
    libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copiar requerimientos
COPY requirements.txt .

# Instalar dependencias en /usr/local (Usando --break-system-packages para contenedores Ubuntu)
RUN pip3 install --upgrade pip --break-system-packages && \
    pip3 install --no-cache-dir -r requirements.txt --prefix=/usr/local --break-system-packages

# Stage 3: Runtime image
FROM ubuntu:24.04 AS runtime

WORKDIR /app

# Instalar solo dependencias de ejecución
RUN apt-get update && apt-get install -y \
    python3.12 \
    python3-pip \
    libgomp1 \
    libopenblas0 \
    libcurl4 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3

# Copiar binarios de llama-server
COPY --from=llama-builder /app/build/bin/llama-server /usr/local/bin/llama-server
COPY --from=llama-builder /app/build/bin/*.so* /usr/local/lib/

# Copiar TODO el contenido de /usr/local desde el builder (Librerías compatibles)
COPY --from=python-builder /usr/local /usr/local

# Configuración del sistema para Ubuntu 24.04
ENV LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH
# En Ubuntu, las librerías se instalan en dist-packages por defecto
ENV PYTHONPATH=/usr/local/lib/python3.12/dist-packages:/app
ENV PYTHONUNBUFFERED=1
ENV TRANSFORMERS_CACHE=/home/synergix/.cache/huggingface

# Create non-root user
RUN useradd -m -u 1001 synergix && \
    chown -R synergix:synergix /app

USER synergix

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p /home/synergix/.cache /home/synergix/.local && \
    chown -R synergix:synergix /home/synergix/.cache /home/synergix/.local

# Default command
CMD ["python3", "main.py"]
