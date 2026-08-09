from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings, normalize_phone
from app.schemas import IncomingMessage


class ZapiError(RuntimeError):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


def _payload_id(payload: dict[str, Any]) -> str:
    message_id = str(payload.get("messageId") or payload.get("zaapId") or "").strip()
    if message_id:
        return message_id
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_received_callback(payload: dict[str, Any]) -> IncomingMessage:
    phone = normalize_phone(str(payload.get("phone") or payload.get("participantPhone") or ""))
    message_id = str(payload.get("messageId") or payload.get("zaapId") or _payload_id(payload))
    event_id = _payload_id(payload)
    common = {
        "event_id": event_id,
        "message_id": message_id,
        "phone": phone,
        "chat_name": payload.get("chatName") or payload.get("senderName"),
        "from_me": bool(payload.get("fromMe", False)),
        "is_group": bool(payload.get("isGroup", False)),
        "is_newsletter": bool(payload.get("isNewsletter", False)),
        "is_status_reply": bool(payload.get("isStatusReply", False)),
        "raw": payload,
    }

    text_data = payload.get("text")
    if isinstance(text_data, dict) and text_data.get("message") is not None:
        return IncomingMessage(kind="text", text=str(text_data.get("message") or ""), **common)

    image_data = payload.get("image")
    if isinstance(image_data, dict):
        return IncomingMessage(
            kind="image",
            text=str(image_data.get("caption") or ""),
            caption=str(image_data.get("caption") or ""),
            media_url=image_data.get("imageUrl"),
            mime_type=image_data.get("mimeType"),
            **common,
        )

    audio_data = payload.get("audio")
    if isinstance(audio_data, dict):
        return IncomingMessage(
            kind="audio",
            media_url=audio_data.get("audioUrl"),
            mime_type=audio_data.get("mimeType"),
            **common,
        )

    button_data = payload.get("buttonsResponseMessage")
    if isinstance(button_data, dict):
        return IncomingMessage(kind="button", text=str(button_data.get("message") or ""), **common)

    list_data = payload.get("listResponseMessage")
    if isinstance(list_data, dict):
        return IncomingMessage(kind="list", text=str(list_data.get("message") or ""), **common)

    return IncomingMessage(kind="unsupported", **common)


@dataclass(frozen=True)
class SendResult:
    sent: bool
    suppressed: bool = False
    provider_message_id: str | None = None


class ZapiClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _can_send(self, phone: str) -> bool:
        if self.settings.outbound_mode == "disabled":
            return False
        if self.settings.outbound_mode == "test_only":
            return normalize_phone(phone) in self.settings.test_phone_set
        return True

    def _endpoint(self, path: str) -> str:
        if not self.settings.zapi_instance_id or not self.settings.zapi_token:
            raise ZapiError("Credenciais Z-API não configuradas")
        base = self.settings.zapi_base_url.rstrip("/")
        return f"{base}/instances/{self.settings.zapi_instance_id}/token/{self.settings.zapi_token}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.zapi_client_token:
            headers["Client-Token"] = self.settings.zapi_client_token
        return headers

    @staticmethod
    def _provider_message_id(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        return str(payload.get("messageId") or payload.get("id") or payload.get("zaapId") or "") or None

    async def send_text(self, phone: str, message: str, reply_to: str | None = None) -> SendResult:
        if not self._can_send(phone):
            return SendResult(sent=False, suppressed=True)
        body: dict[str, Any] = {"phone": normalize_phone(phone), "message": message}
        if reply_to:
            body["messageId"] = reply_to
        try:
            response = await self._client.post(self._endpoint("send-text"), headers=self._headers(), json=body)
        except httpx.HTTPError as exc:
            raise ZapiError(f"Falha de transporte Z-API: {exc}") from exc
        if response.status_code >= 400:
            raise ZapiError(f"Z-API HTTP {response.status_code}: {response.text[:500]}", response.status_code)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return SendResult(sent=True, provider_message_id=self._provider_message_id(payload))

    async def send_image(
        self,
        phone: str,
        image: str,
        caption: str = "",
        reply_to: str | None = None,
    ) -> SendResult:
        """Send one approved HTTPS image URL through Z-API's send-image endpoint."""
        if not self._can_send(phone):
            return SendResult(sent=False, suppressed=True)
        if not isinstance(image, str) or not re.match(r"^(?:https://|data:image/)", image, flags=re.IGNORECASE):
            raise ZapiError("Imagem precisa ser uma URL HTTPS ou um data URI de imagem")
        body: dict[str, Any] = {"phone": normalize_phone(phone), "image": image}
        if caption:
            body["caption"] = caption
        if reply_to:
            body["messageId"] = reply_to
        try:
            response = await self._client.post(self._endpoint("send-image"), headers=self._headers(), json=body)
        except httpx.HTTPError as exc:
            raise ZapiError(f"Falha de transporte Z-API: {exc}") from exc
        if response.status_code >= 400:
            raise ZapiError(f"Z-API HTTP {response.status_code}: {response.text[:500]}", response.status_code)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return SendResult(sent=True, provider_message_id=self._provider_message_id(payload))

    async def download_media(self, url: str | None) -> tuple[bytes, str]:
        if not url or not re.match(r"^https?://", url, flags=re.IGNORECASE):
            raise ZapiError("URL de mídia ausente ou inválida")
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise ZapiError(f"Falha ao baixar mídia da Z-API: {exc}") from exc
        if response.status_code >= 400:
            raise ZapiError(f"Download de mídia HTTP {response.status_code}", response.status_code)
        if len(response.content) > self.settings.media_max_bytes:
            raise ZapiError("Mídia excede o tamanho máximo configurado")
        content_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
        return response.content, content_type
