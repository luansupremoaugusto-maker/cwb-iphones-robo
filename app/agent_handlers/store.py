"""Store response handlers for AgentService."""

from __future__ import annotations

import re

from app.agent import (
    AgentDecision,
    DELIVERY_FEE_HANDOFF_REASON,
    DELIVERY_FEE_HANDOFF_REPLY,
    _appointment_context,
    _delivery_or_pickup_reply,
    _delivery_payment_reply,
    _has_delivery_context,
    _has_product_reference,
    _has_sealed_reference,
    _has_seminovo_reference,
    _has_today_visit_offer,
    _has_unanswered_reservation_request,
    _has_visit_date_reference,
    _has_visit_time_reference,
    _installment_context_query,
    _is_appointment_followup,
    _is_business_weekday,
    _is_current_date_request,
    _is_delivery_fee_request,
    _is_delivery_followup_request,
    _is_delivery_or_pickup_request,
    _is_delivery_payment_question,
    _is_delivery_quote_followup,
    _is_explicit_human_request,
    _is_physical_store_request,
    _is_pickup_schedule_request,
    _is_reservation_request,
    _is_sealed_item,
    _is_store_hours_request,
    _is_today_store_status_request,
    _is_visit_request,
    _is_visit_unavailability_message,
    _is_warranty_request,
    _normalize,
    _pickup_schedule_reply,
    _reservation_policy,
    _reservation_reply,
    _store_address,
    _store_hours_reply,
    _today_label,
    _today_store_reply,
    _tomorrow_store_reply,
    _visit_date_for_request,
    _visit_day_reply,
    _visit_time_for_request,
)
from app.trade_in import (
    is_non_apple_trade_in_request,
    is_parts_buyback_request,
    is_trade_in_request,
    trade_in_em_andamento,
)


_STORE_CONDITION_POLICY_QUESTION_RE = re.compile(
    r"\b(?:so|somente|apenas)\b.{0,25}"
    r"\b(?:trabalh\w*|vend\w*|comercializ\w*)\b.{0,35}"
    r"\b(?:semi[\s-]*nov\w*|usad\w*|lacrad\w*|nov\w*)\b"
    r"|\b(?:trabalh\w*|vend\w*|comercializ\w*)\b.{0,25}"
    r"\b(?:so|somente|apenas)\b.{0,35}"
    r"\b(?:semi[\s-]*nov\w*|usad\w*|lacrad\w*|nov\w*)\b",
    re.IGNORECASE,
)


def _try_store_condition_policy_question(self, text: str) -> AgentDecision | None:
    if (
        is_non_apple_trade_in_request(text)
        or is_parts_buyback_request(text)
        or is_trade_in_request(text)
        or not _STORE_CONDITION_POLICY_QUESTION_RE.search(_normalize(text))
    ):
        return None
    return AgentDecision(
        reply=self.faq.get("condicao")
        or "Trabalhamos com produtos seminovos e produtos novos, lacrados.",
        confidence="high",
    )


def _append_delivery_or_pickup_info(self, reply: str, text: str) -> str:
    if not _is_delivery_or_pickup_request(text):
        return reply
    information = _delivery_or_pickup_reply(self.faq, text)
    if not information:
        return reply
    return f"{reply}\n\n{information}"

def _try_delivery_payment(
    self,
    text: str,
    history: list[dict[str, str]] | None = None,
) -> AgentDecision | None:
    if _is_delivery_quote_followup(text, history):
        return AgentDecision(
            reply=(
                "Obrigado! Vou encaminhar seu endereço para um atendente confirmar "
                "a taxa e o prazo da entrega e orientar como concluir o pagamento antes do envio."
            ),
            handoff=True,
            handoff_reason=DELIVERY_FEE_HANDOFF_REASON,
            confidence="high",
        )

    delivery_fee_request = _is_delivery_fee_request(text, history)
    acknowledge_cannot_visit = (
        _is_visit_unavailability_message(text) and _has_delivery_context(history)
    )
    if not acknowledge_cannot_visit and not _is_delivery_payment_question(text):
        return None
    return AgentDecision(
        reply=_delivery_payment_reply(
            self.faq,
            acknowledge_cannot_visit=acknowledge_cannot_visit,
        ),
        handoff=delivery_fee_request,
        handoff_reason=DELIVERY_FEE_HANDOFF_REASON if delivery_fee_request else None,
        confidence="high",
    )

