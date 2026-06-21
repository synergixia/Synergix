"""
image_gen.py — client for the Synergix image generator (Fal.ai).

Calls the Fal.ai hosted API (https://fal.run/<model>) with an
`Authorization: Key <FAL_KEY>` header and returns PNG bytes. Fal returns the
image as a URL (downloaded here) or, with sync mode, an inline data URI.

Fal is a managed service, so there is nothing to host: no GPU, no worker, no
cold starts. Configure FAL_KEY (and optionally FAL_MODEL) and you're done.
"""

import base64
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Fal.ai API key, format "<id>:<secret>" (https://fal.ai/dashboard/keys).
FAL_KEY = os.getenv("FAL_KEY", "").strip()
FAL_MODEL = os.getenv("FAL_MODEL", "fal-ai/flux/schnell").strip().strip("/")
# FLUX models are guidance-distilled: ~4 steps, no guidance_scale, no negative
# prompt. SDXL-style models use ~25 steps with guidance + a negative prompt.
_IS_FLUX = "flux" in FAL_MODEL.lower()
FAL_IMAGE_SIZE = os.getenv("FAL_IMAGE_SIZE", "square_hd")  # 1024x1024
FAL_STEPS = int(os.getenv("FAL_STEPS", "4" if _IS_FLUX else "25"))
FAL_GUIDANCE = float(os.getenv("FAL_GUIDANCE", "7.5"))
FAL_NEGATIVE = os.getenv(
    "FAL_NEGATIVE",
    "lowres, blurry, deformed, disfigured, bad anatomy, watermark, text, signature",
)

# Feature flag — when false the bot never offers image generation. Lets you ship
# the code but keep it dark.
IMAGE_GEN_ENABLED = os.getenv("IMAGE_GEN_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on",
)

# Fal is fast, but allow room for queueing; short connect timeout fails fast.
_TIMEOUT = httpx.Timeout(float(os.getenv("IMAGE_GEN_TIMEOUT", "180")), connect=10.0)


class ImageGenConnector:
    def __init__(self, timeout: httpx.Timeout = _TIMEOUT):
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._fal_base = f"https://fal.run/{FAL_MODEL}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def health(self) -> bool:
        # Managed API — no per-endpoint probe; available when enabled + key set.
        return bool(IMAGE_GEN_ENABLED and FAL_KEY)

    async def generate(self, prompt: str, *, seed: Optional[int] = None) -> Optional[bytes]:
        """Generate an image from ``prompt``. Returns PNG bytes, or None on failure."""
        if not IMAGE_GEN_ENABLED:
            return None
        if not FAL_KEY:
            logger.warning("FAL_KEY not set — cannot generate images")
            return None

        body = {
            "prompt": prompt,
            "image_size": FAL_IMAGE_SIZE,
            "num_inference_steps": FAL_STEPS,
            "num_images": 1,
            "enable_safety_checker": True,
        }
        # Only SDXL-style models accept these; FLUX schnell would reject them.
        if not _IS_FLUX:
            body["negative_prompt"] = FAL_NEGATIVE
            body["guidance_scale"] = FAL_GUIDANCE
        if seed is not None:
            body["seed"] = seed

        try:
            client = await self._get_client()
            resp = await client.post(
                self._fal_base, json=body, headers={"Authorization": f"Key {FAL_KEY}"}
            )
            if resp.status_code != 200:
                logger.warning(
                    "fal %s returned %d: %s", FAL_MODEL, resp.status_code, resp.text[:300]
                )
                return None
            data = resp.json()
            images = data.get("images") or []
            url = images[0].get("url") if images else None
            if not url:
                logger.warning("fal returned no image: %s", str(data)[:300])
                return None
            if url.startswith("data:"):
                return _decode_data_uri(url)
            # Hosted URL — download the bytes.
            img = await client.get(url)
            if img.status_code != 200:
                logger.warning("failed to download fal image %s: %d", url, img.status_code)
                return None
            return img.content
        except Exception as exc:
            logger.warning("image generation failed: %s", exc)
            return None


def _decode_data_uri(uri: str) -> Optional[bytes]:
    """Decode a `data:image/...;base64,<data>` URI to raw bytes."""
    try:
        return base64.b64decode(uri.split(",", 1)[1])
    except Exception as exc:
        logger.warning("failed to decode fal data URI: %s", exc)
        return None


_connector: Optional[ImageGenConnector] = None


def get_image_generator() -> ImageGenConnector:
    global _connector
    if _connector is None:
        _connector = ImageGenConnector()
    return _connector


__all__ = ["ImageGenConnector", "get_image_generator", "IMAGE_GEN_ENABLED"]
