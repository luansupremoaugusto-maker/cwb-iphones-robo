from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.agent import AgentService, _normalize
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


def _seminovo_item(item_id: str, name: str, capacity: str, price: float) -> InventoryItem:
    return InventoryItem(
        external_id=item_id,
        name=name,
        category="Celular",
        capacity=capacity,
        color="ROSA",
        price_brl=price,
        source="mercado_phone",
        condition="seminovo",
        availability="Disponivel para venda",
        quantity=1,
        battery_health=88,
        search_text=f"{name} {capacity} rosa celular seminovo",
    )


class SealedCatalog:
    def __init__(self):
        self.items = [_sealed_item("iphone-16-lacrado", "iPhone 16", "128 GB", 4600)]

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
    cache.items = [_seminovo_item("iphone-15-plus-rosa", "iPhone 15 Plus", "128 GB", 2830)]
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


@pytest.mark.asyncio
async def test_missing_lacrado_followup_offers_catalog_alternatives_without_handoff(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Qual e o valor do lacrado?",
        history=[
            {
                "role": "user",
                "content": "Ola boa tarde ... qual o valor do iPhone 15 plus Rosa?",
            },
            {
                "role": "assistant",
                "content": (
                    "Temos o iPhone 15 Plus Rosa, 128GB, seminovo por R$ 2.830,00. "
                    "Esta disponivel e com 88% de saude da bateria."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "atendente" not in _normalize(decision.reply)
    assert "nao localizei" in _normalize(decision.reply)
    assert "iPhone 15 Plus" in decision.reply
    assert "SEMINOVO" in decision.reply
    assert "iPhone 16" in decision.reply
    assert decision.reply.count("NOVO LACRADO") == 1


@pytest.mark.asyncio
async def test_battery_followup_for_unavailable_model_reports_stock_and_alternatives(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=None,
    )
    cache.items = [
        _seminovo_item("iphone-15-rosa", "iPhone 15", "128 GB", 2920),
        _seminovo_item("iphone-16e-rosa", "iPhone 16e", "128 GB", 2660),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Esse 15 plus, está quanto de bateria?",
        history=[
            {
                "role": "assistant",
                "content": (
                    "• iPhone 15 128gb: R$ 2.920,00 (18x de R$ 200,77)\n"
                    "• iPhone 15 Plus 128gb: R$ 2.830,00 (18x de R$ 194,58)\n"
                    "Qual você prefere?"
                ),
            }
        ],
    )

    normalized = _normalize(decision.reply)
    assert decision.handoff is False
    assert "vou confirmar" not in normalized
    assert "nao localizei" in normalized
    assert "iphone 15 plus" in normalized
    assert "iphone 16 e" in normalized


@pytest.mark.asyncio
async def test_missing_explicit_lacrado_offers_other_catalog_models_without_handoff(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Tem iPhone 15 Plus lacrado?")

    assert decision.handoff is False
    assert "atendente" not in _normalize(decision.reply)
    assert "iPhone 16" in decision.reply
    assert "NOVO LACRADO" in decision.reply
    assert decision.reply.count("NOVO LACRADO") == 1


@pytest.mark.asyncio
async def test_plural_lacrado_price_followup_lists_complete_sealed_catalog(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [
        _sealed_item("iphone-15-lacrado", "iPhone 15", "128 GB", 4000),
        _sealed_item("iphone-16e-lacrado", "iPhone 16e", "128 GB", 4200),
        _sealed_item("iphone-16-plus-lacrado", "iPhone 16 Plus", "256 GB", 4800),
        _sealed_item("iphone-16-pro-max-lacrado", "iPhone 16 Pro Max", "256 GB", 5500),
    ]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = []
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Quanto estão os lacrados?",
        history=[
            {"role": "user", "content": "Qual o valor do iPhone 15?"},
            {
                "role": "assistant",
                "content": "Temos o iPhone 15, 128 GB, seminovo por R$ 2.830,00.",
            },
            {"role": "user", "content": "Do treze pra cima"},
            {
                "role": "assistant",
                "content": (
                    "Do iPhone 13 pra cima, com bateria de 90% ou mais, temos: "
                    "iPhone 13 Pro Max; iPhone 16e; iPhone 16 Plus; iPhone 16 Pro Max. "
                    "Também temos modelos lacrados do iPhone 15 em diante, mas neles a bateria não se aplica."
                ),
            },
        ],
    )

    assert decision.handoff is False
    for model in ("iPhone 15", "iPhone 16e", "iPhone 16 Plus", "iPhone 16 Pro Max"):
        assert model in decision.reply
    assert decision.reply.count("NOVO LACRADO") == 4
    assert "SEMINOVO" not in decision.reply.upper()


def build_accessory_agent(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [
        _sealed_item("sheet:bot:8", "Fonte Tipo-C 20W original", "-", 150),
    ]
    sealed.items[0].search_text = "fonte tipo c 20w original carregador novo lacrado"
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = []
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


def build_phone_and_accessory_agent(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [
        _sealed_item("sheet:bot:8", "Fonte Tipo-C 20W original", "-", 150),
    ]
    sealed.items[0].search_text = "fonte tipo c 20w original carregador novo lacrado"
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [_seminovo_item("phone:15-pro", "iPhone 15 Pro", "128 GB", 3460)]
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


@pytest.mark.asyncio
async def test_source_question_uses_the_sealed_catalog_without_handoff(tmp_path):
    agent = build_accessory_agent(tmp_path)

    decision = await agent.respond("Vc vende apenas a fonte original do iPhone?")

    assert decision.handoff is False
    assert decision.product_references == ["sheet:bot:8"]
    assert "Fonte Tipo-C 20W original" in decision.reply
    assert "NOVO LACRADO" in decision.reply
    assert "R$ 150,00" in decision.reply


@pytest.mark.asyncio
async def test_carregador_followup_uses_the_sealed_catalog(tmp_path):
    agent = build_accessory_agent(tmp_path)

    decision = await agent.respond(
        "Carregador",
        history=[
            {"role": "user", "content": "Vc vende apenas a fonte original do iPhone?"},
        ],
    )

    assert decision.handoff is False
    assert decision.product_references == ["sheet:bot:8"]
    assert "Fonte Tipo-C 20W original" in decision.reply


@pytest.mark.asyncio
async def test_type_c_purchase_reason_keeps_the_iphone_context(tmp_path):
    agent = build_phone_and_accessory_agent(tmp_path)

    decision = await agent.respond(
        "Acho melhor o iPhone 15 Pro porque o carregador já é tipo C, né?",
        history=[
            {
                "role": "assistant",
                "content": "iPhone 15 Pro, 128 GB, seminovo por R$ 3.460,00.",
            }
        ],
    )

    assert decision.handoff is False
    assert "iPhone 15 Pro" in decision.reply
    assert "Fonte Tipo-C 20W original" not in decision.reply
    assert "NOVO LACRADO" not in decision.reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "Vocês vendem carregador?",
        "Quanto custa a fonte?",
        "Quanto custa o carregador tipo C do iPhone 15 Pro?",
    ],
)
async def test_explicit_accessory_request_overrides_phone_history(tmp_path, query):
    agent = build_phone_and_accessory_agent(tmp_path)

    decision = await agent.respond(
        query,
        history=[
            {
                "role": "assistant",
                "content": "iPhone 15 Pro, 128 GB, seminovo por R$ 3.460,00.",
            }
        ],
    )

    assert decision.handoff is False
    assert decision.product_references == ["sheet:bot:8"]
    assert "Fonte Tipo-C 20W original" in decision.reply
    assert "R$ 150,00" in decision.reply
