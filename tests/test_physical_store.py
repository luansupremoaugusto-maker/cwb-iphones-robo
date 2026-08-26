from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import app.agent as agent_module
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


def freeze_weekday(monkeypatch):
    current = datetime(2026, 8, 10, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)


@pytest.mark.asyncio
async def test_physical_store_question_returns_address_without_handoff(tmp_path, monkeypatch):
    freeze_weekday(monkeypatch)
    agent = build_agent(tmp_path)

    decision = await agent.respond("Vocês têm loja física ou só entrega mesmo?")

    assert decision.handoff is False
    assert "Sim, temos loja física" in decision.reply
    assert "Avenida Nossa Senhora da Luz, 1341" in decision.reply
    assert "09:00" in decision.reply
    assert "reserva" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_negative_physical_store_question_is_corrected(tmp_path, monkeypatch):
    freeze_weekday(monkeypatch)
    agent = build_agent(tmp_path)

    decision = await agent.respond("Não tem loja física?")

    assert decision.handoff is False
    assert "Sim, temos loja física" in decision.reply
    assert "Curitiba" in decision.reply
    assert "reserva" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_address_question_includes_appointment_notice(tmp_path, monkeypatch):
    freeze_weekday(monkeypatch)
    agent = build_agent(tmp_path)

    decision = await agent.respond("Qual o endereço da loja?")

    assert decision.handoff is False
    assert "Avenida Nossa Senhora da Luz, 1341" in decision.reply
    assert "horário marcado" in decision.reply
    assert "reserva" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_address_and_hours_question_returns_both_details(tmp_path, monkeypatch):
    freeze_weekday(monkeypatch)
    agent = build_agent(tmp_path)

    decision = await agent.respond("Qual o endereço e horario de atendimento de vcs?")

    assert decision.handoff is False
    assert "Avenida Nossa Senhora da Luz, 1341" in decision.reply
    assert "09:00" in decision.reply
    assert "horário marcado" in decision.reply


@pytest.mark.asyncio
async def test_informal_store_origin_question_returns_address_without_catalog_list(tmp_path, monkeypatch):
    freeze_weekday(monkeypatch)
    agent = build_agent(tmp_path)

    decision = await agent.respond("De onde é a loja de vcs?")

    assert decision.handoff is False
    assert "Avenida Nossa Senhora da Luz, 1341" in decision.reply
    assert "lista completa" not in decision.reply.lower()
