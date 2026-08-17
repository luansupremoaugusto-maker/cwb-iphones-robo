from __future__ import annotations

from app.agent import _ensure_trade_in_form_before_handoff
from app.schemas import AgentDecision
from app.trade_in import (
    CONDITION_HANDOFF_REPLY,
    PURCHASE_WITHOUT_TRADE_IN_REPLY,
    TRADE_IN_FORM,
    TRADE_IN_REASON,
    is_purchase_without_trade_in_request,
    is_trade_in_request,
)


def test_explicit_new_purchase_with_broken_phone_is_not_trade_in():
    text = (
        "comprar um novo, o meu acabou quebrando o visor e a tela de tras, "
        "ai acho que fica inviavel voces pegarem"
    )
    assert is_purchase_without_trade_in_request(text) is True
    assert is_trade_in_request(text) is False


def test_purchase_context_cannot_be_rewritten_as_trade_in_handoff():
    decision = _ensure_trade_in_form_before_handoff(
        AgentDecision(
            reply=TRADE_IN_FORM,
            handoff=True,
            handoff_reason=TRADE_IN_REASON,
        ),
        "comprar um novo, o meu acabou quebrando o visor e a tela de tras, "
        "ai acho que fica inviavel voces pegarem",
        [
            {"role": "user", "content": "estou querendo trocar de celular"},
            {
                "role": "assistant",
                "content": "Voce quer comprar um aparelho novo ou deseja dar seu celular Apple como parte do pagamento?",
            },
        ],
    )

    assert decision.reply == PURCHASE_WITHOUT_TRADE_IN_REPLY
    assert decision.handoff is False


def test_pix_pickup_price_offer_cannot_be_rewritten_as_trade_in_handoff():
    text = "Faz 3mil no Pix pra buscar?"
    image_description = "Imagem de um iPhone 14 Pro Max 128 GB anunciado pela loja"
    request_context = f"{text} {image_description}"

    assert is_purchase_without_trade_in_request(request_context) is True
    assert is_trade_in_request(request_context) is False

    decision = _ensure_trade_in_form_before_handoff(
        AgentDecision(
            reply=TRADE_IN_FORM,
            handoff=True,
            handoff_reason=TRADE_IN_REASON,
        ),
        text,
        [],
        image_description=image_description,
    )

    assert decision.handoff is False
    assert decision.reply != TRADE_IN_FORM


def test_condition_question_with_product_photo_keeps_generic_handoff():
    text = "O que e esse amassado no canto?\nMarcas de uso?"
    image_description = "Descricao visual da imagem recebida: iPhone 13 Pro seminovo"
    candidate_reply = "A avaliacao do iPhone depende do estado. Vou encaminhar para um atendente."

    assert is_trade_in_request(f"{text} {image_description}") is False

    decision = _ensure_trade_in_form_before_handoff(
        AgentDecision(
            reply=candidate_reply,
            handoff=True,
            handoff_reason=TRADE_IN_REASON,
        ),
        text,
        [],
        image_description=image_description,
    )

    assert decision.handoff is True
    assert decision.reply == CONDITION_HANDOFF_REPLY


def test_visual_port_question_does_not_keep_an_evaluation_form_from_the_model():
    text = "O que seria esse branco perto da entrada do carregador?"
    image_description = (
        "Descrição visual da imagem recebida: há um ponto branco perto da entrada "
        "do carregador de um aparelho Apple."
    )

    decision = _ensure_trade_in_form_before_handoff(
        AgentDecision(
            reply=TRADE_IN_FORM,
            handoff=True,
            handoff_reason=TRADE_IN_REASON,
        ),
        text,
        [],
        image_description=image_description,
    )

    assert decision.handoff is True
    assert decision.reply == CONDITION_HANDOFF_REPLY
    assert "lista de avaliação" not in decision.reply.lower()


def test_trade_in_offer_with_condition_details_still_uses_evaluation_form():
    text = "Quero vender meu iPhone 13, mas ele tem marcas de uso."

    assert is_trade_in_request(text) is True

    decision = _ensure_trade_in_form_before_handoff(
        AgentDecision(
            reply="A avaliação do iPhone depende do estado. Vou encaminhar para um atendente.",
            handoff=True,
            handoff_reason=TRADE_IN_REASON,
        ),
        text,
        [],
    )

    assert decision.reply == TRADE_IN_FORM
