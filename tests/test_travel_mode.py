from __future__ import annotations

from datetime import datetime
import time
from zoneinfo import ZoneInfo

import pytest

import app.agent as agent_module
from app.adapters.catalog_cache import StoreCatalogCache
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore
from app.schemas import AgentDecision, InventoryItem


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


STORE_TZ = ZoneInfo("America/Sao_Paulo")


def build_agent(tmp_path, *, items: list[InventoryItem] | None = None):
    settings = Settings(
        mercado_cache_ttl_seconds=60,
        faq_path="data/faq.yaml",
    )
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    if items:
        cache.items = items
        cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


def freeze_store_clock(monkeypatch, current: datetime):
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current", "travel_active"),
    [
        (datetime(2026, 8, 28, 10, 0, tzinfo=STORE_TZ), False),
        (datetime(2026, 8, 29, 10, 0, tzinfo=STORE_TZ), True),
        (datetime(2026, 9, 9, 10, 0, tzinfo=STORE_TZ), True),
        (datetime(2026, 9, 10, 0, 0, tzinfo=STORE_TZ), False),
    ],
)
async def test_travel_mode_has_inclusive_dates_and_resumes_on_september_10(
    tmp_path, monkeypatch, current, travel_active
):
    freeze_store_clock(monkeypatch, current)
    agent = build_agent(tmp_path)

    decision = await agent.respond("Vocês entregam por motoboy?")

    assert decision.handoff is False
    if travel_active:
        assert "10/09/2026" in decision.reply
        assert "envios" in decision.reply.lower()
    else:
        assert "Enviamos para Curitiba" in decision.reply
        assert "10/09/2026" not in decision.reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Vocês entregam por motoboy?",
        "Quero retirar o aparelho na loja.",
        "Quero marcar uma visita para amanhã.",
    ],
)
async def test_travel_mode_defers_operations_without_handoff(tmp_path, monkeypatch, text):
    freeze_store_clock(monkeypatch, datetime(2026, 8, 29, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond(text)

    assert decision.handoff is False
    assert "10/09/2026" in decision.reply
    assert "retom" in decision.reply.lower()
    assert "reserva" not in decision.reply.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Quero uma visita na loja.",
        "Tem como ir aí?",
        "Vou na loja hoje.",
    ],
)
async def test_travel_mode_catches_colloquial_visit_requests_without_handoff(
    tmp_path, monkeypatch, text
):
    freeze_store_clock(monkeypatch, datetime(2026, 8, 29, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond(text)

    assert decision.handoff is False
    assert "retiradas" in decision.reply.lower()
    assert "10/09/2026" in decision.reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Vocês enviam para São Paulo?",
        "Vocês mandam para fora de Curitiba?",
        "Tem frete?",
        "Qual a taxa do frete?",
    ],
)
async def test_travel_mode_defers_shipping_variations_without_handoff(
    tmp_path, monkeypatch, text
):
    freeze_store_clock(monkeypatch, datetime(2026, 8, 31, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond(text)

    assert decision.handoff is False
    assert "envios" in decision.reply.lower()
    assert "10/09/2026" in decision.reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "travel_notice_expected"),
    [
        ("Vocês enviam iPhone 17 para Curitiba?", True),
        ("Posso buscar o aparelho na loja?", True),
        ("Pode mandar foto do iPhone 17 lacrado?", False),
    ],
)
async def test_travel_mode_distinguishes_shipping_from_product_photos(
    tmp_path, monkeypatch, text, travel_notice_expected
):
    freeze_store_clock(monkeypatch, datetime(2026, 8, 31, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond(text)

    assert decision.handoff is False
    if travel_notice_expected:
        assert "10/09/2026" in decision.reply
    else:
        assert "fotos do produto" in decision.reply.lower()
        assert "10/09/2026" not in decision.reply


@pytest.mark.asyncio
async def test_travel_mode_does_not_treat_generic_photo_verb_as_shipping(
    tmp_path, monkeypatch
):
    freeze_store_clock(monkeypatch, datetime(2026, 8, 31, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond("Pode mandar foto do aparelho?")

    assert decision.handoff is False
    assert "10/09/2026" not in decision.reply


@pytest.mark.asyncio
async def test_travel_mode_keeps_photo_context_about_past_delivery_online(
    tmp_path, monkeypatch
):
    freeze_store_clock(monkeypatch, datetime(2026, 8, 31, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond("Tem foto do aparelho que foi entregue?")

    assert decision.handoff is False
    assert "10/09/2026" not in decision.reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Vocês estão atendendo normalmente?",
        "Quando volta o atendimento presencial?",
        "Está aberto agora?",
        "Que dia reabre?",
    ],
)
async def test_travel_mode_explains_when_service_resumes(tmp_path, monkeypatch, text):
    freeze_store_clock(monkeypatch, datetime(2026, 9, 1, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond(text)

    assert decision.handoff is False
    assert "10/09/2026" in decision.reply
    assert "atendente" in decision.reply.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Quero falar com um atendente.",
        "Quero falar com uma pessoa.",
        "Quero acionar a garantia porque meu iPhone não funciona.",
    ],
)
async def test_travel_mode_defers_explicit_human_and_warranty_problem_without_handoff(
    tmp_path, monkeypatch, text
):
    freeze_store_clock(monkeypatch, datetime(2026, 9, 9, 23, 59, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond(text)

    assert decision.handoff is False
    assert "10/09/2026" in decision.reply
    assert "atendente" in decision.reply.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Vocês fazem assistência técnica?",
        "Vocês fazem assistência?",
        "Preciso de assistência com meu iPhone.",
        "Meu iPhone está com problema.",
        "Meu celular deu problema.",
        "Vocês pegam meu iPhone 13 na troca?",
        "Quero acionar a garantia.",
    ],
)
async def test_travel_mode_does_not_auto_route_non_explicit_services(
    tmp_path, monkeypatch, text
):
    freeze_store_clock(monkeypatch, datetime(2026, 9, 2, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond(text)

    assert decision.handoff is False
    assert "informações sobre estoque" in decision.reply.lower()
    assert "10/09/2026" in decision.reply
    assert "vou encaminhar" not in decision.reply.lower()
    assert "problemas, acionamentos de garantia e assistência técnica" not in decision.reply.lower()
    assert "confirmações com um atendente" not in decision.reply.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Meu iPhone não funciona e quero acionar a garantia.",
        "O aparelho deu defeito, preciso usar a garantia.",
        "Quero reclamar do defeito do meu iPhone pela garantia.",
        "Meu iPhone não liga e está na garantia.",
        "Meu celular deu problema e está na garantia.",
    ],
)
async def test_travel_mode_defers_warranty_problems_without_handoff(
    tmp_path, monkeypatch, text
):
    freeze_store_clock(monkeypatch, datetime(2026, 9, 2, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond(text)

    assert decision.handoff is False
    assert "problemas" in decision.reply.lower()
    assert "atendente" in decision.reply.lower()
    assert "10/09/2026" in decision.reply


@pytest.mark.asyncio
async def test_travel_mode_keeps_product_price_in_mixed_shipping_question(tmp_path, monkeypatch):
    freeze_store_clock(monkeypatch, datetime(2026, 8, 31, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(
        tmp_path,
        items=[
            InventoryItem(
                external_id="iphone-13-128",
                name="iPhone 13",
                category="Celular",
                capacity="128GB",
                color="PRETO",
                condition="SEMINOVO",
                availability="Disponível para venda",
                quantity=1,
                price_brl=2200.0,
                battery_health=92,
                search_text="iphone 13 128gb preto celular seminovo",
            )
        ],
    )

    decision = await agent.respond("Vocês entregam por motoboy e quanto custa o iPhone 13?")

    assert decision.handoff is False
    assert "iPhone 13" in decision.reply
    assert "R$" in decision.reply
    assert "10/09/2026" in decision.reply


@pytest.mark.asyncio
async def test_travel_mode_keeps_product_price_when_questions_use_punctuation(
    tmp_path, monkeypatch
):
    freeze_store_clock(monkeypatch, datetime(2026, 8, 31, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(
        tmp_path,
        items=[
            InventoryItem(
                external_id="iphone-13-128",
                name="iPhone 13",
                category="Celular",
                capacity="128GB",
                color="PRETO",
                condition="SEMINOVO",
                availability="Disponível para venda",
                quantity=1,
                price_brl=2200.0,
                battery_health=92,
                search_text="iphone 13 128gb preto celular seminovo",
            )
        ],
    )

    decision = await agent.respond(
        "Quanto custa o iPhone 13? Quero retirar na loja."
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-13-128"]
    assert "R$" in decision.reply
    assert "10/09/2026" in decision.reply


@pytest.mark.asyncio
async def test_travel_mode_keeps_confirmed_catalog_information_online(tmp_path, monkeypatch):
    freeze_store_clock(monkeypatch, datetime(2026, 8, 30, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(
        tmp_path,
        items=[
            InventoryItem(
                external_id="iphone-13-128",
                name="iPhone 13",
                category="Celular",
                capacity="128GB",
                color="PRETO",
                condition="SEMINOVO",
                availability="Disponível para venda",
                quantity=1,
                price_brl=2200.0,
                battery_health=92,
                search_text="iphone 13 128gb preto celular seminovo",
            )
        ],
    )

    decision = await agent.respond("Tem iPhone 13 128GB?")

    assert decision.handoff is False
    assert "iPhone 13" in decision.reply
    assert "R$" in decision.reply
    assert "10/09/2026" not in decision.reply


@pytest.mark.asyncio
async def test_travel_mode_keeps_general_warranty_information_online(tmp_path, monkeypatch):
    freeze_store_clock(monkeypatch, datetime(2026, 9, 1, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond("Qual é a garantia dos aparelhos?")

    assert decision.handoff is False
    assert "90 dias" in decision.reply
    assert "1 ano" in decision.reply
    assert "10/09/2026" not in decision.reply


@pytest.mark.asyncio
async def test_travel_mode_keeps_address_and_hours_but_blocks_in_person_service(
    tmp_path, monkeypatch
):
    freeze_store_clock(monkeypatch, datetime(2026, 9, 2, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond("Qual o endereço da loja e horário de atendimento?")

    assert decision.handoff is False
    assert "Avenida Nossa Senhora da Luz, 1341" in decision.reply
    assert "09:00" in decision.reply
    assert "18:00" in decision.reply
    assert "10/09/2026" in decision.reply
    assert "atendimento presencial" in decision.reply.lower()


@pytest.mark.asyncio
async def test_travel_mode_does_not_report_store_open_for_today(tmp_path, monkeypatch):
    freeze_store_clock(monkeypatch, datetime(2026, 9, 3, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond("Está aberto hoje?")

    assert decision.handoff is False
    assert "10/09/2026" in decision.reply
    assert "atendimento presencial" in decision.reply.lower()
    assert "atendemos hoje das 09:00 às 18:00" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_travel_mode_suppresses_unexpected_fallback_handoff(tmp_path, monkeypatch):
    freeze_store_clock(monkeypatch, datetime(2026, 9, 4, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    async def unexpected_handoff(self, text, history=None, image_description=None):
        return AgentDecision(
            reply="Vou encaminhar sua mensagem para um atendente.",
            handoff=True,
            handoff_reason="fallback",
        )

    monkeypatch.setattr(AgentService, "_offline_response", unexpected_handoff)

    decision = await agent.respond("Preciso de uma ajuda que não está no FAQ.")

    assert decision.handoff is False
    assert "10/09/2026" in decision.reply
    assert "informações sobre estoque" in decision.reply.lower()
    assert "vou encaminhar" not in decision.reply.lower()
    assert "confirmações com um atendente" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_travel_mode_is_removed_after_resume_date(tmp_path, monkeypatch):
    freeze_store_clock(monkeypatch, datetime(2026, 9, 10, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond("Vocês fazem assistência técnica?")

    assert decision.handoff is True
    assert "assistência técnica" in decision.reply.lower()
    assert "retomado em 10/09/2026" not in decision.reply.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Meu celular não carrega.",
        "Meu iPhone não está carregando.",
        "Meu aparelho parou de carregar.",
    ],
)
async def test_travel_mode_treats_charging_failure_as_service_issue(
    tmp_path, monkeypatch, text
):
    freeze_store_clock(monkeypatch, datetime(2026, 9, 2, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(
        tmp_path,
        items=[
            InventoryItem(
                external_id="iphone-13-128",
                name="iPhone 13",
                category="Celular",
                capacity="128GB",
                color="PRETO",
                condition="SEMINOVO",
                availability="Disponível para venda",
                quantity=1,
                price_brl=2200.0,
                battery_health=92,
                search_text="iphone 13 128gb preto celular seminovo",
            )
        ],
    )

    decision = await agent.respond(text)

    assert decision.handoff is False
    assert decision.product_references == []
    assert "Encontrei estas opções" not in decision.reply
    assert "informações sobre estoque" in decision.reply.lower()
    assert "10/09/2026" in decision.reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Meu iPhone não carrega e está na garantia.",
        "Na garantia, meu celular parou de carregar.",
    ],
)
async def test_travel_mode_defers_warranty_charging_failure_to_resume_date(
    tmp_path, monkeypatch, text
):
    freeze_store_clock(monkeypatch, datetime(2026, 9, 2, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond(text)

    assert decision.handoff is False
    assert "problemas no aparelho relacionados à garantia" in decision.reply.lower()
    assert "10/09/2026" in decision.reply


@pytest.mark.asyncio
async def test_travel_mode_keeps_catalog_answer_when_human_request_is_explicitly_mixed(
    tmp_path, monkeypatch
):
    freeze_store_clock(monkeypatch, datetime(2026, 9, 2, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(
        tmp_path,
        items=[
            InventoryItem(
                external_id="iphone-13-128",
                name="iPhone 13",
                category="Celular",
                capacity="128GB",
                color="PRETO",
                condition="SEMINOVO",
                availability="Disponível para venda",
                quantity=1,
                price_brl=2200.0,
                battery_health=92,
                search_text="iphone 13 128gb preto celular seminovo",
            )
        ],
    )

    decision = await agent.respond(
        "Quero falar com um atendente. Tem iPhone 13 128GB?"
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-13-128"]
    assert "iPhone 13" in decision.reply
    assert "R$" in decision.reply
    assert "atendimento" in decision.reply.lower()
    assert "10/09/2026" in decision.reply


@pytest.mark.asyncio
async def test_travel_mode_keeps_catalog_answer_when_warranty_problem_is_mixed(
    tmp_path, monkeypatch
):
    freeze_store_clock(monkeypatch, datetime(2026, 9, 2, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(
        tmp_path,
        items=[
            InventoryItem(
                external_id="iphone-13-128",
                name="iPhone 13",
                category="Celular",
                capacity="128GB",
                color="PRETO",
                condition="SEMINOVO",
                availability="Disponível para venda",
                quantity=1,
                price_brl=2200.0,
                battery_health=92,
                search_text="iphone 13 128gb preto celular seminovo",
            )
        ],
    )

    decision = await agent.respond(
        "Meu iPhone não carrega e está na garantia. Tem iPhone 13 128GB?"
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-13-128"]
    assert "problemas no aparelho relacionados à garantia" in decision.reply.lower()
    assert "iPhone 13" in decision.reply
    assert "R$" in decision.reply
    assert "10/09/2026" in decision.reply


@pytest.mark.asyncio
async def test_travel_mode_defers_handoff_confirmation_from_history(
    tmp_path, monkeypatch
):
    freeze_store_clock(monkeypatch, datetime(2026, 9, 2, 10, 0, tzinfo=STORE_TZ))
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Sim, por favor.",
        history=[
            {
                "role": "assistant",
                "content": "Posso encaminhar seu pedido para um atendente finalizar?",
            }
        ],
    )

    assert decision.handoff is False
    assert "atendimento" in decision.reply.lower()
    assert "10/09/2026" in decision.reply
