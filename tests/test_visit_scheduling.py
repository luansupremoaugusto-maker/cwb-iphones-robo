from __future__ import annotations

from datetime import datetime
import time
from zoneinfo import ZoneInfo

import pytest

import app.agent as agent_module
from app.adapters.mercado_phone import InventoryCache
from app.agent import AgentService, _normalize
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
async def test_unable_to_visit_after_delivery_question_does_not_offer_appointment(tmp_path):
    agent = build_agent(tmp_path)
    history = [
        {"role": "user", "content": "Qual é o endereço de vocês?"},
        {
            "role": "assistant",
            "content": (
                "Estamos na Avenida Nossa Senhora da Luz, 1341. Posso marcar uma "
                "visita para hoje? Qual horário fica melhor para você?"
            ),
        },
        {
            "role": "user",
            "content": "E se acaso fosse para vocês entregarem na minha casa, como faríamos?",
        },
        {
            "role": "assistant",
            "content": (
                "Se for em Curitiba ou região, podemos enviar por motoboy; para outras "
                "cidades, por Sedex. O pagamento precisa ser feito antes do envio; não "
                "fazemos pagamento na entrega."
            ),
        },
    ]

    decision = await agent.respond("Não consigo ir até a loja, não dá tempo", history=history)
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "motoboy" in reply
    assert "antecipado antes do despacho" in reply
    assert "nao e possivel pagar na entrega" in reply
    assert "visita" not in reply
    assert "qual horario" not in reply


@pytest.mark.asyncio
async def test_future_visit_plan_overrides_no_time_to_visit_today(tmp_path, monkeypatch):
    current = datetime(2026, 8, 18, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)
    initial = await agent.respond("Gostaria de marcar uma visita para hoje.")
    history = [
        {"role": "user", "content": "Gostaria de marcar uma visita para hoje."},
        {"role": "assistant", "content": initial.reply},
        {
            "role": "user",
            "content": "E se acaso fosse para vocês entregarem na minha casa?",
        },
        {
            "role": "assistant",
            "content": (
                "Enviamos para Curitiba e região por motoboy. O pagamento deve ser "
                "antecipado antes do despacho."
            ),
        },
    ]

    decision = await agent.respond(
        "Não dá tempo de ir hoje, vou deixar para ir amanhã.",
        history=history,
    )

    assert decision.handoff is False
    assert "quarta-feira, 19/08/2026" in decision.reply
    assert "visita para amanhã" in decision.reply.lower()
    assert "qual horário" in decision.reply.lower()


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
@pytest.mark.parametrize(
    "text",
    [
        "Hoje você trabalha?",
        "Oiii\nHoje você trabalha?",
        "Vocês trabalham hoje?",
    ],
)
async def test_working_today_question_ignores_unrelated_catalog_and_trade_in_history(
    tmp_path, monkeypatch, text
):
    current = datetime(2026, 8, 9, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)
    history = [
        {"role": "user", "content": "Tem M5 1T prata?"},
        {
            "role": "assistant",
            "content": "MacBook Air 2026 Apple M5 prata, SSD 1 TB.",
        },
        {"role": "user", "content": "Também tenho um iPhone 16 Pro Max para avaliação."},
        {
            "role": "assistant",
            "content": "Envie os dados do iPhone para a avaliação de troca.",
        },
    ]

    decision = await agent.respond(text, history=history)

    assert decision.handoff is False
    assert decision.product_references == []
    assert "domingo, 09/08/2026" in decision.reply
    assert "fechada" in decision.reply.lower()
    assert "macbook" not in decision.reply.lower()
    assert "m5" not in decision.reply.lower()
    assert "iphone 16" not in decision.reply.lower()
    assert "avaliação" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_working_today_and_visit_request_keeps_scheduling_route(tmp_path, monkeypatch):
    current = datetime(2026, 8, 10, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Hoje você trabalha? Quero agendar uma visita hoje às 16h."
    )

    assert decision.handoff is True
    assert "registrar a solicitação da sua visita" in decision.reply.lower()


