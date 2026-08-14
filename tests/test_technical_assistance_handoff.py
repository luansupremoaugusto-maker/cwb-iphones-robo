from __future__ import annotations

import pytest

from app.adapters.mercado_phone import InventoryCache
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


@pytest.mark.asyncio
async def test_battery_replacement_question_is_answered_and_forwarded(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("quanto vcs cobram para trocar a bateria do iphone 11 64g?")

    assert decision.handoff is True
    assert "assistência técnica" in decision.reply.lower()
    assert "atendente" in decision.reply.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "Vocês fazem assistência técnica?",
        "Vocês fazem conserto de iPhone?",
        "Vocês trocam a tela do iPhone 13?",
        "Meu iPhone está com a tela trincada, vocês consertam?",
        "Quero trocar a bateria do meu iPhone 11.",
    ],
)
async def test_other_repair_questions_are_also_forwarded(tmp_path, question):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(question)

    assert decision.handoff is True
    assert "assistência técnica" in decision.reply.lower()
    assert "atendente" in decision.reply.lower()


@pytest.mark.asyncio
async def test_battery_health_question_is_not_treated_as_repair(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Qual a saúde da bateria do iPhone 11?")

    assert decision.handoff is False
