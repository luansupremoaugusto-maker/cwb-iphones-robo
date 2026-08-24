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


def build_agent(tmp_path):
    settings = Settings(faq_path="data/faq.yaml")
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


def assert_no_payment_link_fee_or_table(reply: str):
    normalized = reply.lower()
    assert "%" not in reply
    assert "taxas do link" not in normalized
    assert "4,20%" not in reply
    assert "6,09%" not in reply
    assert "16,66%" not in reply
    assert "1x de" not in normalized
    assert "6x de" not in normalized
    assert "12x de" not in normalized


@pytest.mark.asyncio
async def test_payment_link_is_no_longer_accepted_without_handoff(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Vocês conseguem gerar link de pagamento?")

    reply = decision.reply.lower()
    assert decision.handoff is False
    assert "não aceitamos mais link de pagamento" in reply
    assert "máquina física" in reply
    assert_no_payment_link_fee_or_table(decision.reply)


@pytest.mark.asyncio
async def test_online_credit_card_payment_is_rejected_as_link_payment(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Queria saber se posso pagar por cartão de crédito online, pois o físico está em outra cidade"
    )

    reply = decision.reply.lower()
    assert decision.handoff is False
    assert "cartão de crédito online" in reply
    assert "não aceitamos mais" in reply
    assert "link de pagamento" in reply
    assert "máquina física" in reply
    assert_no_payment_link_fee_or_table(decision.reply)


@pytest.mark.asyncio
async def test_payment_link_installment_request_is_rejected_without_simulation(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Posso pagar por link em 18x?")

    assert decision.handoff is False
    assert "não aceitamos mais link de pagamento" in decision.reply.lower()
    assert_no_payment_link_fee_or_table(decision.reply)



@pytest.mark.asyncio
async def test_payment_link_rate_question_is_rejected_without_percentages(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Qual é a taxa do link de pagamento?")

    reply = decision.reply.lower()
    assert decision.handoff is False
    assert "não aceitamos mais link de pagamento" in reply
    assert "taxas do link de pagamento" not in reply
    assert_no_payment_link_fee_or_table(decision.reply)


def test_payment_faq_removes_link_rates_and_does_not_offer_link():
    faq = FAQStore("data/faq.yaml")

    assert "link" not in faq.get("pagamento").lower()
    assert "não aceitamos mais link de pagamento" in faq.get("link_pagamento").lower()
    assert "%" not in faq.get("link_pagamento")
    assert "taxas_link_pagamento" not in faq.data.get("topics", {})


@pytest.mark.asyncio
async def test_online_card_followup_is_rejected_even_with_product_context(tmp_path):
    settings = Settings(
        mercado_cache_ttl_seconds=60,
        faq_path="data/faq.yaml",
    )
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="16-pro-max-512",
            name="iPhone 16 Pro Max",
            capacity="512 GB",
            category="Celular",
            price_brl=5480.0,
            quantity=1,
            availability="Disponivel para venda",
            condition="seminovo",
            battery_health=100,
            search_text="iphone 16 pro max 512 gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Mas seria entao para passar cartao por pagamento online, iPhone 16 pro max 512gb",
        history=[
            {
                "role": "assistant",
                "content": (
                    "Temos sim! iPhone 16 Pro Max 512GB, seminovo, bateria 100% - R$ 5.480. "
                    "iPhone 16 Pro Max 256GB, seminovo, bateria 100% - R$ 5.500. "
                    "Qual capacidade voce prefere?"
                ),
            },
            {"role": "user", "content": "Parcelamento e ate 18x? Como ficaria cada um?"},
        ],
    )

    reply = decision.reply
    assert decision.handoff is False
    assert "não aceitamos mais link de pagamento" in reply.lower()
    assert_no_payment_link_fee_or_table(reply)


@pytest.mark.asyncio
async def test_online_card_specific_quantity_is_rejected_without_link_table(tmp_path):
    settings = Settings(
        mercado_cache_ttl_seconds=60,
        faq_path="data/faq.yaml",
    )
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="16-pro-max-512",
            name="iPhone 16 Pro Max",
            capacity="512 GB",
            category="Celular",
            price_brl=5480.0,
            quantity=1,
            availability="Disponivel para venda",
            condition="seminovo",
            battery_health=100,
            search_text="iphone 16 pro max 512 gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Quero pagar pelo link o iPhone 16 Pro Max 512 GB em 6x",
    )

    reply = decision.reply
    assert decision.handoff is False
    assert "não aceitamos mais link de pagamento" in reply.lower()
    assert_no_payment_link_fee_or_table(reply)


@pytest.mark.asyncio
async def test_old_link_installment_history_is_not_reused(tmp_path):
    settings = Settings(
        mercado_cache_ttl_seconds=60,
        faq_path="data/faq.yaml",
    )
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="15-pro-128-blue",
            name="iPhone 15 Pro",
            capacity="128 GB",
            category="Celular",
            price_brl=3460.0,
            quantity=1,
            availability="Disponivel para venda",
            condition="seminovo",
            battery_health=93,
            search_text="iphone 15 pro 128 gb titanio azul celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Em 6 vezes quanto fica, e tem mais juros.",
        history=[
            {
                "role": "user",
                "content": "Queria pagar parcelado à distância pelo cartão.",
            },
            {
                "role": "assistant",
                "content": (
                    "Para pagar parcelado à distância, usamos link de pagamento:\n"
                    "1x de R$ 3.611,69\n"
                    "2x de R$ 1.842,19\n"
                    "3x de R$ 1.240,28\n"
                    "4x de R$ 939,30\n"
                    "5x de R$ 758,77\n"
                    "6x de R$ 638,40\n"
                    "12x de R$ 345,97\n"
                    "O aparelho é o iPhone 15 Pro 128GB seminovo, por R$ 3.460,00 à vista."
                ),
            },
        ],
    )

    reply = decision.reply
    assert decision.handoff is False
    assert "não aceitamos mais link de pagamento" in reply.lower()
    assert_no_payment_link_fee_or_table(reply)

