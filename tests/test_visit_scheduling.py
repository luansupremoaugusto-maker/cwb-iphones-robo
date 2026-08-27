from __future__ import annotations

from datetime import datetime
import time
from zoneinfo import ZoneInfo

import pytest

import app.agent as agent_module
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
async def test_reservation_denial_offers_visit_without_reserving(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Tem como reservar o aparelho até segunda?")

    assert decision.handoff is False
    assert "não trabalhamos com reserva" in decision.reply.lower()
    assert "marcar" in decision.reply.lower()
    assert "não reserva o aparelho" not in decision.reply.lower()
    assert "cancelam" in decision.reply.lower()
    assert "deixamos de vender" in decision.reply.lower()


@pytest.mark.asyncio
async def test_visit_request_offers_today_on_weekday(tmp_path, monkeypatch):
    current = datetime(2026, 8, 10, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond("Gostaria de marcar um horário para visitar a loja.")

    assert decision.handoff is False
    assert "segunda-feira, 10/08/2026" in decision.reply
    assert "visita para hoje" in decision.reply.lower()
    assert "qual horário" in decision.reply.lower()
    assert "reserva" not in decision.reply.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Consigo te entregar um 16 pro Max hoje?",
        "Dá para ir lá vê hoje?",
    ],
)
async def test_visit_request_reports_closed_on_saturday(tmp_path, monkeypatch, text):
    current = datetime(2026, 8, 15, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond(text)

    assert decision.handoff is False
    assert "sábado, 15/08/2026" in decision.reply
    assert "fechada" in decision.reply.lower()
    assert "visita para hoje" not in decision.reply.lower()
    assert "dia de atendimento" in decision.reply.lower()


@pytest.mark.asyncio
async def test_current_day_question_includes_date_and_same_day_visit_offer(tmp_path, monkeypatch):
    current = datetime(2026, 8, 10, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond("Está aberto hoje?")

    assert decision.handoff is False
    assert "segunda-feira, 10/08/2026" in decision.reply
    assert "visita para hoje" in decision.reply.lower()
    assert "reserva" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_current_date_question_returns_weekday_and_date(tmp_path, monkeypatch):
    current = datetime(2026, 8, 10, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond("Que dia e hoje?")

    assert decision.handoff is False
    assert decision.reply == "Hoje é segunda-feira, 10/08/2026."


@pytest.mark.asyncio
async def test_current_day_question_reports_closed_on_weekend(tmp_path, monkeypatch):
    current = datetime(2026, 8, 9, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond("Está aberto hoje?")

    assert decision.handoff is False
    assert "domingo, 09/08/2026" in decision.reply
    assert "fechada" in decision.reply.lower()
    assert "visita para hoje" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_visit_followup_with_day_and_time_is_forwarded(tmp_path):
    agent = build_agent(tmp_path)
    initial = await agent.respond("Quero marcar uma visita à loja.")

    decision = await agent.respond(
        "Pode ser segunda-feira às 10h.",
        history=[
            {"role": "user", "content": "Quero marcar uma visita à loja."},
            {"role": "assistant", "content": initial.reply},
        ],
    )

    assert decision.handoff is True
    assert "solicitação da sua visita" in decision.reply
    assert "reserva" not in decision.reply.lower()
    assert "reserva" not in (decision.handoff_reason or "").lower()
    assert "agendamento" in (decision.handoff_reason or "").lower()


@pytest.mark.asyncio
async def test_visit_followup_with_compact_hour_is_forwarded(tmp_path):
    agent = build_agent(tmp_path)
    initial = await agent.respond("Quero marcar uma visita a loja.")

    decision = await agent.respond(
        "Amanha, 15h pode ser",
        history=[
            {"role": "user", "content": "Quero marcar uma visita a loja."},
            {"role": "assistant", "content": initial.reply},
        ],
    )

    assert decision.handoff is True
    assert "encaminhar para um atendente" in decision.reply.lower()
    assert "agendamento" in (decision.handoff_reason or "").lower()


@pytest.mark.asyncio
async def test_visit_followup_with_time_only_uses_explicit_today_offer(tmp_path, monkeypatch):
    current = datetime(2026, 8, 17, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    initial = await agent.respond("Tem que marcar horário?")
    decision = await agent.respond(
        "Pode ser às 17h?",
        history=[
            {"role": "user", "content": "Tem que marcar horário?"},
            {"role": "assistant", "content": initial.reply},
        ],
    )

    assert decision.handoff is True
    assert "solicitação da sua visita" in decision.reply
    assert "encaminhar para um atendente" in decision.reply.lower()
    assert "agendamento" in (decision.handoff_reason or "").lower()


@pytest.mark.asyncio
async def test_visit_followup_now_reports_closed_on_saturday(tmp_path, monkeypatch):
    current = datetime(2026, 8, 15, 16, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)
    initial = await agent.respond("Dá para ir lá vê hoje?")

    decision = await agent.respond(
        "Agora tem como?",
        history=[
            {"role": "user", "content": "Dá para ir lá vê hoje?"},
            {"role": "assistant", "content": initial.reply},
        ],
    )

    assert decision.handoff is False
    assert "sábado, 15/08/2026" in decision.reply
    assert "fechada" in decision.reply.lower()
    assert "visita para hoje" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_visit_followup_with_tomorrow_uses_tomorrow_instead_of_repeating_today(
    tmp_path, monkeypatch
):
    current = datetime(2026, 8, 18, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)
    initial = await agent.respond("Hoje não consigo ir até aí")

    decision = await agent.respond(
        "Vou deixar para ir amanhã!",
        history=[
            {"role": "user", "content": "Hoje não consigo ir até aí"},
            {"role": "assistant", "content": initial.reply},
        ],
    )

    assert decision.handoff is False
    assert "amanhã" in decision.reply.lower()
    assert "quarta-feira, 19/08/2026" in decision.reply
    assert "qual horário" in decision.reply.lower()
    assert "visita para hoje" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_visit_followup_with_tomorrow_does_not_offer_closed_store(
    tmp_path, monkeypatch
):
    current = datetime(2026, 8, 14, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)
    initial = await agent.respond("Hoje não consigo ir até aí")

    decision = await agent.respond(
        "Vou deixar para ir amanhã!",
        history=[
            {"role": "user", "content": "Hoje não consigo ir até aí"},
            {"role": "assistant", "content": initial.reply},
        ],
    )

    assert decision.handoff is False
    assert "sábado, 15/08/2026" in decision.reply
    assert "fechada" in decision.reply.lower()
    assert "visita para amanhã" not in decision.reply.lower()








@pytest.mark.asyncio
async def test_explicit_reservation_overrides_pending_visit_context(tmp_path):
    agent = build_agent(tmp_path)
    initial = await agent.respond("Quero marcar uma visita à loja.")

    decision = await agent.respond(
        "Tem como reservar o aparelho?",
        history=[
            {"role": "user", "content": "Quero marcar uma visita à loja."},
            {"role": "assistant", "content": initial.reply},
        ],
    )

    assert decision.handoff is False
    assert "não trabalhamos com reserva" in decision.reply.lower()
    assert "cancelam" in decision.reply.lower()
    assert "deixamos de vender" in decision.reply.lower()


@pytest.mark.asyncio
async def test_new_product_question_overrides_stale_visit_prompt(tmp_path, monkeypatch):
    current = datetime(2026, 8, 10, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    settings = Settings(faq_path="data/faq.yaml")
    cache = InventoryCache(
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
            search_text="iphone 12 preto 128gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    initial = await agent.respond("Estao abertos hoje?")
    decision = await agent.respond(
        "Ainda tem o 12?",
        history=[
            {"role": "user", "content": "Estao abertos hoje?"},
            {"role": "assistant", "content": initial.reply},
        ],
    )

    assert "IPHONE 12" in decision.reply
    assert "visita" not in decision.reply.lower()
    assert "09:00" not in decision.reply


@pytest.mark.asyncio
async def test_generic_hours_question_uses_faq_and_marked_appointment(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Ate que horario voces atendem?")

    assert decision.handoff is False
    assert "09:00" in decision.reply
    assert "18:00" in decision.reply
    assert "marcad" in decision.reply.lower()
    assert "confirmar" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_hours_followup_after_ipad_installment_reply_uses_faq_without_handoff(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Perfeito qual horario de funcionamento de vcs?",
        history=[
            {
                "role": "user",
                "content": "Para fazer em 12x quanto fica por favor e prazo para retirar?",
            },
            {
                "role": "assistant",
                "content": (
                    "Bom dia! 😊 Para o iPad 11 128 GB novo lacrado: preço à vista R$ 3.100,00. "
                    "Parcelamento no cartão de crédito na máquina física. O novo lacrado é por "
                    "encomenda, com prazo de 1 semana. A retirada é feita na loja com horário marcado."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "09:00" in decision.reply
    assert "18:00" in decision.reply
    assert "horário marcado" in decision.reply
    assert "atendente" not in decision.reply.lower()
