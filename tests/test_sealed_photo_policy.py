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
