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


@pytest.mark.asyncio
async def test_photo_retry_reuses_the_previous_customer_product_request(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    urls = [f"https://photos.example/iphone-16e-white-{index}.jpg" for index in range(7)]
    cache.items = [
        InventoryItem(
            external_id="16e-white",
            name="IPHONE 16 E",
            category="Celular",
            capacity="128GB",
            color="BRANCO",
            colors="BRANCO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            search_text="iphone 16 e branco 128 gb celular seminovo",
            photo_urls=urls,
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [
        {"role": "user", "content": "manda fotos do 16e branco pfv"},
        {"role": "assistant", "content": "Claro! Seguem as fotos do IPHONE 16 E 128GB."},
    ]

    decision = await agent.respond("acho que não foi enviado", history=history)

    assert decision.image_urls == urls
    assert "IPHONE 16 E" in decision.reply
