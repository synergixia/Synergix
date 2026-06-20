"""
app.py — Synergix image generator microservice.

Wraps stable-diffusion.cpp (via the `stable_diffusion_cpp` Python bindings) in
a tiny HTTP API so the bot can request images the same way it talks to the
Thinker/Judge llama.cpp servers.

Runs FLUX.1-schnell quantized (GGUF) on CPU. Generation is heavy (minutes per
image on CPU), so the service serializes work with a lock and the bot is
expected to queue requests on its side as well.

Endpoints:
  GET  /health    → 200 once the model is loaded, 503 while still loading.
  POST /generate  → {"prompt": "...", "steps"?, "width"?, "height"?, "seed"?}
                    returns image/png bytes.
"""

import asyncio
import io
import logging
import os
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("image-gen")

# ── Model config (paths to the FLUX.1-schnell GGUF + encoders) ───────────────
DIFFUSION_MODEL = os.getenv("FLUX_DIFFUSION_MODEL", "/models/flux/flux1-schnell-q4_k.gguf")
CLIP_L_PATH     = os.getenv("FLUX_CLIP_L", "/models/flux/clip_l.safetensors")
T5XXL_PATH      = os.getenv("FLUX_T5XXL", "/models/flux/t5xxl_fp16.safetensors")
VAE_PATH        = os.getenv("FLUX_VAE", "/models/flux/ae.safetensors")

# FLUX.1-schnell is guidance-distilled: cfg_scale 1.0 and ~4 steps.
DEFAULT_STEPS  = int(os.getenv("IMG_STEPS", "4"))
DEFAULT_WIDTH  = int(os.getenv("IMG_WIDTH", "768"))
DEFAULT_HEIGHT = int(os.getenv("IMG_HEIGHT", "768"))
DEFAULT_CFG    = float(os.getenv("IMG_CFG", "1.0"))
N_THREADS      = int(os.getenv("IMG_THREADS", str(os.cpu_count() or 8)))
# Hard caps so a crafted request can't ask for a 4096² image and pin the CPU.
MAX_DIM   = int(os.getenv("IMG_MAX_DIM", "1024"))
MAX_STEPS = int(os.getenv("IMG_MAX_STEPS", "8"))

app = FastAPI(title="Synergix image-gen")

# Loaded lazily in a background thread so the container reports healthy only
# once the (large) model weights are in memory.
_sd = None
_load_error: str = ""
_gen_lock = threading.Lock()  # serialize generation — CPU does one at a time


def _load_model() -> None:
    global _sd, _load_error
    try:
        from stable_diffusion_cpp import StableDiffusion
        logger.info("Loading FLUX.1-schnell (this can take a while)…")
        _sd = StableDiffusion(
            diffusion_model_path=DIFFUSION_MODEL,
            clip_l_path=CLIP_L_PATH,
            t5xxl_path=T5XXL_PATH,
            vae_path=VAE_PATH,
            n_threads=N_THREADS,
        )
        logger.info("Model loaded — ready to generate.")
    except Exception as exc:  # noqa: BLE001 — surface any load failure via /health
        _load_error = f"{type(exc).__name__}: {exc}"
        logger.exception("Failed to load model: %s", _load_error)


@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=_load_model, daemon=True).start()


@app.get("/health")
def health() -> Response:
    if _sd is not None:
        return Response(status_code=200)
    # 503 = still loading (or failed); the bot treats image-gen as unavailable.
    detail = _load_error or "model still loading"
    return Response(content=detail, status_code=503)


class GenerateRequest(BaseModel):
    prompt: str
    steps: int | None = None
    width: int | None = None
    height: int | None = None
    seed: int | None = None


def _run_generation(req: GenerateRequest) -> bytes:
    steps = min(req.steps or DEFAULT_STEPS, MAX_STEPS)
    width = min(req.width or DEFAULT_WIDTH, MAX_DIM)
    height = min(req.height or DEFAULT_HEIGHT, MAX_DIM)
    seed = req.seed if req.seed is not None else -1

    # Only one generation at a time — protects the CPU from oversubscription
    # even if two requests slip through the bot-side queue.
    with _gen_lock:
        images = _sd.txt_to_img(
            prompt=req.prompt,
            cfg_scale=DEFAULT_CFG,
            sample_steps=steps,
            width=width,
            height=height,
            seed=seed,
        )

    if not images:
        raise RuntimeError("generator returned no image")

    buf = io.BytesIO()
    images[0].save(buf, format="PNG")
    return buf.getvalue()


@app.post("/generate")
async def generate(req: GenerateRequest) -> Response:
    if _sd is None:
        raise HTTPException(status_code=503, detail=_load_error or "model still loading")
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    try:
        # Run the blocking, CPU-heavy generation off the event loop.
        png = await asyncio.to_thread(_run_generation, req)
    except Exception as exc:  # noqa: BLE001
        logger.exception("generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return Response(content=png, media_type="image/png")
