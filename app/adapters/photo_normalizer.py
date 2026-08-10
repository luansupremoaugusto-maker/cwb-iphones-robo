from __future__ import annotations

import base64
import logging
from io import BytesIO
from typing import Any

import httpx
from PIL import Image, ImageOps

from app.config import Settings


logger = logging.getLogger(__name__)


def normalize_image_bytes(content: bytes, content_type: str = "image/jpeg") -> str:
    """Return an EXIF-corrected, portrait-oriented JPEG data URI."""
    with Image.open(BytesIO(content)) as original:
        image = ImageOps.exif_transpose(original)
        if image.width > image.height:
            image = image.rotate(90, expand=True)
        if image.mode != "RGB":
            image = image.convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=88, optimize=True)

    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


class ProductPhotoNormalizer:
    """Prepare catalog photos for WhatsApp without writing media to disk."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
        self._owns_client = client is None
        self._cache: dict[str, str] = {}

    async def normalize(self, url: str) -> str:
        if not isinstance(url, str) or not url.lower().startswith("https://"):
            return url
        if url in self._cache:
            return self._cache[url]

        try:
            response = await self._client.get(url)
            response.raise_for_status()
            if len(response.content) > self.settings.media_max_bytes:
                return url
            normalized = normalize_image_bytes(
                response.content,
                response.headers.get("content-type", "image/jpeg"),
            )
        except Exception as exc:
            # Sending the original approved HTTPS URL is safer than failing
            # the whole answer when a remote attachment is not an image.
            logger.warning("product photo normalization failed: %s", type(exc).__name__)
            return url

        self._cache[url] = normalized
        return normalized

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
