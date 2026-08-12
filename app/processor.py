from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

from app.adapters.openai_media import OpenAIMediaError
from app.adapters.zapi import ZapiClient, ZapiError, normalize_received_callback
from app.agent import AgentService
from app.config import Settings, normalize_phone
from app.schemas import AgentDecision, IncomingMessage
from app.storage.database import Repository


ADMIN_COMMAND_RE = re.compile(r"^#(assumir|retomar|fechar)\s+(\d{10,15})\s*$", re.IGNORECASE)
ADMIN_BULK_COMMAND_RE = re.compile(r"^#(retomar_todos|liberar_todos)\s*$", re.IGNORECASE)


def _fold_text(value: str | None) -> str:
    plain = "".join(
        char for char in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", plain).strip().lower()


def _clean_summary_text(value: str | None, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", (value or "")).strip()
    text = re.sub(r"(?i)\bimei(?:\s*2)?\s*[:=-]?\s*[0-9A-Za-z-]{6,}", "IMEI [oculto]", text)
    text = re.sub(
        r"(?i)\b(?:sn|serial(?:number)?)\s*[:=-]?\s*[0-9A-Za-z-]{4,}",
        "serial [oculto]",
        text,
    )
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _image_history_text(caption: str | None, description: str | None) -> str:
    """Persist only safe, text-based context derived from an inbound image."""
    parts: list[str] = []
    safe_caption = _clean_summary_text(caption, limit=500)
    safe_description = _clean_summary_text(description, limit=1200)
    if safe_caption:
        parts.append(f"Legenda da imagem: {safe_caption}")
    if safe_description:
        parts.append(f"Descricao visual da imagem recebida: {safe_description}")
    return "\n".join(parts)


def _remove_sent_image_urls(text: str, image_urls: list[str] | None) -> str:
    """Avoid repeating image attachments as clickable links in the text."""
    cleaned = text or ""
    for image_url in dict.fromkeys(image_urls or []):
        if isinstance(image_url, str) and image_url:
            cleaned = cleaned.replace(image_url, "")
    cleaned = "\n".join(line.strip() for line in cleaned.splitlines() if line.strip())
    return cleaned or "Seguem as fotos."


def _handoff_title(reason: str, context: str) -> str:
    combined = _fold_text(f"{reason} {context}")
    if any(marker in combined for marker in ("trade", "usado", "parte do pagamento", "avaliacao")):
        return "Cliente avaliando/negociando o usado"
    if "agend" in combined:
        return "Agendamento solicitado"
    if any(marker in combined for marker in ("reclamacao", "reclama", "problema", "defeito")):
        return "Cliente com problema ou reclamação"
    if any(marker in combined for marker in ("midia", "audio", "imagem")):
        return "Falha ao processar mídia"
    if "falha" in combined or "erro" in combined:
        return "Falha no atendimento automático"
    if any(marker in combined for marker in ("atendente", "humano", "pessoa")):
        return "Atendimento humano solicitado"
    return "Novo atendimento humano"


def _customer_observations(
    history: list[dict[str, str]] | None,
    current_context: str,
    limit: int = 4,
) -> list[str]:
    messages: list[str] = []
    for entry in history or []:
        if entry.get("role") != "user":
            continue
        cleaned = _clean_summary_text(entry.get("content"))
        if cleaned and cleaned not in messages:
            messages.append(cleaned)
    current = _clean_summary_text(current_context)
    if current and current not in messages:
        messages.append(current)
    return messages[-limit:]


def _build_handoff_message(
    customer_phone: str,
    reason: str,
    context: str,
    *,
    chat_name: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> str:
    title = _handoff_title(reason, context)
    safe_name = _clean_summary_text(chat_name, limit=80)
    customer_label = f"{customer_phone} ({safe_name})" if safe_name else customer_phone
    observations = _customer_observations(history, context)
    observation_text = " | ".join(observations) if observations else "Contexto não disponível."

    return "\n".join(
        [
            f"🔔 {title}",
            f"Cliente: {customer_label}",
            "",
            "📋 Resumo do atendimento",
            f"• Motivo: {_clean_summary_text(reason, limit=240)}",
            f"• Obs: {observation_text}",
            "",
            f"Assuma a conversa. Use #assumir {customer_phone} para assumir.",
            f"Quando terminar, use #retomar {customer_phone} para liberar o robô.",
            f"Use #fechar {customer_phone} para encerrar a conversa.",
        ]
    )


class MessageProcessor:
    def __init__(
        self,
        settings: Settings,
        repository: Repository,
        zapi: ZapiClient,
        agent: AgentService,
        media: Any | None = None,
        photo_normalizer: Any | None = None,
    ):
        self.settings = settings
        self.repository = repository
        self.zapi = zapi
        self.agent = agent
        self.media = media
        self.photo_normalizer = photo_normalizer

    async def process_payload(self, payload: dict[str, Any]) -> None:
        await self.process_batch([payload])

    async def process_batch(self, payloads: list[dict[str, Any]]) -> None:
        """Process all messages in one durable debounce window as one turn."""
        grouped: dict[str, list[IncomingMessage]] = defaultdict(list)
        for payload in payloads:
            incoming = normalize_received_callback(payload)
            if not incoming.phone:
                self.repository.audit("ignored_event", None, {"reason": "missing_phone"})
                continue
            if incoming.from_me or incoming.is_group or incoming.is_newsletter or incoming.is_status_reply:
                self.repository.audit("ignored_event", incoming.phone, {"reason": "provider_control_event"})
                continue

            command = self._parse_admin_command(incoming)
            if command:
                await self._handle_admin_command(incoming.phone, command)
                continue
            grouped[incoming.phone].append(incoming)

        for phone, incoming_messages in grouped.items():
            await self._process_phone_batch(phone, incoming_messages)

    async def _process_phone_batch(self, phone: str, incoming_messages: list[IncomingMessage]) -> None:
        first = incoming_messages[0]
        last = incoming_messages[-1]
        self.repository.get_or_create_conversation(phone, first.chat_name)
        previous_history = self.repository.recent_messages(phone, limit=12)

        stored_message_ids: list[int] = []
        for incoming in incoming_messages:
            stored_message_ids.append(
                self.repository.add_message(
                    phone,
                    direction="inbound",
                    kind=incoming.kind,
                    text=incoming.text or incoming.caption,
                    provider_message_id=incoming.message_id,
                    raw=self._safe_raw(incoming),
                )
            )

        conversation = self.repository.get_conversation(phone)
        if conversation and conversation.status in {"human_pending", "human_active", "closed"}:
            self.repository.audit(
                "message_held",
                phone,
                {"status": conversation.status, "batch_size": len(incoming_messages)},
            )
            return

        text_parts: list[str] = []
        image_descriptions: list[str] = []
        try:
            for index, incoming in enumerate(incoming_messages):
                working_text, image_description = await self._prepare_input(incoming)
                if working_text:
                    text_parts.append(working_text)
                if image_description:
                    image_descriptions.append(image_description)
                    self.repository.update_message_text(
                        stored_message_ids[index],
                        _image_history_text(incoming.caption, image_description),
                    )
                elif incoming.kind == "audio" and working_text:
                    # Keep audio transcripts available to a later follow-up,
                    # just like image descriptions.
                    self.repository.update_message_text(
                        stored_message_ids[index],
                        working_text,
                    )
        except (OpenAIMediaError, ZapiError) as exc:
            await self._send_customer(
                last,
                "Não consegui processar essa mídia agora. Vou encaminhar sua mensagem para um atendente.",
            )
            reason = str(exc)[:255]
            if self.repository.claim_human_handoff(phone, reason):
                await self._notify_admins(
                    phone,
                    "Falha ao processar mídia",
                    "\n".join(text_parts)[:900],
                    chat_name=first.chat_name,
                    history=previous_history,
                )
            else:
                self.repository.audit(
                    "handoff_notification_suppressed",
                    phone,
                    {"reason": "conversation_already_human", "handoff_reason": reason},
                )
            return

        combined_text = "\n".join(text_parts).strip()
        combined_image_description = "\n".join(image_descriptions).strip() or None
        try:
            decision = await self.agent.respond(
                combined_text,
                history=previous_history,
                image_description=combined_image_description,
            )
        except Exception as exc:
            decision = AgentDecision(
                reply="Vou encaminhar sua mensagem para um atendente confirmar essa informação.",
                handoff=True,
                handoff_reason=f"Falha inesperada: {type(exc).__name__}",
                confidence="low",
            )

        await self._send_customer(last, decision.reply, image_urls=decision.image_urls)
        self.repository.audit(
            "agent_response",
            phone,
            {
                "handoff": decision.handoff,
                "confidence": decision.confidence,
                "reason": decision.handoff_reason,
                "batch_size": len(incoming_messages),
                "image_count": len(decision.image_urls),
            },
        )
        if decision.handoff:
            reason = decision.handoff_reason or "Atendimento humano solicitado"
            if self.repository.claim_human_handoff(phone, reason):
                await self._notify_admins(
                    phone,
                    reason,
                    combined_text[:900],
                    chat_name=first.chat_name,
                    history=previous_history,
                )
            else:
                self.repository.audit(
                    "handoff_notification_suppressed",
                    phone,
                    {"reason": "conversation_already_human", "handoff_reason": reason},
                )

    @staticmethod
    def _safe_raw(incoming: IncomingMessage) -> dict[str, Any]:
        raw = dict(incoming.raw)
        for field in ("senderPhoto", "photo"):
            raw.pop(field, None)
        return {"type": raw.get("type"), "messageId": raw.get("messageId"), "kind": incoming.kind}

    @staticmethod
    def _parse_admin_command(incoming: IncomingMessage) -> tuple[str, str | None] | None:
        text = (incoming.text or "").strip()
        match = ADMIN_COMMAND_RE.match(text)
        if match:
            return match.group(1).lower(), normalize_phone(match.group(2))
        bulk_match = ADMIN_BULK_COMMAND_RE.match(text)
        if bulk_match:
            return bulk_match.group(1).lower(), None
        return None

    async def _handle_admin_command(self, sender_phone: str, command: tuple[str, str | None]) -> None:
        action, target = command
        if sender_phone not in self.settings.admin_phone_set:
            self.repository.audit("unauthorized_admin_command", sender_phone, {"action": action})
            return

        if action in {"retomar_todos", "liberar_todos"}:
            released_count = self.repository.release_all_human_conversations(
                f"Comando {action} por atendente autorizado"
            )
            self.repository.audit(
                "admin_command",
                None,
                {
                    "action": action,
                    "operator": sender_phone,
                    "released_count": released_count,
                },
            )
            await self._send_phone(
                sender_phone,
                f"{released_count} conversa(s) em atendimento humano foram liberadas para o robô.",
                kind="admin",
            )
            return

        if target is None:
            return
        status = {"assumir": "human_active", "retomar": "bot_active", "fechar": "closed"}[action]
        self.repository.set_conversation_status(target, status, f"Comando {action} por atendente autorizado")
        self.repository.audit("admin_command", target, {"action": action, "operator": sender_phone})
        await self._send_phone(
            sender_phone,
            f"Conversa {target}: status alterado para {status}.",
            kind="admin",
        )

    async def _prepare_input(self, incoming: IncomingMessage) -> tuple[str, str | None]:
        if incoming.kind in {"text", "button", "list"}:
            return (incoming.text or "").strip(), None
        if incoming.kind == "audio":
            if self.media is None:
                raise OpenAIMediaError("Serviço de transcrição não configurado")
            content, mime_type = await self.zapi.download_media(incoming.media_url)
            return await self.media.transcribe(content, mime_type), None
        if incoming.kind == "image":
            if self.media is None:
                raise OpenAIMediaError("Serviço de visão não configurado")
            content, mime_type = await self.zapi.download_media(incoming.media_url)
            description = await self.media.describe_image(content, mime_type, incoming.caption)
            return (incoming.caption or "").strip(), description
        return "", None

    async def _send_customer(
        self,
        incoming: IncomingMessage,
        text: str,
        image_urls: list[str] | None = None,
    ) -> None:
        text = _remove_sent_image_urls(text, image_urls)
        await self._send_phone(incoming.phone, text, reply_to=incoming.message_id)
        for index, image_url in enumerate(image_urls or []):
            normalized_url = image_url
            if self.photo_normalizer is not None:
                normalized_url = await self.photo_normalizer.normalize(image_url)
            await self._send_image(
                incoming.phone,
                normalized_url,
                reply_to=incoming.message_id if index == 0 else None,
            )

    async def _send_image(self, phone: str, image_url: str, reply_to: str | None = None) -> None:
        provider_id: str | None = None
        try:
            result = await self.zapi.send_image(phone, image_url, reply_to=reply_to)
            provider_id = result.provider_message_id
            if result.suppressed:
                self.repository.audit(
                    "outbound_suppressed",
                    phone,
                    {"mode": self.settings.outbound_mode, "kind": "image"},
                )
        except ZapiError as exc:
            self.repository.audit(
                "outbound_error",
                phone,
                {"error": str(exc)[:500], "status": exc.status_code, "kind": "image"},
            )
        self.repository.add_message(
            normalize_phone(phone),
            direction="outbound",
            kind="image",
            text="",
            provider_message_id=provider_id,
            raw={"suppressed": self.settings.outbound_mode != "live", "kind": "image"},
        )

    async def _send_phone(
        self,
        phone: str,
        text: str,
        reply_to: str | None = None,
        kind: str = "text",
    ) -> None:
        provider_id: str | None = None
        try:
            result = await self.zapi.send_text(phone, text, reply_to=reply_to)
            provider_id = result.provider_message_id
            if result.suppressed:
                self.repository.audit("outbound_suppressed", phone, {"mode": self.settings.outbound_mode})
        except ZapiError as exc:
            self.repository.audit("outbound_error", phone, {"error": str(exc)[:500], "status": exc.status_code})
        self.repository.add_message(
            normalize_phone(phone),
            direction="outbound",
            kind=kind,
            text=text,
            provider_message_id=provider_id,
            raw={"suppressed": self.settings.outbound_mode != "live"},
        )

    async def _notify_admins(
        self,
        customer_phone: str,
        reason: str,
        context: str,
        *,
        chat_name: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> None:
        admins = self.settings.admin_phone_set
        if not admins:
            self.repository.audit("handoff_without_admin", customer_phone, {"reason": reason})
            return
        message = _build_handoff_message(
            customer_phone,
            reason,
            context,
            chat_name=chat_name,
            history=history,
        )
        for admin in admins:
            if admin != customer_phone:
                await self._send_phone(admin, message, kind="handoff")
