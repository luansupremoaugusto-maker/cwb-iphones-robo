from __future__ import annotations

import json
import time

import httpx
import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.adapters.zapi import ZapiClient
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem
from app.trade_in import is_trade_in_request


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


class FakeSealedCache:
    def __init__(self):
        self.items = [
            InventoryItem(
                external_id="sheet:iphone-16",
                name="iPhone 16",
                capacity="128 GB",
                price_brl=4600.0,
                source="google_sheets",
                condition="novo lacrado",
                search_text="iphone 16 128 gb novo lacrado",
            )
        ]

    async def search(self, query: str, limit: int = 5):
        return self.items[:limit]

    async def get(self, product_id: str):
        return next((item for item in self.items if item.external_id == product_id), None)


def build_cache(tmp_path, *, photos: bool = False):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    item = InventoryItem(
        external_id="mp:iphone-15",
        name="iPhone 15",
        category="Celular",
        capacity="128 GB",
        price_brl=3400.0,
        quantity=1,
        availability="Disponível para venda",
        condition="seminovo",
        search_text="iphone 15 128 gb celular seminovo",
        photo_urls=["https://photos.example/iphone-15.jpg"] if photos else [],
    )
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=FakeSealedCache(),
    )
    cache.items = [item]
    cache.last_refresh = time.time()
    return cache, settings


@pytest.mark.asyncio
async def test_available_list_includes_complete_seminew_and_sealed_sections(tmp_path):
    cache, settings = build_cache(tmp_path)
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("O que tem disponível?")

    assert decision.handoff is False
    assert "Seminovos disponíveis para venda" in decision.reply
    assert "iPhone 15" in decision.reply
    assert "Novos lacrados por encomenda" in decision.reply
    assert "iPhone 16" in decision.reply


