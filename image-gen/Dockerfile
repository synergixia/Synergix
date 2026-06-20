# Synergix image-gen — stable-diffusion.cpp (CPU) behind a small FastAPI API.
#
# Builds the stable_diffusion_cpp Python bindings from source (compiles native
# code, hence build-essential + cmake). Runs Stable Diffusion 1.5 on CPU.
FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.title="Synergix image-gen"
LABEL org.opencontainers.image.description="Stable Diffusion 1.5 image generator (stable-diffusion.cpp, CPU)"

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Build toolchain for stable-diffusion-cpp-python. No OpenBLAS: on AVX-512
# CPUs (Zen 4) ggml's native SIMD kernels are faster, and OpenBLAS spawns its
# own thread pool that oversubscribes the cores (8 ggml threads × N BLAS
# threads), which cripples per-iteration speed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Native CPU build (AVX-512 etc. auto-detected by ggml on the build host).
RUN pip install --no-cache-dir \
        fastapi \
        "uvicorn[standard]" \
        pillow \
        stable-diffusion-cpp-python

COPY app.py /app/app.py

# Non-root user.
RUN groupadd -r synergix && useradd -r -g synergix -d /app -s /sbin/nologin synergix \
    && chown -R synergix:synergix /app
USER synergix

EXPOSE 8084

# Single worker: the model is large and generation is serialized anyway.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8084", "--workers", "1"]
