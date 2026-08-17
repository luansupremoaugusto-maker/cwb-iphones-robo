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


@pytest.mark.asyncio
async def test_payment_link_policy_is_answered_without_handoff(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Vocês conseguem gerar link de pagamento?")

    reply = decision.reply.lower()
    assert decision.handoff is False
    assert "link de pagamento" in reply
    assert "preferimos" in reply
    assert "máquina física" in reply
    assert "taxa" not in reply
    assert "12x" not in reply
    assert "18x" not in reply


@pytest.mark.asyncio
async def test_online_credit_card_payment_is_treated_as_payment_link(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Queria saber se posso pagar por cartão de crédito online, pois o físico está em outra cidade"
    )

    reply = decision.reply.lower()
    assert decision.handoff is False
    assert "cartão de crédito online" in reply
    assert "link de pagamento" in reply
    assert "máquina física" in reply
    assert "encaminhar" not in reply


@pytest.mark.asyncio
async def test_payment_link_request_does_not_use_machine_installment_limit(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Posso pagar por link em 18x?")

    reply = decision.reply.lower()
    assert "12x" in reply
    assert "18x" not in reply



@pytest.mark.asyncio
async def test_payment_link_rate_question_returns_saved_rates(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Qual é a taxa do link de pagamento?")

    reply = decision.reply.lower()
    assert "taxas do link de pagamento" in reply
    assert "pix: grátis" in reply
    assert "4,20%" in reply
    assert "6,09%" in reply
    assert "16,66%" in reply
    assert "12x" in reply
    assert "18x" not in reply


def test_general_payment_faq_does_not_offer_link_proactively():
    faq = FAQStore("data/faq.yaml")

    assert "link" not in faq.get("pagamento").lower()


@pytest.mark.asyncio
async def test_online_card_followup_returns_link_installment_table_for_product(tmp_path):
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
    assert "link de pagamento" in reply.lower()
    assert "1x de R$ 5.720,25" in reply
    assert "12x de R$ 547,96" in reply
    assert "18x" not in reply


@pytest.mark.asyncio
async def test_online_card_specific_quantity_returns_full_link_comparison_table(tmp_path):
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
    assert "1x de" in reply
    assert "6x de" in reply
    assert "12x de" in reply
    assert "18x" not in reply

