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

# Stage 2: Python environment builder (USANDO VENV)
FROM ubuntu:24.04 AS python-builder

WORKDIR /app

# Instalar Python y dependencias
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

# Crear entorno virtual (Aísla Aiogram y las demás gemas)
RUN python3.12 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copiar requirements
COPY requirements.txt .

# Instalar dependencias en el entorno virtual
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Stage 3: Runtime image
FROM ubuntu:24.04 AS runtime

WORKDIR /app

# Instalar base
RUN apt-get update && apt-get install -y \
    python3.12 \
    python3-venv \
    libgomp1 \
    libopenblas0 \
    libcurl4 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3

# Copiar Llama-server
COPY --from=llama-builder /app/build/bin/llama-server /usr/local/bin/llama-server
COPY --from=llama-builder /app/build/bin/*.so* /usr/local/lib/

# Copiar el entorno virtual COMPLETO de Python
COPY --from=python-builder /opt/venv /opt/venv

# Setear LD_LIBRARY y el PATH al VENV
ENV LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Crear usuario seguro
RUN useradd -m -u 1001 synergix && chown -R synergix:synergix /app
USER synergix

# Copiar el código del bot                                                                           COPY . .                                                                                                                                                                                                  # Comando final (Ocupa el python del venv)                                                           CMD ["python", "main.py"]
