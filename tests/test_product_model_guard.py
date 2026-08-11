from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.agent import AgentService, _format_product_availability
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


def _sealed_item(item_id: str, name: str, capacity: str, price: float) -> InventoryItem:
    return InventoryItem(
        external_id=item_id,
        name=name,
        category="Novo lacrado",
        capacity=capacity,
        price_brl=price,
        source="google_sheets",
        condition="novo lacrado",
        availability="Preco confirmado",
        search_text=f"{name} {capacity} novo lacrado",
    )


class SealedCatalog:
    def __init__(self):
        self.items = [
            _sealed_item("13-pro-128", "iPhone 13 Pro", "128 GB", 5000),
            _sealed_item("13-pro-256", "iPhone 13 Pro", "256 GB", 5500),
            _sealed_item("17-pro-max-128", "iPhone 17 Pro Max", "128 GB", 7000),
            _sealed_item("17-pro-max-256", "iPhone 17 Pro Max", "256 GB", 7800),
            _sealed_item("17-pro-512", "iPhone 17 Pro", "512 GB", 8000),
            _sealed_item("17-air-512", "iPhone 17 Air", "512 GB", 8500),
            _sealed_item("macbook-neo-13", "MacBook Neo 2026 13", "256 GB", 4900),
            _sealed_item("macbook-air-15", "MacBook Air", "512 GB", 8500),
            _sealed_item("ipad-air-128", "iPad Air", "128 GB", 3000),
        ]

    async def ensure_fresh(self):
        return None

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
        sealed_cache=SealedCatalog(),
    )
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


@pytest.mark.asyncio
async def test_explicit_iphone_13_pro_keeps_model_and_requested_capacities(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Queria o 13 PRO que tem tela de 120hz, armazenamento pode ser 128 ou 256"
    )

    assert set(decision.product_references) == {"13-pro-128", "13-pro-256"}
    assert "IPHONE 13 PRO" in decision.reply.upper()
    assert "128 GB" in decision.reply.upper()
    assert "256 GB" in decision.reply.upper()
    assert "17 PRO" not in decision.reply.upper()
    assert "MACBOOK" not in decision.reply.upper()


@pytest.mark.asyncio
async def test_missing_iphone_13_pro_512_does_not_return_17_pro_options(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("iPhone 13 Pro verde 512 GB")

    assert decision.product_references == []
    assert "17" not in decision.reply
    assert "MacBook" not in decision.reply


@pytest.mark.asyncio
async def test_bare_iphone_13_followup_does_not_return_macbook_13(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("O 13 nao tem mais?")

    assert decision.product_references == []
    assert "MacBook" not in decision.reply
    assert "17" not in decision.reply


def test_availability_lines_identify_each_model_when_candidates_differ():
    reply = _format_product_availability(
        [
            _sealed_item("17-pro-max", "iPhone 17 Pro Max", "256 GB", 7800),
            _sealed_item("17-pro", "iPhone 17 Pro", "512 GB", 8000),
        ]
    )

    bullet_lines = [line for line in reply.splitlines() if "\u2014" in line]
    assert any("iPhone 17 Pro Max" in line for line in bullet_lines)
    assert any("iPhone 17 Pro" in line and "Max" not in line for line in bullet_lines)


@pytest.mark.asyncio
async def test_explicit_ipad_does_not_return_iphone_or_macbook(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Voce tem algum iPad que esteja bem em conta?")

    assert decision.product_references == ["ipad-air-128"]
    assert "IPAD AIR" in decision.reply.upper()
    assert "IPHONE" not in decision.reply.upper()
    assert "MACBOOK" not in decision.reply.upper()


@pytest.mark.asyncio
async def test_availability_query_with_two_models_keeps_available_second_model(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-12-128",
            name="IPHONE 12",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2200,
            source="mercado_phone",
            search_text="iphone 12 preto 128gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("iphone 11 ou 12 tem disponivel 128gb?")

    assert decision.handoff is False
    assert decision.product_references == ["iphone-12-128"]
    assert "IPHONE 12" in decision.reply.upper()
    assert "128GB" in decision.reply.upper()
