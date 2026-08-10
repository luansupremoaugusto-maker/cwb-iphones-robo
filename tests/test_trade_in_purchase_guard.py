from __future__ import annotations

from app.agent import _ensure_trade_in_form_before_handoff
from app.schemas import AgentDecision
from app.trade_in import (
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
