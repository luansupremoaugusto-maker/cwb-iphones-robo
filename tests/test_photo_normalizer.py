from __future__ import annotations

import base64
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.adapters.photo_normalizer import ProductPhotoNormalizer, normalize_image_bytes
from app.config import Settings


def _jpeg_bytes(width: int = 40, height: int = 20) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "red").save(output, format="JPEG")
    return output.getvalue()


def test_normalize_image_bytes_returns_portrait_jpeg_data_uri():
    result = normalize_image_bytes(_jpeg_bytes())
    assert result.startswith("data:image/jpeg;base64,")

    decoded = base64.b64decode(result.split(",", 1)[1])
    with Image.open(BytesIO(decoded)) as image:
        assert image.height > image.width


@pytest.mark.asyncio
async def test_product_photo_normalizer_caches_download_and_keeps_https_fallback():
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=_jpeg_bytes())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    normalizer = ProductPhotoNormalizer(Settings(media_max_bytes=100_000), client=client)
    url = "https://photos.example/iphone-14.jpg"

    first = await normalizer.normalize(url)
    second = await normalizer.normalize(url)

    assert first == second
    assert first.startswith("data:image/jpeg;base64,")
    assert calls == 1
    await normalizer.aclose()
    await client.aclose()
