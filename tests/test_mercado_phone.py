from __future__ import annotations

import httpx
import pytest

from app.adapters.mercado_phone import MercadoPhoneClient, MercadoPhoneError
from app.config import Settings


def _item(item_id: str, name: str) -> dict:
    return {
        "id": item_id,
        "aparelhoDescricao": name,
        "descricao": name,
        "quantidade": 1,
        "disponibilidade": "Disponível",
        "valorVenda": 1999.9,
        "tipoProdutoDescricao": "Celular",
    }


@pytest.mark.asyncio
async def test_inventory_is_paginated_and_only_get_is_used():
    calls: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        assert request.method == "GET"
        assert request.headers["X-API-Key"] == "mpk-test"
        assert request.headers["X-Unit-Id"] == "2620"
        page = int(request.url.params["page"])
        if page == 1:
            return httpx.Response(
                200,
                json={"total": 3, "items": [_item("1", "iPhone 13"), _item("2", "iPhone 13 256GB")], "page": 1, "limit": 2},
            )
        return httpx.Response(200, json={"total": 3, "items": [_item("3", "iPhone 14")], "page": 2, "limit": 2})

    settings = Settings(
        mercado_phone_api_key="mpk-test",
        mercado_system_unit_id=2620,
        mercado_page_limit=2,
        mercado_phone_base_url="https://mercado.test",
    )
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url=settings.mercado_phone_base_url)
    mercado = MercadoPhoneClient(settings, client=client)
    try:
        items = await mercado.fetch_all_inventory()
    finally:
        await client.aclose()

    assert [item.external_id for item in items] == ["1", "2", "3"]
    assert len(calls) == 2
    assert all(method == "GET" for method, _ in calls)


@pytest.mark.asyncio
async def test_inventory_capacity_is_resolved_from_storage_size_id():
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.method == "GET"
        if request.url.path == "/api/v1/inventory":
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "items": [
                        {
                            **_item("11948642", "IPHONE 12"),
                            "descricao": "IPHONE 12",
                            "gbId": 17106,
                        }
                    ],
                    "page": 1,
                    "limit": 300,
                },
            )
        if request.url.path == "/api/v1/catalog/storage-sizes":
            assert request.url.params["page"] == "1"
            assert request.url.params["limit"] == "300"
            return httpx.Response(
                200,
                json={"total": 1, "items": [{"id": 17106, "size": "128gb"}]},
            )
        raise AssertionError(f"Unexpected request path: {request.url.path}")

    settings = Settings(
        mercado_phone_api_key="mpk-test",
        mercado_system_unit_id=2620,
        mercado_phone_base_url="https://mercado.test",
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=settings.mercado_phone_base_url,
    )
    mercado = MercadoPhoneClient(settings, client=client)
    try:
        items = await mercado.fetch_all_inventory()
    finally:
        await client.aclose()

    assert [item.external_id for item in items] == ["11948642"]
    assert items[0].capacity == "128GB"
    assert "128 gb" in items[0].search_text
    assert calls == ["/api/v1/inventory", "/api/v1/catalog/storage-sizes"]


@pytest.mark.asyncio
async def test_mercado_phone_surfaces_auth_error_without_mutation():
    methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(401, json={"detail": "unauthorized"})

    settings = Settings(mercado_phone_api_key="mpk-test", mercado_phone_base_url="https://mercado.test")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=settings.mercado_phone_base_url)
    mercado = MercadoPhoneClient(settings, client=client)
    try:
        with pytest.raises(MercadoPhoneError) as error:
            await mercado.list_stores()
    finally:
        await client.aclose()

    assert error.value.status_code == 401
    assert methods == ["GET"]
