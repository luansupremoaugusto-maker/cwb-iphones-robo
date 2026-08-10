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


@pytest.mark.asyncio
async def test_specific_installment_keeps_selected_battery_on_short_followup(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        FakeMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    low_battery = InventoryItem(
        external_id="14-pro-max-128-81",
        name="IPHONE 14 PRO MAX",
        category="Celular",
        capacity="128GB",
        color="ROXO",
        condition="SEMINOVO",
        availability="Disponivel para venda",
        quantity=1,
        price_brl=3140.0,
        battery_health=81,
        search_text="iphone 14 pro max roxo 128 gb bateria 81 celular seminovo",
    )
    selected = low_battery.model_copy(
        update={
            "external_id": "14-pro-max-128-86",
            "price_brl": 3300.0,
            "battery_health": 86,
            "search_text": "iphone 14 pro max roxo 128 gb bateria 86 celular seminovo",
        }
    )
    cache.items = [low_battery, selected]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Bateria 86%",
        history=[
            {"role": "user", "content": "Parcelado ficaria quantos em 12x?"},
            {
                "role": "assistant",
                "content": "Claro! Para qual aparelho voce quer simular em 12x?",
            },
            {"role": "user", "content": "14 pro Max Roxo 128 gb bateria 86%"},
            {
                "role": "assistant",
                "content": (
                    "Encontrei duas opcoes de iPhone 14 Pro Max roxo, 128 GB: "
                    "R$ 3.140 (bateria 81%) e R$ 3.300 (bateria 86%)."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "R$ 3.300,00" in decision.reply
    assert "12x de R$ 319,95" in decision.reply
    assert "R$ 3.140,00" not in decision.reply
