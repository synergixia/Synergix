"""
image_gen.py — client for the Synergix image generator.

Supports several backends behind one interface (the bot/manager don't care which):

  * "fal"        — Fal.ai hosted API (default). POST https://fal.run/{model} with
                   an `Authorization: Key <FAL_KEY>` header; the image comes back
                   as a URL (or inline data URI) which we download to bytes.
  * "http"       — a FastAPI service (local CPU stable-diffusion.cpp, or a pod)
                   exposing GET /health and POST /generate -> PNG bytes.
  * "serverless" — a RunPod Serverless endpoint (POST /v2/{id}/runsync) returning
                   the image base64-encoded in the job output.

Select with IMAGE_GEN_MODE (default "fal").
"""

import asyncio
import base64
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

IMAGE_GEN_MODE = os.getenv("IMAGE_GEN_MODE", "fal").strip().lower()

# --- fal.ai mode ---
FAL_KEY = os.getenv("FAL_KEY", "").strip()
FAL_MODEL = os.getenv("FAL_MODEL", "fal-ai/fast-sdxl").strip().strip("/")
FAL_IMAGE_SIZE = os.getenv("FAL_IMAGE_SIZE", "square_hd")  # 1024x1024
FAL_STEPS = int(os.getenv("FAL_STEPS", "25"))
FAL_GUIDANCE = float(os.getenv("FAL_GUIDANCE", "7.5"))
FAL_NEGATIVE = os.getenv(
    "FAL_NEGATIVE",
    "lowres, blurry, deformed, disfigured, bad anatomy, watermark, text, signature",
)

# --- http / pod mode ---
IMAGE_GEN_HOST = os.getenv("IMAGE_GEN_HOST", "http://image-gen:8084")
# Shared secret sent as X-API-Key (only used in http mode; empty = no auth).
IMAGE_GEN_API_KEY = os.getenv("IMAGE_GEN_API_KEY", "").strip()

# --- serverless mode ---
RUNPOD_ENDPOINT_ID = os.getenv("RUNPOD_ENDPOINT_ID", "").strip()
RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY", "").strip()

# Feature flag — when false the bot never offers image generation, regardless of
# whether the service is up. Lets you ship the code but keep it dark.
IMAGE_GEN_ENABLED = os.getenv("IMAGE_GEN_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on",
)

# Generous read timeout to cover serverless cold starts; the connect timeout
# stays short so an unreachable endpoint fails fast.
_TIMEOUT = httpx.Timeout(float(os.getenv("IMAGE_GEN_TIMEOUT", "300")), connect=10.0)
# How long to keep polling a serverless job before giving up (cold start + queue).
_SERVERLESS_POLL_TIMEOUT = float(os.getenv("IMAGE_GEN_POLL_TIMEOUT", "240"))


