"""
image_gen.py — client for the Synergix image-gen microservice.

Talks to the FastAPI/stable-diffusion.cpp service (see ../../image-gen) the same
way local_ia.py talks to the llama.cpp servers: a small async httpx wrapper with
a health check. Generation is slow on CPU, so the read timeout is generous.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

IMAGE_GEN_HOST = os.getenv("IMAGE_GEN_HOST", "http://image-gen:8084")

# Feature flag — when false the bot never offers image generation, regardless of
# whether the service is up. Lets you ship the code but keep it dark.
IMAGE_GEN_ENABLED = os.getenv("IMAGE_GEN_ENABLED", "true").strip().lower() in (
    "1", "true", "yes", "on",
)

# FLUX.1-schnell on CPU can take a few minutes per image; allow plenty of room
# but cap the connect timeout so an unreachable service fails fast.
_TIMEOUT = httpx.Timeout(float(os.getenv("IMAGE_GEN_TIMEOUT", "600")), connect=5.0)


class ImageGenConnector:
    def __init__(self, base_url: str = IMAGE_GEN_HOST, timeout: httpx.Timeout = _TIMEOUT):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def health(self) -> bool:
        if not IMAGE_GEN_ENABLED:
            return False
        try:
            client = await self._get_client()
            resp = await client.get(f"{self._base_url}/health")
            return resp.status_code == 200
        except Exception:
            return False

    async def generate(self, prompt: str, *, seed: Optional[int] = None) -> Optional[bytes]:
        """Generate an image from ``prompt``. Returns PNG bytes, or None on failure."""
        if not IMAGE_GEN_ENABLED:
            return None
        payload: dict = {"prompt": prompt}
        if seed is not None:
            payload["seed"] = seed
        try:
            client = await self._get_client()
            resp = await client.post(f"{self._base_url}/generate", json=payload)
            if resp.status_code != 200:
                logger.warning(
                    "image-gen %s returned %d: %s",
                    self._base_url, resp.status_code, resp.text[:300],
                )
                return None
            return resp.content
        except Exception as exc:
            logger.warning("image generation failed: %s", exc)
            return None


_connector: Optional[ImageGenConnector] = None


def get_image_generator() -> ImageGenConnector:
    global _connector
    if _connector is None:
        _connector = ImageGenConnector()
    return _connector


__all__ = ["ImageGenConnector", "get_image_generator", "IMAGE_GEN_ENABLED", "IMAGE_GEN_HOST"]
