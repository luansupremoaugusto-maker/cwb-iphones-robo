from __future__ import annotations

import pytest

from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore
from app.runtime import build_runtime
from app.trade_in import (
    TRADE_IN_FORM,
    TRADE_IN_NEGOTIATION_REPLY,
    is_trade_in_negotiation,
    is_trade_in_context_request,
    is_trade_in_request,
    trade_in_em_andamento,
)
from app.adapters.mercado_phone import InventoryCache


def test_trade_in_detector_matches_part_payment_and_avoids_unrelated_exchange():
    assert is_trade_in_request("Vocês aceitam meu iPhone como parte do pagamento?")
    assert is_trade_in_request("Posso dar meu celular de entrada?")
    assert is_trade_in_request("Quero avaliação do meu aparelho usado")
    assert not is_trade_in_request("Quero trocar a película do meu iPhone")


def test_credit_limit_is_not_a_device_sale_offer():
    assert is_trade_in_request("To vendo meu limite") is False


def test_new_phone_payment_split_is_not_trade_in():
    text = (
        "Tenho interesse no iPhone 17 Pro Max. Fazer uma parte do pagamento "
        "a vista e parcelar o restante no cartao."
    )
    history = [
        {"role": "user", "content": "Quanto esta o iPhone 17 Pro Max?"},
        {"role": "assistant", "content": "iPhone 17 Pro Max 256 GB: R$ 7.800"},
    ]

    assert is_trade_in_request(text) is False
    assert is_trade_in_context_request(text, history) is False


@pytest.mark.parametrize(
    "text",
    [
        "quero comprar um iphone",
        "tem iphone usado?",
        "tem usado?",
        "vou dar 2000 de entrada",
        "a entrada vai ser em dinheiro e o restante no pix",
        "voces aceitam cartao?",
        "voces compram da Apple?",
        "voces compram Samsung?",
        "nao quero trocar, so comprar",
        "meu celular foi roubado, preciso comprar um novo",
        "buscar na loja",
    ],
)
def test_trade_in_guard_does_not_capture_purchase_stock_or_payment(text):
    assert is_trade_in_request(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "voces compram algum produto Apple?",
        "a loja compra celular usado?",
        "vc pegaria ele ainda como forma de pagamento?",
        "da pra usar ele de entrada?",
        "quero comprar um 15 novo e dar meu celular como entrada",
        "quero vender meu iphone 13",
        "tenho um iPhone 11 Pro Max para vender",
        "estou vendendo meu iPhone 11 Pro Max",
    ],
)
def test_trade_in_guard_keeps_buyback_and_device_entry_requests(text):
    assert is_trade_in_request(text) is True


def test_trade_in_history_marker_only_counts_assistant_form():
    assert trade_in_em_andamento(
        [{"role": "assistant", "content": TRADE_IN_FORM}]
    ) is True
    assert trade_in_em_andamento(
        [{"role": "user", "content": TRADE_IN_FORM}]
    ) is False
    assert is_trade_in_negotiation("vamos fechar R$ 400") is True
    assert is_trade_in_negotiation("buscar na loja") is False


@pytest.mark.asyncio
async def test_trade_in_negotiation_after_form_is_handed_off_without_price(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(
        "vamos fechar R$ 400",
        history=[{"role": "assistant", "content": TRADE_IN_FORM}],
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_NEGOTIATION_REPLY
    assert "400" not in decision.reply


@pytest.mark.asyncio
async def test_trade_in_response_is_form_and_handoff(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond("Vocês pegam meu iPhone como parte do pagamento?")

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM
    assert "Qual modelo de iPhone?" in decision.reply
    assert "Saúde da bateria" in decision.reply


@pytest.mark.asyncio
async def test_processor_pauses_trade_in_conversation_after_sending_form():
    settings = Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        outbound_mode="disabled",
    )
    runtime = build_runtime(settings, offline=True)
    try:
        await runtime.processor.process_payload(
            {
                "messageId": "trade-1",
                "phone": "5511999999999",
                "text": {"message": "Aceita meu celular como parte do pagamento?"},
            }
        )
        conversation = runtime.repository.get_conversation("5511999999999")
        messages = runtime.repository.recent_messages("5511999999999")
    finally:
        await runtime.aclose()

    assert conversation is not None and conversation.status == "human_pending"
    assert any(item["role"] == "assistant" and "Qual modelo de iPhone?" in item["content"] for item in messages)


def test_trade_in_context_detector_matches_abbreviated_model_followup():
    history = [
        {"role": "user", "content": "Quanto esta o iPhone 17 Pro Max?"},
        {
            "role": "assistant",
            "content": "iPhone 17 Pro Max por R$ 7.800. Qual capacidade voce deseja?",
        },
    ]

    assert is_trade_in_context_request("Tenho 14 quantos sera ficaria dai?", history)
    assert not is_trade_in_context_request("Tenho 14 anos, quanto falta?", history)


@pytest.mark.asyncio
async def test_trade_in_context_followup_returns_form_before_handoff(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(
        "Tenho 14 quantos sera ficaria dai?",
        history=[
            {"role": "user", "content": "Quanto esta o iPhone 17 Pro Max?"},
            {
                "role": "assistant",
                "content": "iPhone 17 Pro Max por R$ 7.800. Qual capacidade voce deseja?",
            },
        ],
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_trade_in_confirmation_returns_form_before_handoff(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(
        "Ok",
        history=[
            {
                "role": "assistant",
                "content": (
                    "Voce quer dar seu iPhone 14 como parte do pagamento? "
                    "A avaliacao depende do estado. Vou encaminhar para um atendente."
                ),
            }
        ],
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_owned_iphone_for_sale_returns_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond("Tenho um iPhone 11 Pro Max para vender.")

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM
    assert "Qual modelo de iPhone?" in decision.reply
