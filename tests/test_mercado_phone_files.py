from __future__ import annotations

import json
import time

import httpx
import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.adapters.mercado_phone_files import extract_file_urls, list_product_files
from app.config import Settings
from app.schemas import InventoryItem


def test_extract_file_urls_accepts_nested_attachment_shapes_without_metadata():
    payload = {
        "data": {
            "files": [
                {"id": 77, "nome": "foto.jpg", "arquivo": {"url": "https://cdn.example/iphone.jpg"}},
                {"serialNumber": "must-not-leak", "urlArquivo": "https://cdn.example/iphone-2.jpg"},
            ]
        }
    }

    assert extract_file_urls(payload) == [
        "https://cdn.example/iphone.jpg",
        "https://cdn.example/iphone-2.jpg",
    ]


@pytest.mark.asyncio
async def test_list_product_files_sends_the_documented_read_only_post():
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        assert request.headers["X-API-Key"] == "mpk-test"
        assert request.headers["X-Unit-Id"] == "2620"
        return httpx.Response(
            200,
            json={"items": [{"arquivoUrl": "https://cdn.example/iphone.jpg"}]},
        )

    settings = Settings(
        mercado_phone_api_key="mpk-test",
        mercado_system_unit_id=2620,
        mercado_phone_files_url=(
            "https://app.mercadophone.tech/api.php?"
            "class=ArquivoApiController&method=index"
        ),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    class MercadoStub:
        def __init__(self):
            self.settings = settings
            self._client = client
            self.headers = {
                "Accept": "application/json",
                "X-API-Key": "mpk-test",
                "X-Unit-Id": "2620",
            }

    try:
        response = await list_product_files(MercadoStub(), "123", origin=1)
    finally:
        await client.aclose()

    assert response["items"][0]["arquivoUrl"] == "https://cdn.example/iphone.jpg"
    assert captured["method"] == "POST"
    assert "class=ArquivoApiController" in str(captured["url"])
    assert captured["body"] == {
        "page": 1,
        "order": "id",
        "direction": "desc",
        "filters": {"id": "", "origem": "1", "objetoId": "123"},
    }


@pytest.mark.asyncio
async def test_catalog_loads_product_photos_on_demand_from_mercado_phone(tmp_path):
    calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"items": [{"url": "https://cdn.example/iphone-15.jpg"}]},
        )

    settings = Settings(
        mercado_phone_api_key="mpk-test",
        mercado_phone_files_url="https://app.mercadophone.tech/api.php?class=ArquivoApiController&method=index",
        mercado_cache_ttl_seconds=60,
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    class MercadoStub:
        def __init__(self):
            self.settings = settings
            self._client = http_client
            self.headers = {
                "Accept": "application/json",
                "X-API-Key": "mpk-test",
                "X-Unit-Id": "2620",
            }

        async def fetch_all_inventory(self):
            return []

    cache = StoreCatalogCache(
        MercadoStub(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=None,
    )
    cache.items = [
        InventoryItem(
            external_id="123",
            name="iPhone 15",
            category="Celular",
            quantity=1,
            availability="Disponível",
            search_text="iphone 15 celular",
            source="mercado_phone",
        )
    ]
    cache.last_refresh = time.time()

    try:
        products = await cache.search("iPhone 15", limit=1)
    finally:
        await http_client.aclose()

    assert products[0].photo_urls == ["https://cdn.example/iphone-15.jpg"]
    assert calls == [
        {
            "page": 1,
            "order": "id",
            "direction": "desc",
            "filters": {"id": "", "origem": "1", "objetoId": "123"},
        }
    ]
