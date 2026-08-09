from __future__ import annotations

import httpx
import pytest

from app.adapters.mercado_phone import MercadoPhoneClient
from app.config import Settings


@pytest.mark.asyncio
async def test_mercado_phone_retries_rate_limit():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"detail": "rate limit"})
        return httpx.Response(200, json={"items": []})

    settings = Settings(mercado_phone_api_key="mpk-test", mercado_phone_base_url="https://mercado.test")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=settings.mercado_phone_base_url)
    mercado = MercadoPhoneClient(settings, client=client)
    try:
        response = await mercado.list_stores()
    finally:
        await client.aclose()

    assert response == {"items": []}
    assert calls == 3
