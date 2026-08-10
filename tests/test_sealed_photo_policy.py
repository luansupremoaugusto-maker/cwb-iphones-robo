from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


class SealedPriceCache:
    def __init__(self):
        self.items = [
            InventoryItem(
                external_id="sheet:iphone-17",
                name="iPhone 17",
                capacity="256 GB",
                price_brl=5700.0,
                source="google_sheets",
                condition="novo lacrado",
                search_text="iphone 17 256 gb novo lacrado",
            )
        ]

    async def search(self, query: str, limit: int = 5):
        return self.items[:limit]

    async def get(self, product_id: str):
        return next((item for item in self.items if item.external_id == product_id), None)


def build_agent(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=SealedPriceCache(),
    )
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


@pytest.mark.asyncio
async def test_generic_request_for_sealed_order_photos_explains_no_system_photos(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Pode mandar fotos dos lacrados de encomenda?")

    assert decision.image_urls == []
    assert "por encomenda" in decision.reply.lower()
    assert "fotos do produto" in decision.reply.lower()
    assert "sistema" in decision.reply.lower()


@pytest.mark.asyncio
async def test_specific_sealed_item_never_sends_photos(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Pode mandar a foto do iPhone 17?")

    assert decision.image_urls == []
    assert "por encomenda" in decision.reply.lower()
    assert "fotos do produto" in decision.reply.lower()


@pytest.mark.asyncio
async def test_ready_sealed_item_can_send_its_catalog_photos(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=SealedPriceCache(),
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-17-ready-sealed",
            name="iPhone 17",
            category="Celular",
            capacity="256 GB",
            color="PRETO",
            price_brl=5700.0,
            source="mercado_phone",
            condition="novo lacrado",
            availability="Disponível para venda",
            quantity=1,
            search_text="iphone 17 256 gb preto novo lacrado",
            photo_urls=["https://photos.example/iphone-17-ready-sealed.jpg"],
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Pode mandar a foto do iPhone 17 lacrado?")

    assert decision.image_urls == ["https://photos.example/iphone-17-ready-sealed.jpg"]
    assert decision.product_references == ["iphone-17-ready-sealed"]


@pytest.mark.asyncio
async def test_followup_after_sealed_availability_never_uses_seminovo_photo(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=SealedPriceCache(),
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-seminovo",
            name="iPhone 16",
            category="Celular",
            capacity="128 GB",
            color="AZUL ULTRAMARINO",
            source="mercado_phone",
            condition="seminovo",
            availability="Disponivel para venda",
            quantity=1,
            search_text="iphone 16 128 gb azul ultramarino celular seminovo",
            photo_urls=["https://photos.example/iphone-16-seminovo.jpg"],
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    history = [
        {"role": "user", "content": "16 tem disponivel em quais cores?"},
        {
            "role": "assistant",
            "content": (
                "Sim. Encontrei estas opcoes de iPhone 16 disponiveis: "
                "Azul Ultramarino, Verde-acinzentado, Rosa, Branco e Preto "
                "- 128 GB - NOVO LACRADO - R$ 4.600,00."
            ),
        },
    ]

    decision = await agent.respond("poderia mandar foto ou video", history=history)

    assert decision.image_urls == []
    assert "por encomenda" in decision.reply.lower()
    assert "fotos do produto" in decision.reply.lower()
