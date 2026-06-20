"""
app.py — Synergix GPU image generator (RunPod).

Diffusers-based Stable Diffusion XL service for an NVIDIA GPU. Exposes the same
HTTP contract as the CPU service (../image-gen) so the bot is unchanged:

  GET  /health    → 200 once the pipeline is loaded, 503 while still loading.
  POST /generate  → {"prompt": "...", "steps"?, "width"?, "height"?, "seed"?}
                    returns image/png bytes. Requires the X-API-Key header when
                    IMAGE_GEN_API_KEY is set (it always should be — this runs on
                    a public RunPod proxy URL).

SDXL is downloaded from Hugging Face on first start and cached under HF_HOME;
point HF_HOME at a RunPod network volume so it survives pod restarts.
"""

import asyncio
import io
import logging
import os
import threading

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("image-gen-gpu")

MODEL_ID = os.getenv("SD_MODEL_ID", "stabilityai/stable-diffusion-xl-base-1.0")

# Shared secret. The endpoint is reachable over the public RunPod proxy, so when
# this is set every /generate call must present X-API-Key. Empty = no auth
# (only acceptable for a private/local test).
API_KEY = os.getenv("IMAGE_GEN_API_KEY", "").strip()

DEFAULT_STEPS    = int(os.getenv("IMG_STEPS", "30"))
DEFAULT_WIDTH    = int(os.getenv("IMG_WIDTH", "1024"))
DEFAULT_HEIGHT   = int(os.getenv("IMG_HEIGHT", "1024"))
DEFAULT_CFG      = float(os.getenv("IMG_CFG", "7.0"))
DEFAULT_NEGATIVE = os.getenv(
    "IMG_NEGATIVE",
    "lowres, blurry, deformed, disfigured, bad anatomy, watermark, text, signature",
)
# Hard caps so a crafted request can't ask for a 4096² image / 500 steps.
MAX_DIM   = int(os.getenv("IMG_MAX_DIM", "1024"))
MAX_STEPS = int(os.getenv("IMG_MAX_STEPS", "50"))

app = FastAPI(title="Synergix image-gen (GPU)")

_pipe = None
_load_error: str = ""
# SDXL fits comfortably on a 16 GB GPU, but we still serialise generation so a
# burst of requests can't exhaust VRAM.
_gen_lock = threading.Lock()


def _load_pipeline() -> None:
    global _pipe, _load_error
    try:
        import torch
        from diffusers import StableDiffusionXLPipeline

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA GPU not available")

        logger.info("Loading SDXL pipeline '%s' (downloads on first run)…", MODEL_ID)
        pipe = StableDiffusionXLPipeline.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.float16,
            use_safetensors=True,
            variant="fp16",
        )
        pipe = pipe.to("cuda")
        pipe.set_progress_bar_config(disable=True)
        # Trim VRAM headroom so other allocations (VAE) never OOM on 16 GB.
        try:
            pipe.enable_vae_tiling()
        except Exception:
            pass
        _pipe = pipe
        logger.info("Model loaded — ready to generate on %s.", torch.cuda.get_device_name(0))
    except Exception as exc:  # noqa: BLE001 — surface load failures via /health
        _load_error = f"{type(exc).__name__}: {exc}"
        logger.exception("Failed to load pipeline: %s", _load_error)


@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=_load_pipeline, daemon=True).start()


def _require_key(x_api_key: str = Header(default="")) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


@app.get("/health")
def health() -> Response:
    if _pipe is not None:
        return Response(status_code=200)
    return Response(content=_load_error or "model still loading", status_code=503)


class GenerateRequest(BaseModel):
    prompt: str
    steps: int | None = None
    width: int | None = None
    height: int | None = None
    seed: int | None = None


def _run_generation(req: GenerateRequest) -> bytes:
    import torch

    steps = min(req.steps or DEFAULT_STEPS, MAX_STEPS)
    width = min(req.width or DEFAULT_WIDTH, MAX_DIM)
    height = min(req.height or DEFAULT_HEIGHT, MAX_DIM)
    generator = None
    if req.seed is not None:
        generator = torch.Generator(device="cuda").manual_seed(int(req.seed))

    with _gen_lock:
        result = _pipe(
            prompt=req.prompt,
            negative_prompt=DEFAULT_NEGATIVE,
            num_inference_steps=steps,
            guidance_scale=DEFAULT_CFG,
            width=width,
            height=height,
            generator=generator,
        )

    image = result.images[0]
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


@app.post("/generate", dependencies=[Depends(_require_key)])
async def generate(req: GenerateRequest) -> Response:
    if _pipe is None:
        raise HTTPException(status_code=503, detail=_load_error or "model still loading")
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    try:
        png = await asyncio.to_thread(_run_generation, req)
    except Exception as exc:  # noqa: BLE001
        logger.exception("generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    return Response(content=png, media_type="image/png")