@pytest.mark.asyncio
async def test_colloquial_attendem_hoje_question_reports_closed_on_saturday(
    tmp_path, monkeypatch
):
    current = datetime(2026, 9, 19, 9, 58, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Atendem hoje?",
        history=[
            {"role": "user", "content": "Bom dia"},
            {
                "role": "assistant",
                "content": (
                    "Agradecemos sua mensagem. Não estamos disponíveis no momento, "
                    "mas entraremos em contato assim que possível."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "sábado, 19/09/2026" in decision.reply
    assert "hoje a loja está fechada" in decision.reply.lower()
    assert "atendemos hoje das 09:00 às 18:00" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_time_only_followup_after_closed_hours_question_does_not_schedule_today(
    tmp_path, monkeypatch
):
    current = datetime(2026, 9, 19, 9, 58, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)
    history = [
        {"role": "user", "content": "Bom dia"},
        {
            "role": "assistant",
            "content": (
                "Agradecemos sua mensagem. Não estamos disponíveis no momento, "
                "mas entraremos em contato assim que possível."
            ),
        },
        {"role": "user", "content": "Atendem hoje?"},
    ]
    closed_reply = await agent.respond("Atendem hoje?", history=history[:2])
    history.append({"role": "assistant", "content": closed_reply.reply})

    decision = await agent.respond("As 15h", history=history)

    assert "hoje a loja está fechada" in closed_reply.reply.lower()
    assert decision.handoff is False
    assert "sábado, 19/09/2026" in decision.reply
    assert "dia de atendimento" in decision.reply.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Está aberto a loja?", "A loja está aberta?"])
async def test_unqualified_store_open_question_uses_current_store_date(tmp_path, monkeypatch, text):
    current = datetime(2026, 9, 14, 14, 31, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond(text)

    assert decision.handoff is False
    assert "segunda-feira, 14/09/2026" in decision.reply
    assert "Atendemos hoje" in decision.reply
    assert "domingo" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_store_open_question_with_hours_uses_faq_hours(tmp_path, monkeypatch):
    current = datetime(2026, 9, 14, 14, 31, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond("A loja está aberta até que horas?")

    assert decision.handoff is False
    assert "Hoje é" not in decision.reply
    assert "09:00" in decision.reply
    assert "18:00" in decision.reply


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
async def test_visit_followup_today_abbreviation_reports_closed_on_saturday(
    tmp_path, monkeypatch
):
    current = datetime(2026, 10, 3, 15, 34, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Daria hj?",
        history=[
            {"role": "user", "content": "Sim por gentileza"},
            {
                "role": "assistant",
                "content": "Claro! 😊 Qual dia e horário você prefere para solicitar a visita?",
            },
        ],
    )

    assert decision.handoff is False
    assert "sábado, 03/10/2026" in decision.reply.lower()
    assert "fechada" in decision.reply.lower()
    assert "visita para hoje" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_available_now_after_today_visit_offer_reports_closed_on_saturday(
    tmp_path, monkeypatch
):
    current = datetime(2026, 10, 3, 15, 34, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Estamos disponíveis agr",
        history=[
            {"role": "user", "content": "Sim por gentileza"},
            {
                "role": "assistant",
                "content": "Claro! 😊 Qual dia e horário você prefere para solicitar a visita?",
            },
            {"role": "user", "content": "Daria hj?"},
            {
                "role": "assistant",
                "content": (
                    "Posso solicitar para hoje, sim 😊 Atendemos de segunda a sexta, "
                    "das 9h às 18h, sempre com horário marcado. Qual horário você prefere? "
                    "A visita precisa ser confirmada pelo atendente."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "sábado, 03/10/2026" in decision.reply.lower()
    assert "fechada" in decision.reply.lower()
    assert "solicitação da sua visita" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_visit_followup_with_closed_day_and_time_is_not_forwarded(
    tmp_path, monkeypatch
):
    current = datetime(2026, 10, 3, 15, 34, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)
    initial = await agent.respond("Quero marcar uma visita à loja.")

    decision = await agent.respond(
        "Pode ser sábado às 15h?",
        history=[
            {"role": "user", "content": "Quero marcar uma visita à loja."},
            {"role": "assistant", "content": initial.reply},
        ],
    )

    assert decision.handoff is False
    assert "sábado, 03/10/2026" in decision.reply.lower()
    assert "fechada" in decision.reply.lower()
    assert "solicitação da sua visita" not in decision.reply.lower()


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
async def test_reservation_and_friday_visit_request_explains_policy_and_respects_day(
    tmp_path, monkeypatch
):
    current = datetime(2026, 9, 30, 17, 10, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Só sexta-feira consigo ir aí. Teria alguma forma de me garantir/reservar ele?",
        history=[
            {
                "role": "user",
                "content": "O valor integral do 16 Pro fica em R$ 4.560,00 mesmo, então?",
            },
            {
                "role": "assistant",
                "content": (
                    "Sim 😊 Encontrei estas opções de IPHONE 16 PRO disponíveis: "
                    "IPHONE 16 PRO — TITÂNIO DESERTO — 256GB — SEMINOVO — "
                    "R$ 4.560,00 | Bat: 90%"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "não trabalhamos com reserva" in decision.reply.lower()
    assert "cancelam" in decision.reply.lower()
    assert "deixamos de vender" in decision.reply.lower()
    assert "não consigo garantir" in decision.reply.lower()
    assert "sexta-feira, 02/10/2026" in decision.reply.lower()
    assert "09:00" in decision.reply
    assert "18:00" in decision.reply
    assert "qual horário" in decision.reply.lower()
    assert "visita para hoje" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_friday_visit_followup_does_not_repeat_invitation_for_today(
    tmp_path, monkeypatch
):
    current = datetime(2026, 9, 30, 17, 11, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Só consigo na sexta-feira!",
        history=[
            {
                "role": "user",
                "content": (
                    "Só sexta-feira consigo ir aí. Teria alguma forma de me garantir/reservar ele?"
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "Sim, temos loja física. Hoje é quarta-feira, 30/09/2026. "
                    "Atendemos hoje das 09:00 às 18:00, com horário marcado. "
                    "Posso marcar uma visita para hoje? Qual horário fica melhor para você?"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "não trabalhamos com reserva" in decision.reply.lower()
    assert "cancelam" in decision.reply.lower()
    assert "sexta-feira, 02/10/2026" in decision.reply.lower()
    assert "09:00" in decision.reply
    assert "18:00" in decision.reply
    assert "qual horário" in decision.reply.lower()
    assert "visita para hoje" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_reservation_with_tomorrow_uses_tomorrow_when_today_not_open(
    tmp_path, monkeypatch
):
    current = datetime(2026, 10, 2, 17, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond("Só amanhã consigo ir aí. Tem como reservar o aparelho?")

    assert decision.handoff is False
    assert "não trabalhamos com reserva" in decision.reply.lower()
    assert "sábado, 03/10/2026" in decision.reply.lower()
    assert "fechada" in decision.reply.lower()
    assert "visita para hoje" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_reservation_for_weekday_next_week_uses_that_calendar_week(
    tmp_path, monkeypatch
):
    current = datetime(2026, 9, 30, 17, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Só segunda da próxima semana consigo ir aí. Tem como reservar o aparelho?"
    )

    assert decision.handoff is False
    assert "segunda-feira, 05/10/2026" in decision.reply.lower()
    assert "segunda-feira, 12/10/2026" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_reservation_only_for_tomorrow_uses_tomorrow_date(tmp_path, monkeypatch):
    current = datetime(2026, 10, 2, 17, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond("Tem como reservar o aparelho para amanhã?")

    assert decision.handoff is False
    assert "sábado, 03/10/2026" in decision.reply.lower()
    assert "fechada" in decision.reply.lower()
    assert "visita para hoje" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_reservation_only_for_next_week_uses_that_calendar_week(tmp_path, monkeypatch):
    current = datetime(2026, 9, 30, 17, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond("Tem como reservar para segunda da próxima semana?")

    assert decision.handoff is False
    assert "segunda-feira, 05/10/2026" in decision.reply.lower()
    assert "segunda-feira, 12/10/2026" not in decision.reply.lower()


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
async def test_abbreviated_hours_question_after_catalog_offer_returns_faq_hours(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "até q hrs vcs ficam abertos?",
        history=[
            {
                "role": "assistant",
                "content": (
                    "Esse não interessaria? iPhone 14 Pro Max 128 GB seminovo "
                    "por R$ 3.080,00."
                ),
            },
            {"role": "user", "content": "Oi"},
        ],
    )

    assert decision.handoff is False
    assert "09:00" in decision.reply
    assert "18:00" in decision.reply
    assert "horário marcado" in decision.reply.lower()
    assert "confirmar" not in decision.reply.lower()
    assert "atendente" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_today_hours_question_with_whatsapp_shorthand_returns_real_hours(
    tmp_path, monkeypatch
):
    current = datetime(2026, 8, 14, 14, 57, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Até q horas a loja de vcs ficam abertas hj?",
        history=[
            {"role": "user", "content": "Posso pagar on-line agora ?"},
            {
                "role": "assistant",
                "content": (
                    "Não aceitamos pagamento por cartão online ou link. 😊\n"
                    "Se for retirar na loja, o pagamento é feito na hora, com horário marcado. "
                    "Para envio, o pagamento deve ser antecipado antes do despacho; aceitamos "
                    "PIX, dinheiro ou cartão na máquina física. A taxa e o prazo de entrega "
                    "precisam ser cotados com um atendente."
                ),
            },
            {"role": "user", "content": "Pq n vou conseguir ir buscar hoje"},
            {
                "role": "assistant",
                "content": (
                    "Não aceitamos pagamento por cartão online ou link. 😊\n"
                    "Se for retirar na loja, o pagamento é feito na hora, com horário marcado. "
                    "Para envio, o pagamento deve ser antecipado antes do despacho; aceitamos "
                    "PIX, dinheiro ou cartão na máquina física. A taxa e o prazo de entrega "
                    "precisam ser cotados com um atendente."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "sexta-feira, 14/08/2026" in decision.reply
    assert "09:00" in decision.reply
    assert "18:00" in decision.reply
    assert "confirmar" not in decision.reply.lower()
    assert "não localizei esse produto" not in decision.reply.lower()


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


@pytest.mark.asyncio
async def test_explicit_saturday_hours_question_after_address_reply_uses_faq_hours(
    tmp_path, monkeypatch
):
    current = datetime(2026, 9, 17, 14, 50, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "vcs abrem no sabado tbm ?",
        history=[
            {"role": "user", "content": "qual endereço da loja"},
            {
                "role": "assistant",
                "content": (
                    "Sim, temos loja física. Hoje é quinta-feira, 17/09/2026. "
                    "Atendemos hoje das 09:00 às 18:00, com horário marcado. "
                    "Endereço: Avenida Nossa Senhora da Luz, 1341 - Jardim Social, "
                    "Curitiba - PR, 82520-060. Posso marcar uma visita para hoje? "
                    "Qual horário fica melhor para você?"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "sábado" in decision.reply.lower()
    assert "fechad" in decision.reply.lower()
    assert "09:00" in decision.reply
    assert "18:00" in decision.reply
    assert "hoje é" not in decision.reply.lower()
    assert "visita para hoje" not in decision.reply.lower()
