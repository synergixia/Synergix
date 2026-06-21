"""
handler.py — Synergix image generator for RunPod Serverless.

RunPod Serverless invokes handler(job) instead of running an HTTP server. The
worker stays warm between requests, so the SDXL pipeline is loaded once into a
module global and reused.

Input  (job["input"]): {"prompt": str, "steps"?, "width"?, "height"?, "seed"?}
Output (JSON):         {"image_b64": "<base64 PNG>"}  or  {"error": "..."}

Auth is handled by RunPod (the caller's Bearer API key), so there is no custom
API-key check here. SDXL is pulled from Hugging Face on the first cold start and
cached under HF_HOME — point that at the serverless network volume
(/runpod-volume) so it survives between cold starts.
"""

import base64
import io
import os

import runpod

MODEL_ID = os.getenv("SD_MODEL_ID", "stabilityai/stable-diffusion-xl-base-1.0")

DEFAULT_STEPS    = int(os.getenv("IMG_STEPS", "30"))
DEFAULT_WIDTH    = int(os.getenv("IMG_WIDTH", "1024"))
DEFAULT_HEIGHT   = int(os.getenv("IMG_HEIGHT", "1024"))
DEFAULT_CFG      = float(os.getenv("IMG_CFG", "7.0"))
DEFAULT_NEGATIVE = os.getenv(
    "IMG_NEGATIVE",
    "lowres, blurry, deformed, disfigured, bad anatomy, watermark, text, signature",
)
MAX_DIM   = int(os.getenv("IMG_MAX_DIM", "1024"))
MAX_STEPS = int(os.getenv("IMG_MAX_STEPS", "50"))

_pipe = None


def _get_pipe():
    """Load the SDXL pipeline once; reused while the worker stays warm."""
    global _pipe
    if _pipe is None:
        import torch
        from diffusers import StableDiffusionXLPipeline

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA GPU not available")
        pipe = StableDiffusionXLPipeline.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.float16,
            use_safetensors=True,
            variant="fp16",
        ).to("cuda")
        pipe.set_progress_bar_config(disable=True)
        try:
            pipe.enable_vae_tiling()
        except Exception:
            pass
        _pipe = pipe
    return _pipe


def handler(job):
    inp = job.get("input") or {}
    prompt = str(inp.get("prompt") or "").strip()
    if not prompt:
        return {"error": "prompt is required"}

    import torch

    steps = min(int(inp.get("steps") or DEFAULT_STEPS), MAX_STEPS)
    width = min(int(inp.get("width") or DEFAULT_WIDTH), MAX_DIM)
    height = min(int(inp.get("height") or DEFAULT_HEIGHT), MAX_DIM)
    generator = None
    if inp.get("seed") is not None:
        generator = torch.Generator(device="cuda").manual_seed(int(inp["seed"]))

    try:
        pipe = _get_pipe()
        image = pipe(
            prompt=prompt,
            negative_prompt=DEFAULT_NEGATIVE,
            num_inference_steps=steps,
            guidance_scale=DEFAULT_CFG,
            width=width,
            height=height,
            generator=generator,
        ).images[0]
    except Exception as exc:  # noqa: BLE001 — return the error to the caller
        return {"error": f"{type(exc).__name__}: {exc}"}

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return {"image_b64": base64.b64encode(buf.getvalue()).decode("ascii")}


runpod.serverless.start({"handler": handler})
