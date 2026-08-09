from __future__ import annotations

import httpx
import pytest

from app.adapters.mercado_phone_files import list_product_files
from app.config import Settings


@pytest.mark.asyncio
async def test_file_endpoint_receives_authorization_header():
    captured: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        return httpx.Response(200, json={"data": {"totalItens": 0, "itens": []}})

    settings = Settings(
        mercado_phone_api_key="mpk-test",
        mercado_phone_files_url="https://app.mercadophone.tech/api.php?class=ArquivoApiController&method=index",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    class MercadoStub:
        def __init__(self):
            self.settings = settings
            self._client = client
            self.headers = {"X-API-Key": "mpk-test", "X-Unit-Id": "2620"}

    try:
        await list_product_files(MercadoStub(), "123")
    finally:
        await client.aclose()

    assert captured["authorization"] == "mpk-test"