def _try_delivery_or_pickup(
    self,
    text: str,
    history: list[dict[str, str]] | None = None,
) -> AgentDecision | None:
    delivery_fee_request = _is_delivery_fee_request(text, history)
    delivery_followup = _is_delivery_followup_request(text, history)
    pickup_schedule_request = _is_pickup_schedule_request(text)
    if (
        not (
            _is_delivery_or_pickup_request(text, history)
            or delivery_followup
            or delivery_fee_request
            or pickup_schedule_request
        )
        or _is_explicit_human_request(text)
        or _is_physical_store_request(text)
    ):
        return None
    if delivery_fee_request:
        return AgentDecision(
            reply=DELIVERY_FEE_HANDOFF_REPLY,
            handoff=True,
            handoff_reason=DELIVERY_FEE_HANDOFF_REASON,
            confidence="high",
        )
    if pickup_schedule_request:
        return AgentDecision(
            reply=_pickup_schedule_reply(self.faq, text),
            confidence="high",
        )
    reply = _delivery_or_pickup_reply(self.faq, text, force_delivery=delivery_followup)
    if not reply:
        return None
    return AgentDecision(reply=reply, confidence="high")

def _try_current_day_information(self, text: str) -> AgentDecision | None:
    if _is_current_date_request(text):
        return AgentDecision(reply=f"Hoje é {_today_label()}.", confidence="high")
    if _is_today_store_status_request(text):
        return AgentDecision(
            reply=_today_store_reply(self.faq, include_physical_store=False),
            confidence="high",
        )
    return None

def _try_store_hours(self, text: str) -> AgentDecision | None:
    if not _is_store_hours_request(text):
        return None
    reply = _store_hours_reply(self.faq)
    if _is_physical_store_request(text):
        reply = f"{reply} Endereço: {_store_address(self.faq)}."
    return AgentDecision(
        reply=reply,
        confidence="high",
    )

def _try_visit_scheduling(
    self,
    text: str,
    history: list[dict[str, str]] | None,
) -> AgentDecision | None:
    is_visit = _is_visit_request(text)
    is_followup = _is_appointment_followup(text, history)
    pending_reservation = is_followup and _has_unanswered_reservation_request(history)
    is_reservation = _is_reservation_request(text) or pending_reservation
    if not (is_visit or is_reservation or is_followup):
        return None

    if is_reservation and not is_visit and not pending_reservation:
        visit_day = _visit_date_for_request(text)
        reply = _reservation_reply(self.faq, visit_day=visit_day)
        return AgentDecision(reply=reply, confidence="high")

    context = _appointment_context(text, history)
    if (
        is_followup
        and _has_visit_time_reference(context)
        and not _has_visit_date_reference(context)
        and _has_today_visit_offer(history)
    ):
        context = f"hoje\n{context}"
    visit_day = _visit_date_for_request(text) or _visit_date_for_request(context)
    if visit_day is not None and not _is_business_weekday(visit_day):
        reply = (
            _reservation_reply(self.faq, visit_day=visit_day)
            if is_reservation
            else _visit_day_reply(visit_day)
        )
        return AgentDecision(reply=reply, confidence="high")
    if _has_visit_date_reference(context) and _has_visit_time_reference(context):
        address = self.faq.get("address") or (
            "Avenida Nossa Senhora da Luz, 1341 - Jardim Social, Curitiba - PR, 82520-060"
        )
        visit_time = _visit_time_for_request(text)
        if visit_day is not None and visit_time is not None:
            reply = (
                "Perfeito 😊 Vou registrar a solicitação da sua visita para "
                f"{_today_label(visit_day)}, às {visit_time}, e encaminhar para um "
                "atendente confirmar. O atendimento é feito com horário marcado. "
                f"Endereço: {address}"
            )
        else:
            reply = (
                "Perfeito 😊 Vou registrar a solicitação da sua visita para o dia e horário "
                "informados e encaminhar para um atendente confirmar. O atendimento é feito "
                "com horário marcado. "
                f"Endereço: {address}"
            )
        if is_reservation:
            reply = (
                f"{_reservation_policy(self.faq)} Não consigo garantir que o aparelho "
                f"continue disponível até a visita. {reply}"
            )
        return AgentDecision(
            reply=reply,
            handoff=True,
            handoff_reason="Agendamento de visita solicitado; atendente deve confirmar o horário",
            confidence="high",
        )

    if is_reservation:
        visit_day = _visit_date_for_request(text)
        return AgentDecision(
            reply=_reservation_reply(self.faq, visit_day=visit_day),
            confidence="high",
        )

    if "amanha" in _normalize(text):
        reply = _tomorrow_store_reply(self.faq)
        return AgentDecision(reply=reply, confidence="high")

    visit_day = _visit_date_for_request(text)
    if visit_day is not None:
        return AgentDecision(reply=_visit_day_reply(visit_day), confidence="high")

    reply = _today_store_reply(self.faq)
    return AgentDecision(reply=reply, confidence="high")

