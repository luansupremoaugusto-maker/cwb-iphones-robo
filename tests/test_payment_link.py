from __future__ import annotations

import pytest

from app.adapters.mercado_phone import InventoryCache
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore


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

