from __future__ import annotations

import re

from app.schemas import AgentDecision


SENSITIVE_REPLY_RE = re.compile(
    r"\b(?:valor\s*custo|fornecedor|imei2?|serial\s*number|serialnumber|"
    r"n[úu]mero\s+de\s+s[ée]rie|id\s+interno|identificador\s+interno)\b",
    re.IGNORECASE,
)


def protect_customer_decision(decision: AgentDecision) -> AgentDecision:
    if SENSITIVE_REPLY_RE.search(decision.reply):
        return AgentDecision(
            reply="Vou encaminhar sua mensagem para um atendente confirmar essa informação.",
            handoff=True,
            handoff_reason="Resposta bloqueada por conter informação restrita",
            confidence="low",
        )
    return decision
