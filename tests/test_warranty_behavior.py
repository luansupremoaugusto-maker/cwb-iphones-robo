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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Qual a garantia do iPhone vendido no boleto?",
        "Qual a garantia dos iPhones que vocês vendem no boleto?",
    ],
)
async def test_warranty_question_about_phone_sold_on_boleto_stays_on_warranty_policy(
    tmp_path,
    text,
):
    agent = build_agent(tmp_path)

    decision = await agent.respond(text)

    assert decision.handoff is False
    assert decision.product_references == []
    assert decision.image_urls == []
    assert "90 dias" in decision.reply
    assert "boleto" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_colloquial_semis_warranty_followup_returns_only_used_device_policy(tmp_path):
    agent = build_agent(tmp_path)
    history = [
        {
            "role": "assistant",
            "content": (
                "Seminovos disponíveis para pronta entrega: iPhone 16 256GB seminovo. "
                "Novos lacrados por encomenda: iPhone 16 128GB novo lacrado."
            ),
        }
    ]

    decision = await agent.respond("Os semis têm garantia?", history=history)

    assert decision.reply == "Produtos seminovos têm garantia de 90 dias."
    assert "90 dias" in decision.reply
    assert "1 ano" not in decision.reply
    assert "Apple" not in decision.reply


@pytest.mark.asyncio
async def test_warranty_and_payment_question_after_catalog_reply_answers_both_without_handoff(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        InventoryItem(
            external_id="iphone-16e-128",
            name="iPhone 16e",
            capacity="128GB",
            category="Celular",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2730.0,
            search_text="iphone 16e 128gb celular seminovo preto",
            color="PRETO",
            colors="PRETO",
            battery_health=100,
        )
    ]
    agent.cache.last_refresh = time.time()
    history = [
        {"role": "user", "content": "Boa noite, teriam o iPhone 16e?"},
        {
            "role": "assistant",
            "content": (
                "Temos sim 😊 iPhone 16e seminovo, 128GB, preto, com saúde da bateria "
                "em 100%. Está disponível por R$ 2.730."
            ),
        },
    ]

    decision = await agent.respond(
        "Como funciona pra garantia, forma de pagamento e etc?",
        history=history,
    )
    reply = decision.reply.lower()

    assert decision.handoff is False
    assert "90 dias" in reply
    assert "1 ano" not in reply
    assert "pix" in reply
    assert "dinheiro" in reply
    assert "cartão de débito" in reply
    assert "cartão de crédito" in reply
    assert "dúvidas sobre esse aparelho" not in reply
    assert decision.product_references == []
    assert decision.image_urls == []


@pytest.mark.asyncio
async def test_negative_boleto_mention_does_not_override_warranty_question(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Como funciona a garantia? Não vou pagar no boleto")

    reply = decision.reply.lower()
    assert decision.handoff is False
    assert "90 dias" in reply
    assert "não parcelamos no boleto" not in reply
