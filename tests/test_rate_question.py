from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.adapters.mercado_phone import InventoryCache
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


def build_empty_agent(tmp_path):
    settings = Settings(faq_path="data/faq.yaml")
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


class SealedRateCatalog:
    def __init__(self):
        self.items = [
            InventoryItem(
                external_id="sheet:iphone-17-pro-256",
                name="iPhone 17 Pro",
                category="Novo lacrado",
                capacity="256 GB",
                price_brl=4000.0,
                condition="novo lacrado",
                source="google_sheets",
                search_text="iphone 17 pro 256 gb novo lacrado",
            )
        ]

    async def search(self, query: str, limit: int = 5):
        return self.items[:limit]

    async def get(self, product_id: str):
        return next((item for item in self.items if item.external_id == product_id), None)


def build_rate_agent(tmp_path):
    settings = Settings(
        google_sheets_enabled=True,
        mercado_cache_ttl_seconds=60,
        faq_path="data/faq.yaml",
    )
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=SealedRateCatalog(),
    )
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


@pytest.mark.asyncio
async def test_no_interest_installment_followup_explains_machine_fee(tmp_path):
    agent = build_empty_agent(tmp_path)

    first = await agent.respond("nao tem sem juros?")
    followup = await agent.respond(
        "quero saber se é possível parcelar sem juros.",
        history=[
            {"role": "user", "content": "nao tem sem juros?"},
            {"role": "assistant", "content": first.reply},
        ],
    )

    for decision in (first, followup):
        reply = decision.reply.lower()
        assert decision.handoff is False
        assert "não há parcelamento sem juros" in reply
        assert "juros da máquina" in reply
        assert "valor passado no cartão" in reply
        assert "quantidade de parcelas" in reply
        assert "qual modelo" not in reply
        assert "%" not in decision.reply


@pytest.mark.asyncio
async def test_generic_rate_question_avoids_percentage_disclosure_and_asks_model(tmp_path):
    agent = build_empty_agent(tmp_path)

    decision = await agent.respond("Mas possuem a taxa?")

    reply = decision.reply.lower()
    assert decision.handoff is False
    assert "cart\u00e3o de cr\u00e9dito" in reply
    assert "m\u00e1quina f\u00edsica" in reply
    assert "%" not in decision.reply
    assert "4,95%" not in decision.reply
    assert "19,20%" not in decision.reply
    assert "qual modelo e capacidade voc\u00ea gostaria de simular" in reply
    assert "taxas do link" not in reply


@pytest.mark.asyncio
async def test_generic_installment_count_after_shipping_simulates_selected_model_not_random_products(tmp_path):
    agent = build_rate_agent(tmp_path)
    agent.cache.sealed_cache.items = [
        InventoryItem(
            external_id="sheet:iphone-16-pro-max-256",
            name="iPhone 16 Pro Max",
            capacity="256 GB",
            price_brl=7600.0,
            condition="novo lacrado",
            source="google_sheets",
            search_text="iphone 16 pro max prateado 256 gb novo lacrado",
        ),
        InventoryItem(
            external_id="sheet:watch-11-46",
            name="Apple Watch Series 11",
            category="Apple Watch",
            capacity="46MM",
            price_brl=2600.0,
            condition="novo lacrado",
            source="google_sheets",
            search_text="apple watch series 11 46mm novo lacrado",
        ),
        InventoryItem(
            external_id="sheet:watch-11-42",
            name="Apple Watch Series 11",
            category="Apple Watch",
            capacity="42MM",
            price_brl=2400.0,
            condition="novo lacrado",
            source="google_sheets",
            search_text="apple watch series 11 42mm novo lacrado",
        ),
        InventoryItem(
            external_id="sheet:iphone-17-air-256",
            name="iPhone 17 Air",
            capacity="256 GB",
            price_brl=5500.0,
            condition="novo lacrado",
            source="google_sheets",
            search_text="iphone 17 air 256 gb novo lacrado",
        ),
    ]
    history = [
        {"role": "user", "content": "iPhone 16 Pro Max 91 de bateria"},
        {
            "role": "assistant",
            "content": (
                "iPhone 16 Pro Max — Prateado — 256 GB — NOVO LACRADO — R$ 7.600,00. "
                "Os lacrados são por encomenda, com prazo de 1 semana."
            ),
        },
        {"role": "user", "content": "Vocês entregam"},
        {
            "role": "assistant",
            "content": (
                "Enviamos para Curitiba e região por motoboy. Para fora de Curitiba, "
                "enviamos por Sedex. O pagamento deve ser antecipado antes do despacho."
            ),
        },
    ]

    decision = await agent.respond("Vocês parcelam em quantas vezes", history=history)

    reply = decision.reply.lower()
    assert decision.handoff is False
    assert decision.product_references == []
    assert "parcelamento do iphone 16 pro max 256 gb" in reply
    assert "preço à vista: r$ 7.600,00" in reply
    assert "1x de" in decision.reply
    assert "18x de" in decision.reply
    assert "apple watch" not in reply
    assert "iphone 17 air" not in reply


@pytest.mark.asyncio
async def test_rate_prompt_followup_calculates_model_without_handoff(tmp_path):
    agent = build_rate_agent(tmp_path)
    initial = await agent.respond("Mas possuem a taxa?")

    decision = await agent.respond(
        "iPhone 17 Pro 256 GB",
        history=[
            {"role": "user", "content": "Mas possuem a taxa?"},
            {"role": "assistant", "content": initial.reply},
        ],
    )

    assert decision.handoff is False
    assert "Parcelamento do iPhone 17 Pro 256 GB" in decision.reply
    assert "1x de" in decision.reply
    assert "18x de" in decision.reply
    assert "atendente" not in decision.reply.lower()
