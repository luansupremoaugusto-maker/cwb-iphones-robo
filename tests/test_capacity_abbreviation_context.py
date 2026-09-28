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


def _iphone(model: str, capacity: str, external_id: str) -> InventoryItem:
    return InventoryItem(
        external_id=external_id,
        name=f"{model} {capacity}",
        category="Celular",
        capacity=capacity,
        color="PRETO",
        price_brl=7000.0,
        quantity=1,
        availability="Disponível para venda",
        source="mercado_phone",
        condition="SEMINOVO",
        search_text=f"{model} {capacity} PRETO celular seminovo",
    )


def _agent(tmp_path, items: list[InventoryItem]) -> AgentService:
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = items
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "Tem o iPhone 17 tb?",
        "Você tem o iPhone 17 tb também?",
    ],
)
async def test_tb_as_also_does_not_turn_iphone_generation_into_capacity(query, tmp_path):
    items = [
        _iphone("iPhone 17", "128 GB", "iphone-17-128"),
        _iphone("iPhone 17", "256 GB", "iphone-17-256"),
    ]
    agent = _agent(tmp_path, items)

    decision = await agent.respond(query)

    assert decision.handoff is False
    assert set(decision.product_references) == {"iphone-17-128", "iphone-17-256"}
    assert "128 GB" in decision.reply
    assert "256 GB" in decision.reply
    assert not any(
        false_capacity in decision.reply.upper()
        for false_capacity in ("17 TB", "17TB")
    )


@pytest.mark.asyncio
async def test_tb_as_also_preserves_an_explicit_256_gb_filter(tmp_path):
    items = [
        _iphone("iPhone 17 Pro Max", "256 GB", "iphone-17-pro-max-256"),
        _iphone("iPhone 17 Pro Max", "1 TB", "iphone-17-pro-max-1tb"),
    ]
    agent = _agent(tmp_path, items)

    decision = await agent.respond("O iPhone 17 Pro Max 256 GB tem tb?")

    assert decision.handoff is False
    assert decision.product_references == ["iphone-17-pro-max-256"]
    assert "256 GB" in decision.reply
    assert "1 TB" not in decision.reply
