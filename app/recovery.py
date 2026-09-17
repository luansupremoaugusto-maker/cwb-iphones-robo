from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.schemas import AgentDecision


RECOVERY_CATEGORY_LABELS = {
    "compra": "Compra, preço ou estoque",
    "pagamento": "Pagamento ou parcelamento",
    "entrega": "Entrega ou retirada",
    "manual": "Revisão manual",
    "outros": "Dúvida geral",
}

_IMEI_RE = re.compile(r"(?i)\bimei(?:\s*2)?\s*[:=-]?\s*[0-9A-Za-z-]{6,}")
_SERIAL_RE = re.compile(r"(?i)\b(?:sn|serial(?:number)?)\s*[:=-]?\s*[0-9A-Za-z-]{4,}")


def _fold(value: str | None) -> str:
    plain = "".join(
        char
        for char in unicodedata.normalize("NFKD", value or "")
        if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", plain).strip().lower()


def clean_recovery_text(value: str | None, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = _IMEI_RE.sub("IMEI [oculto]", text)
    text = _SERIAL_RE.sub("serial [oculto]", text)
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def classify_recovery_message(
    text: str | None,
    decision: AgentDecision | None = None,
) -> tuple[str, str]:
    """Return a small, review-oriented category for the recovery queue."""
    normalized = _fold(text)
    if decision is not None and (decision.handoff or decision.confidence == "low"):
        key = "manual"
    elif any(marker in normalized for marker in ("pix", "cartao", "cartão", "parcela", "parcelado", "juros")):
        key = "pagamento"
    elif any(marker in normalized for marker in ("entrega", "motoboy", "sedex", "retirar", "retirada", "buscar")):
        key = "entrega"
    elif any(
        marker in normalized
        for marker in (
            "iphone",
            "preco",
            "preço",
            "valor",
            "tem ",
            "dispon",
            "estoque",
            "comprar",
            "interesse",
        )
    ):
        key = "compra"
    else:
        key = "outros"
    return key, RECOVERY_CATEGORY_LABELS[key]


def build_recovery_draft(reply: str) -> str:
    response = str(reply or "").strip()
    if not response:
        response = "Você ainda precisa de ajuda com esse atendimento?"
    return (
        "Olá! Desculpe a demora, fiquei alguns dias fora em viagem. "
        "Retomando seu atendimento:\n\n"
        f"{response}"
    )


def recovery_source_message(messages: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, str]]] | None:
    """Choose the latest customer text and the earlier messages as context."""
    for index in range(len(messages) - 1, -1, -1):
        item = messages[index]
        if item.get("direction") != "inbound" or not str(item.get("text") or "").strip():
            continue
        history = [
            {
                "role": "user" if previous.get("direction") == "inbound" else "assistant",
                "content": str(previous.get("text") or ""),
            }
            for previous in messages[:index]
            if str(previous.get("text") or "").strip()
        ]
        return item, history
    return None
