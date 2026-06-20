"""
app.py — Synergix image generator microservice.

Wraps stable-diffusion.cpp (via the `stable_diffusion_cpp` Python bindings) in
a tiny HTTP API so the bot can request images the same way it talks to the
Thinker/Judge llama.cpp servers.

Runs Stable Diffusion 1.5 on CPU (512px). The service serializes work with a
lock and the bot is expected to queue requests on its side as well.

Endpoints:
  GET  /health    → 200 once the model is loaded, 503 while still loading.
  POST /generate  → {"prompt": "...", "steps"?, "width"?, "height"?, "seed"?}
                    returns image/png bytes.
"""

import asyncio
import inspect
import io
import logging
import os
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("image-gen")

# ── Model config ─────────────────────────────────────────────────────────────
# SD 1.5 ships as a single checkpoint. Point SD_MODEL at a .safetensors or a
# quantized .gguf. SD_VAE is optional — leave empty to use the built-in VAE.
SD_MODEL = os.getenv("SD_MODEL", "/models/image/v1-5-pruned-emaonly.safetensors")
SD_VAE   = os.getenv("SD_VAE", "").strip()

# SD 1.5 is native at 512² and needs real CFG (~7) with ~20-25 steps. Much
# lighter than SDXL (860M UNet vs 2.6B), so far faster on CPU.
DEFAULT_STEPS    = int(os.getenv("IMG_STEPS", "20"))
DEFAULT_WIDTH    = int(os.getenv("IMG_WIDTH", "512"))
DEFAULT_HEIGHT   = int(os.getenv("IMG_HEIGHT", "512"))
DEFAULT_CFG      = float(os.getenv("IMG_CFG", "7.0"))
DEFAULT_SAMPLER  = os.getenv("IMG_SAMPLER", "euler_a")
# Applied to every image to nudge quality up; override or blank out via env.
DEFAULT_NEGATIVE = os.getenv(
    "IMG_NEGATIVE",
    "lowres, blurry, deformed, disfigured, bad anatomy, watermark, text, signature",
)
N_THREADS = int(os.getenv("IMG_THREADS", str(os.cpu_count() or 8)))
# Hard caps so a crafted request can't pin the CPU. SD 1.5 also degrades badly
# above ~768 (repeated subjects), so cap there.
MAX_DIM   = int(os.getenv("IMG_MAX_DIM", "768"))
MAX_STEPS = int(os.getenv("IMG_MAX_STEPS", "40"))


def _envbool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# CPU performance flags (supported by this binding). conv_direct uses direct
# convolutions instead of im2col — faster and far less RAM (the VAE decode in
# particular was using ~7.6 GB). flash_attn is off by default (CPU support varies).
CONV_DIRECT     = _envbool("SD_CONV_DIRECT", True)
VAE_CONV_DIRECT = _envbool("SD_VAE_CONV_DIRECT", True)
FLASH_ATTN      = _envbool("SD_FLASH_ATTN", False)
# Weight type the model runs in. SD 1.5 checkpoints are often f32 on disk; running
# q8_0 on CPU is markedly faster (optimized AVX-512 kernels, less memory traffic)
# with no visible quality loss. Set to "f16" or "f32" to disable quantization.
SD_WTYPE = os.getenv("SD_WTYPE", "q8_0").strip()

app = FastAPI(title="Synergix image-gen")

# Loaded lazily in a background thread so the container reports healthy only
# once the (large) model weights are in memory.
_sd = None
_load_error: str = ""
_gen_lock = threading.Lock()  # serialize generation — CPU does one at a time


def _construct(StableDiffusion, base: dict, perf: dict):
    """Construct StableDiffusion, progressively dropping optional kwargs.

    Tries the full set first, then without wtype (in case a quant value is
    unsupported), then bare — so a bad SD_WTYPE degrades gracefully instead of
    leaving the service unhealthy.
    """
    perf_no_wtype = {k: v for k, v in perf.items() if k != "wtype"}
    for variant in ({**base, **perf}, {**base, **perf_no_wtype}, base):
        try:
            return StableDiffusion(**variant)
        except Exception as exc:  # noqa: BLE001 — fall back to a simpler config
            logger.warning("construct attempt failed (%s); trying simpler config", exc)
    # Last resort: let the bare-base error propagate to the caller.
    return StableDiffusion(**base)


def _load_model() -> None:
    global _sd, _load_error
    try:
        from stable_diffusion_cpp import StableDiffusion
        logger.info("Loading Stable Diffusion 1.5 (wtype=%s)…", SD_WTYPE)
        base = {"model_path": SD_MODEL, "n_threads": N_THREADS}
        if SD_VAE:
            base["vae_path"] = SD_VAE
        perf = {}
        if CONV_DIRECT:
            perf["diffusion_conv_direct"] = True
        if VAE_CONV_DIRECT:
            perf["vae_conv_direct"] = True
        if FLASH_ATTN:
            perf["diffusion_flash_attn"] = True
        if SD_WTYPE and SD_WTYPE.lower() not in ("", "default", "auto"):
            perf["wtype"] = SD_WTYPE
        _sd = _construct(StableDiffusion, base, perf)
        # Log the public API so the exact txt2img method/signature is visible in
        # the logs — the binding's method name has varied across versions.
        public = [m for m in dir(_sd) if not m.startswith("_")]
        logger.info("StableDiffusion methods: %s", public)
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


# Method name for txt2img has differed across stable-diffusion-cpp-python
# versions; resolve it at runtime instead of hard-coding one.
_TXT2IMG_CANDIDATES = (
    "txt_to_img", "txt2img", "text_to_image", "generate_image", "generate",
)


def _resolve_txt2img():
    for name in _TXT2IMG_CANDIDATES:
        fn = getattr(_sd, name, None)
        if callable(fn):
            return fn
    available = [m for m in dir(_sd) if not m.startswith("_")]
    raise RuntimeError(f"no txt2img method on StableDiffusion; available: {available}")


def _run_generation(req: GenerateRequest) -> bytes:
    steps = min(req.steps or DEFAULT_STEPS, MAX_STEPS)
    width = min(req.width or DEFAULT_WIDTH, MAX_DIM)
    height = min(req.height or DEFAULT_HEIGHT, MAX_DIM)
    seed = req.seed if req.seed is not None else -1

    fn = _resolve_txt2img()
    # Build the full kwarg set, then keep only those the resolved method accepts
    # (param names also vary between versions; unknown ones fall back to defaults).
    wanted = {
        "prompt": req.prompt,
        "negative_prompt": DEFAULT_NEGATIVE,
        "cfg_scale": DEFAULT_CFG,
        "sample_steps": steps,
        "sample_method": DEFAULT_SAMPLER,
        "width": width,
        "height": height,
        "seed": seed,
    }
    try:
        params = inspect.signature(fn).parameters
        accepts_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        call_kwargs = (
            wanted if accepts_var_kw
            else {k: v for k, v in wanted.items() if k in params}
        )
    except (TypeError, ValueError):
        # Signature unavailable (C binding) — pass the common kwargs as-is.
        call_kwargs = wanted

    # Only one generation at a time — protects the CPU from oversubscription
    # even if two requests slip through the bot-side queue.
    with _gen_lock:
        result = fn(**call_kwargs)

    # Most bindings return a list of PIL images; some return a single image.
    image = result[0] if isinstance(result, (list, tuple)) else result
    if image is None:
        raise RuntimeError("generator returned no image")

    buf = io.BytesIO()
    image.save(buf, format="PNG")
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