@pytest.mark.asyncio
async def test_available_list_includes_ready_sealed_mercado_stock_separately(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = FakeSealedCache()
    sealed.items.append(
        InventoryItem(
            external_id="sheet:iphone-17-pro-max-256",
            name="iPhone 17 Pro Max",
            category="Novo lacrado",
            capacity="256 GB",
            price_brl=7900.0,
            source="google_sheets",
            condition="novo lacrado",
            search_text="iphone 17 pro max 256 gb novo lacrado",
        )
    )
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="mp:iphone-17-pro-max-256-prateado",
            name="iPhone 17 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="PRATEADO",
            price_brl=7460.0,
            quantity=2,
            availability="Disponível para venda",
            source="mercado_phone",
            condition="LACRADO",
            search_text="iphone 17 pro max 256 gb prateado celular lacrado",
        ),
        InventoryItem(
            external_id="mp:iphone-17-pro-max-512-laranja",
            name="iPhone 17 Pro Max",
            category="Celular",
            capacity="512 GB",
            color="LARANJA-CÓSMICO",
            price_brl=8700.0,
            quantity=1,
            availability="Disponível para venda",
            source="mercado_phone",
            condition="LACRADO",
            search_text="iphone 17 pro max 512 gb laranja cosmico celular lacrado",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("O que tem disponível?")

    assert decision.handoff is False
    assert "Lacrados disponíveis para pronta entrega" in decision.reply
    assert "PRATEADO" in decision.reply
    assert "256 GB" in decision.reply
    assert "2 unidades" in decision.reply
    assert "LARANJA-CÓSMICO" in decision.reply
    assert "512 GB" in decision.reply
    assert "Novos lacrados por encomenda" in decision.reply
    assert "iPhone 17 Pro Max" in decision.reply


@pytest.mark.asyncio
async def test_sealed_cell_phone_list_returns_only_iphones_from_both_sources(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = FakeSealedCache()
    sealed.items.extend(
        [
            InventoryItem(
                external_id="sheet:iphone-17",
                name="iPhone 17",
                category="Novo lacrado",
                capacity="256 GB",
                price_brl=5600.0,
                source="google_sheets",
                condition="novo lacrado",
                search_text="iphone 17 256 gb novo lacrado",
            ),
            InventoryItem(
                external_id="sheet:airpods-4",
                name="AirPods 4 com ANC",
                category="Novo lacrado",
                price_brl=1500.0,
                source="google_sheets",
                condition="novo lacrado",
                search_text="airpods 4 com anc novo lacrado",
            ),
            InventoryItem(
                external_id="sheet:apple-watch-se-3",
                name="Apple Watch SE 3 40MM",
                category="Novo lacrado",
                price_brl=2000.0,
                source="google_sheets",
                condition="novo lacrado",
                search_text="apple watch se 3 40mm novo lacrado",
            ),
            InventoryItem(
                external_id="sheet:macbook-air",
                name="MacBook Air",
                category="Novo lacrado",
                price_brl=7000.0,
                source="google_sheets",
                condition="novo lacrado",
                search_text="macbook air novo lacrado",
            ),
            InventoryItem(
                external_id="sheet:ipad",
                name="iPad 11",
                category="Novo lacrado",
                price_brl=3500.0,
                source="google_sheets",
                condition="novo lacrado",
                search_text="ipad 11 novo lacrado",
            ),
        ]
    )
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="mp:iphone-17-pro-max",
            name="iPhone 17 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="PRATEADO",
            price_brl=7460.0,
            quantity=1,
            availability="Disponível para venda",
            source="mercado_phone",
            condition="LACRADO",
            search_text="iphone 17 pro max 256 gb prateado celular lacrado",
        ),
        InventoryItem(
            external_id="mp:airpods-pro-3",
            name="AirPods Pro 3",
            category="Celular",
            price_brl=1800.0,
            quantity=1,
            availability="Disponível para venda",
            source="mercado_phone",
            condition="LACRADO",
            search_text="airpods pro 3 celular lacrado",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Quais celulares lacrados vcs tem?")

    assert decision.handoff is False
    assert "Lacrados disponíveis para pronta entrega" in decision.reply
    assert "Novos lacrados por encomenda" in decision.reply
    assert "iPhone 17 Pro Max" in decision.reply
    assert "iPhone 17" in decision.reply
    for excluded_family in ("AirPods", "Apple Watch", "MacBook", "iPad"):
        assert excluded_family not in decision.reply


@pytest.mark.asyncio
async def test_photo_request_returns_only_approved_product_urls(tmp_path):
    cache, settings = build_cache(tmp_path, photos=True)
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Pode me mandar a foto do iPhone 15?")

    assert decision.handoff is False
    assert decision.image_urls == ["https://photos.example/iphone-15.jpg"]
    assert "fotos do iPhone 15" in decision.reply


def test_generic_apple_buyback_question_uses_the_evaluation_flow():
    assert is_trade_in_request("Vocês compram algum produto Apple?") is True
    assert is_trade_in_request("A loja compra celular usado?") is True
    assert is_trade_in_request("Vocês compram celulares usados?") is True


@pytest.mark.asyncio
async def test_zapi_send_image_uses_send_image_endpoint():
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"messageId": "image-1"})

    settings = Settings(
        outbound_mode="live",
        zapi_instance_id="instance-test",
        zapi_token="token-test",
        zapi_client_token="client-test",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    zapi = ZapiClient(settings, client=client)
    try:
        result = await zapi.send_image(
            "5511999999999",
            "https://photos.example/iphone-15.jpg",
            caption="iPhone 15",
            reply_to="incoming-1",
        )
    finally:
        await client.aclose()

    assert result.sent is True
    assert result.provider_message_id == "image-1"
    assert str(captured["url"]).endswith("/send-image")
    assert captured["json"] == {
        "phone": "5511999999999",
        "image": "https://photos.example/iphone-15.jpg",
        "caption": "iPhone 15",
        "messageId": "incoming-1",
    }