def _try_physical_store(self, text: str) -> AgentDecision | None:
    if not _is_physical_store_request(text):
        return None
    reply = _today_store_reply(self.faq)
    return AgentDecision(reply=reply, confidence="high")


_TRADE_IN_WARRANTY_STATUS_RE = re.compile(
    r"\b(?:"
    r"(?:ja\s+)?(?:acabou|terminou|venceu|expirou)\b.{0,40}\bgarantia\b"
    r"|\bgarantia\b.{0,40}\b(?:ja\s+)?(?:acabou|terminou|venceu|expirou)\b"
    r"|sem\s+(?:a\s+)?garantia\b"
    r"|(?:ja\s+)?nao\s+(?:tem|tenho|possui|possuo)\s+(?:mais\s+)?(?:a\s+)?garantia\b"
    r"|nao\s+(?:esta|ta)\s+(?:mais\s+)?(?:na\s+)?garantia\b"
    r")"
)
_STORE_WARRANTY_REFERENCE_RE = re.compile(
    r"\b(?:voces|loja|seminov\w*|semis|lacrad\w*)\b"
)
_CUSTOMER_DEVICE_OWNERSHIP_RE = re.compile(
    r"\b(?:meu|minha|meus|minhas|peguei|comprei|adquiri|ganhei)\b"
)


def _is_trade_in_warranty_status(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    if not trade_in_em_andamento(history) or "?" in text:
        return False
    normalized = _normalize(text)
    if (
        _STORE_WARRANTY_REFERENCE_RE.search(normalized)
        and not _CUSTOMER_DEVICE_OWNERSHIP_RE.search(normalized)
    ):
        return False
    return bool(_TRADE_IN_WARRANTY_STATUS_RE.search(normalized))


async def _try_warranty(
    self,
    text: str,
    history: list[dict[str, str]] | None,
) -> AgentDecision | None:
    if not _is_warranty_request(text):
        return None
    if _is_trade_in_warranty_status(text, history):
        return None

    normalized = _normalize(text)
    has_sealed = _has_sealed_reference(normalized)
    has_seminovo = _has_seminovo_reference(normalized)
    kind: str | None = None
    if has_sealed and not has_seminovo:
        kind = "sealed"
    elif has_seminovo and not has_sealed:
        kind = "seminovo"
    else:
        previous_user_messages = [
            item.get("content", "").strip()
            for item in (history or [])
            if item.get("role") == "user" and item.get("content", "").strip()
        ]
        has_product_context = _has_product_reference(normalized) or any(
            _has_product_reference(_normalize(message))
            for message in previous_user_messages[-3:]
        )
        if has_product_context:
            try:
                items = await self.cache.search(
                    _installment_context_query(text, history),
                    limit=3,
                )
            except Exception:
                items = []
            if items:
                kind = "sealed" if _is_sealed_item(items[0]) else "seminovo"

    include_accessories = "acompanha" in normalized
    if kind == "sealed":
        reply = self.faq.get("garantia_lacrados") or (
            "Produtos novos lacrados têm garantia de 1 ano pela Apple."
        )
        if include_accessories:
            reply = f"{reply} {self.faq.get('lacrados')}".strip()
    elif kind == "seminovo":
        reply = self.faq.get("garantia_seminovos") or (
            "Produtos seminovos têm garantia de 90 dias."
        )
        if include_accessories:
            reply = f"{reply} {self.faq.get('seminovos')}".strip()
    else:
        reply = self.faq.get("garantia") or (
            "Produtos seminovos têm garantia de 90 dias. "
            "Produtos novos lacrados têm garantia de 1 ano pela Apple."
        )
        if include_accessories:
            reply = (
                f"{reply} {self.faq.get('seminovos')} "
                f"{self.faq.get('lacrados')}"
            ).strip()
    return AgentDecision(reply=reply, confidence="high")
