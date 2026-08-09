from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem


class FakeMercadoClient:
    async def fetch_all_inventory(self):
        return []


class FakeSealedCache:
    def __init__(self):
        self.items = [
            InventoryItem(
                external_id="sheet:e",
                name="iPhone 17 E",
                capacity="256 GB",
                price_brl=4600.0,
                condition="novo lacrado",
                source="google_sheets",
                search_text="iphone 17 e 256 gb novo lacrado",
            ),
            InventoryItem(
                external_id="sheet:air",
                name="iPhone 17 Air",
                capacity="256 GB",
                price_brl=5700.0,
                condition="novo lacrado",
                source="google_sheets",
                search_text="iphone 17 air 256 gb novo lacrado",
            ),
            InventoryItem(
                external_id="sheet:base",
                name="iPhone 17",
                capacity="256 GB",
                price_brl=5700.0,
                condition="novo lacrado",
                source="google_sheets",
                search_text="iphone 17 256 gb novo lacrado",
            ),
            InventoryItem(
                external_id="sheet:pro",
                name="iPhone 17 Pro",
                capacity="256 GB",
                price_brl=6800.0,
                condition="novo lacrado",
                source="google_sheets",
                search_text="iphone 17 pro 256 gb novo lacrado",
            ),
        ]

    async def search(self, query: str, limit: int = 5):
        return self.items[:limit]

    async def get(self, product_id: str):
        return next((item for item in self.items if item.external_id == product_id), None)


def build_cache(tmp_path):
    settings = Settings(
        google_sheets_enabled=True,
        mercado_cache_ttl_seconds=60,
        faq_path=str(tmp_path / "faq.yaml"),
    )
    cache = StoreCatalogCache(
        FakeMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=FakeSealedCache(),
    )
    cache.last_refresh = time.time()
    return cache, settings


@pytest.mark.asyncio
async def test_normal_model_is_preferred_for_installment_lookup(tmp_path):
    cache, _settings = build_cache(tmp_path)

    result = await cache.simulate_all_installments("iPhone 17 normal 256 GB")

    assert result["encontrado"] is True
    assert result["nome"] == "iPhone 17"
    assert len(result["parcelas"]) == 18


@pytest.mark.asyncio
async def test_contextual_installment_question_returns_full_table(tmp_path):
    cache, settings = build_cache(tmp_path)
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "preço, quanto fica parcelado?",
        history=[
            {
                "role": "assistant",
                "content": "O iPhone 17 normal 256 GB novo lacrado está disponível. O valor é R$ 5.700.",
            }
        ],
    )

    assert decision.handoff is False
    assert "1x de" in decision.reply
    assert "18x de" in decision.reply
    assert "em quantas vezes" not in decision.reply.lower()
