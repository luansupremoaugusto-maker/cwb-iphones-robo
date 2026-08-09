from __future__ import annotations

import time

import pytest

from app.adapters.mercado_phone import InventoryCache
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem


@pytest.mark.asyncio
async def test_offline_agent_uses_public_inventory_fields(tmp_path):
    settings = Settings(
        openai_api_key=None,
        mercado_cache_ttl_seconds=60,
        faq_path=str(tmp_path / "faq.yaml"),
    )
    cache = InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json")
    cache.items = [
        InventoryItem(
            external_id="internal-1",
            name="iPhone 13 128GB",
            description="iPhone 13 128GB",
            category="Celular",
            price_brl=1999.0,
            quantity=1,
            availability="Disponível",
            search_text="iphone 13 128gb celular",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Tem iPhone 13?")

    assert decision.handoff is False
    assert "iPhone 13" in decision.reply
    assert "1.999" in decision.reply
    assert "IMEI" not in decision.reply


@pytest.mark.asyncio
async def test_offline_agent_handoffs_explicit_human_request(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    cache = InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json")
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Quero falar com um atendente")

    assert decision.handoff is True
