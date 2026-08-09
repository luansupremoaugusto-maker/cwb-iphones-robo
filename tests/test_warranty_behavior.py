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


def build_agent(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="13-pro-256",
            name="iPhone 13 Pro",
            capacity="256GB",
            category="Celular",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2550.0,
            search_text="iphone 13 pro 256gb celular seminovo prata",
            color="PRATA",
            colors="PRATA",
            battery_health=97,
        )
    ]
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


@pytest.mark.asyncio
async def test_sealed_warranty_question_returns_one_year_by_apple(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("E os lacrados têm quanto tempo de garantia?")

    assert "1 ano" in decision.reply
    assert "Apple" in decision.reply
    assert "90 dias" not in decision.reply


@pytest.mark.asyncio
async def test_seminew_context_returns_90_days_and_accessories(tmp_path):
    agent = build_agent(tmp_path)
    history = [
        {"role": "user", "content": "Está disponível 13 Pro 256GB?"},
        {
            "role": "assistant",
            "content": "Temos 1 iPhone 13 Pro 256GB seminovo, na cor prata, por R$ 2.550.",
        },
    ]

    decision = await agent.respond("Quanto tempo de garantia? O que acompanha?", history=history)

    assert "90 dias" in decision.reply
    assert "cabo e fonte novos" in decision.reply
    assert "homologados pela Anatel" in decision.reply
    assert "1 ano" not in decision.reply


@pytest.mark.asyncio
async def test_generic_warranty_question_returns_both_approved_rules(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Qual é a garantia dos aparelhos?")

    assert "90 dias" in decision.reply
    assert "1 ano" in decision.reply
    assert "Apple" in decision.reply

