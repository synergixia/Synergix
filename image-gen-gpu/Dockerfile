# Synergix image-gen (GPU) — diffusers SDXL for RunPod / any NVIDIA GPU.
#
# Uses an official PyTorch CUDA runtime image (no manual CUDA compilation), then
# installs diffusers. SDXL weights are pulled from Hugging Face on first run and
# cached under HF_HOME — mount a RunPod network volume there to persist them.
FROM pytorch/pytorch:2.4.1-cuda12.4-cudnn9-runtime

LABEL org.opencontainers.image.title="Synergix image-gen (GPU)"
LABEL org.opencontainers.image.description="Stable Diffusion XL image generator (diffusers, CUDA)"

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Persist the HF cache here; mount a RunPod volume at /workspace to keep weights.
ENV HF_HOME=/workspace/hf-cache

WORKDIR /app

RUN pip install --no-cache-dir \
        "numpy<2" \
        "diffusers==0.29.2" \
        "transformers==4.43.4" \
        "accelerate==0.33.0" \
        "huggingface_hub==0.24.6" \
        "safetensors==0.4.4" \
        fastapi \
        "uvicorn[standard]" \
        pillow

COPY app.py /app/app.py

EXPOSE 8084

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8084", "--workers", "1"]