class ImageGenConnector:
    def __init__(self, timeout: httpx.Timeout = _TIMEOUT):
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._mode = IMAGE_GEN_MODE
        self._http_base = IMAGE_GEN_HOST.rstrip("/")
        self._rp_base = f"https://api.runpod.ai/v2/{RUNPOD_ENDPOINT_ID}"
        self._fal_base = f"https://fal.run/{FAL_MODEL}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── health ──────────────────────────────────────────────────────────
    async def health(self) -> bool:
        if not IMAGE_GEN_ENABLED:
            return False
        try:
            if self._mode == "fal":
                # Fal is a managed API — no per-endpoint health probe; treat it as
                # available when a key is configured.
                return bool(FAL_KEY)
            client = await self._get_client()
            if self._mode == "serverless":
                resp = await client.get(
                    f"{self._rp_base}/health",
                    headers={"Authorization": f"Bearer {RUNPOD_API_KEY}"},
                )
            else:
                resp = await client.get(f"{self._http_base}/health")
            return resp.status_code == 200
        except Exception:
            return False

    # ── generate ────────────────────────────────────────────────────────
    async def generate(self, prompt: str, *, seed: Optional[int] = None) -> Optional[bytes]:
        """Generate an image from ``prompt``. Returns PNG bytes, or None on failure."""
        if not IMAGE_GEN_ENABLED:
            return None
        payload: dict = {"prompt": prompt}
        if seed is not None:
            payload["seed"] = seed
        try:
            if self._mode == "fal":
                return await self._generate_fal(payload)
            if self._mode == "serverless":
                return await self._generate_serverless(payload)
            return await self._generate_http(payload)
        except Exception as exc:
            logger.warning("image generation failed: %s", exc)
            return None

    async def _generate_fal(self, payload: dict) -> Optional[bytes]:
        client = await self._get_client()
        body = {
            "prompt": payload["prompt"],
            "negative_prompt": FAL_NEGATIVE,
            "image_size": FAL_IMAGE_SIZE,
            "num_inference_steps": FAL_STEPS,
            "guidance_scale": FAL_GUIDANCE,
            "num_images": 1,
            "enable_safety_checker": True,
            "format": "png",
        }
        if "seed" in payload:
            body["seed"] = payload["seed"]
        resp = await client.post(
            self._fal_base, json=body, headers={"Authorization": f"Key {FAL_KEY}"}
        )
        if resp.status_code != 200:
            logger.warning("fal %s returned %d: %s", FAL_MODEL, resp.status_code, resp.text[:300])
            return None
        data = resp.json()
        images = data.get("images") or []
        if not images or not images[0].get("url"):
            logger.warning("fal returned no image: %s", str(data)[:300])
            return None
        url = images[0]["url"]
        if url.startswith("data:"):
            return _decode_data_uri(url)
        # Otherwise it's a hosted URL — download the bytes.
        img = await client.get(url)
        if img.status_code != 200:
            logger.warning("failed to download fal image %s: %d", url, img.status_code)
            return None
        return img.content

    async def _generate_http(self, payload: dict) -> Optional[bytes]:
        client = await self._get_client()
        headers = {"X-API-Key": IMAGE_GEN_API_KEY} if IMAGE_GEN_API_KEY else {}
        resp = await client.post(
            f"{self._http_base}/generate", json=payload, headers=headers
        )
        if resp.status_code != 200:
            logger.warning(
                "image-gen %s returned %d: %s",
                self._http_base, resp.status_code, resp.text[:300],
            )
            return None
        return resp.content

    async def _generate_serverless(self, payload: dict) -> Optional[bytes]:
        client = await self._get_client()
        headers = {"Authorization": f"Bearer {RUNPOD_API_KEY}"}
        resp = await client.post(
            f"{self._rp_base}/runsync", json={"input": payload}, headers=headers
        )
        if resp.status_code != 200:
            logger.warning("runpod runsync returned %d: %s", resp.status_code, resp.text[:300])
            return None
        data = resp.json()
        status = data.get("status")

        # runsync may return before completion on a cold start — poll /status.
        deadline = asyncio.get_event_loop().time() + _SERVERLESS_POLL_TIMEOUT
        job_id = data.get("id")
        while status in ("IN_QUEUE", "IN_PROGRESS") and job_id:
            if asyncio.get_event_loop().time() > deadline:
                logger.warning("runpod job %s timed out (status=%s)", job_id, status)
                return None
            await asyncio.sleep(2)
            poll = await client.get(f"{self._rp_base}/status/{job_id}", headers=headers)
            data = poll.json()
            status = data.get("status")

        if status != "COMPLETED":
            logger.warning("runpod job did not complete: status=%s detail=%s",
                           status, str(data.get("error") or data.get("output"))[:300])
            return None
        return _decode_output(data.get("output"))


def _decode_data_uri(uri: str) -> Optional[bytes]:
    """Decode a `data:image/...;base64,<data>` URI to raw bytes."""
    try:
        return base64.b64decode(uri.split(",", 1)[1])
    except Exception as exc:
        logger.warning("failed to decode fal data URI: %s", exc)
        return None


def _decode_output(output) -> Optional[bytes]:
    """Extract PNG bytes from a serverless job output ({"image_b64": "..."})."""
    if isinstance(output, dict):
        if output.get("error"):
            logger.warning("image worker error: %s", str(output["error"])[:300])
            return None
        b64 = output.get("image_b64")
        if b64:
            try:
                return base64.b64decode(b64)
            except Exception as exc:
                logger.warning("failed to decode image_b64: %s", exc)
    logger.warning("unexpected serverless output shape: %s", str(output)[:200])
    return None


_connector: Optional[ImageGenConnector] = None


def get_image_generator() -> ImageGenConnector:
    global _connector
    if _connector is None:
        _connector = ImageGenConnector()
    return _connector


__all__ = ["ImageGenConnector", "get_image_generator", "IMAGE_GEN_ENABLED", "IMAGE_GEN_MODE"]
