from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.agent import AgentService, _format_product_availability, _is_available_list_request, _normalize
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


def test_price_table_request_is_treated_as_complete_available_list():
    assert _is_available_list_request("Tem uma tabela de pre\u00e7o dos iPhone") is True


def test_availability_header_does_not_name_only_first_model_when_candidates_differ():
    reply = _format_product_availability(
        [
            _sealed_item("16", "iPhone 16", "128 GB", 4600),
            _sealed_item("15", "iPhone 15", "128 GB", 4000),
        ]
    )

    first_line = _normalize(reply.splitlines()[0])
    assert first_line.endswith("opcoes de iphone disponiveis:")
    assert "opcoes de iphone 16 disponiveis" not in first_line



@pytest.mark.asyncio
async def test_budgeted_multi_device_request_lists_all_matching_options_without_handoff(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-rosa",
            name="iPhone 15",
            category="Celular",
            capacity="128GB",
            color="ROSA",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            price_brl=2650,
            search_text="iphone 15 rosa 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-branco",
            name="iPhone 15 Pro",
            category="Celular",
            capacity="128GB",
            color="TITANIO BRANCO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            price_brl=3410,
            search_text="iphone 15 pro titanio branco 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-ultramarino",
            name="iPhone 16",
            category="Celular",
            capacity="128GB",
            color="ULTRAMARINO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            price_brl=3660,
            search_text="iphone 16 ultramarino 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-verde-256",
            name="iPhone 16",
            category="Celular",
            capacity="256GB",
            color="VERDE",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            price_brl=3820,
            search_text="iphone 16 verde 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-max",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITANIO NATURAL",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            price_brl=4190,
            search_text="iphone 15 pro max titanio natural 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Gostaria de ver iphones na faixa de 4mil para comprar e retirar amanha, preciso de 2 aparelhos"
    )

    assert decision.handoff is False
    for expected in ("iPhone 15", "iPhone 15 Pro", "iPhone 16"):
        assert expected in decision.reply
    assert "R$ 3.820,00" in decision.reply
    assert "iPhone 15 Pro Max" not in decision.reply
    assert "2 aparelhos" in decision.reply


@pytest.mark.asyncio
async def test_explicit_iphone_pro_max_with_abbreviated_battery_matches_first_request(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-pro-max-95",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITANIO NATURAL",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4190,
            battery_health=95,
            search_text="iphone 15 pro max titanio natural 256gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Vi que voces postaram um 15 pro Max titanio natural bat 95% Esta disponivel?"
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-15-pro-max-95"]
    assert "iPhone 15 Pro Max" in decision.reply
    assert "95%" in decision.reply


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
