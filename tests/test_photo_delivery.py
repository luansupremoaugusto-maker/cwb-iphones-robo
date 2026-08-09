from __future__ import annotations

import pytest

from app.adapters.mercado_phone_files import MAX_PRODUCT_PHOTOS, extract_file_urls
from app.adapters.zapi import SendResult
from app.config import Settings
from app.processor import MessageProcessor
from app.schemas import IncomingMessage


def test_extract_file_urls_keeps_seven_product_photos():
    urls = [f"https://cdn.example/iphone-16e-black-{index}.jpg" for index in range(7)]
    payload = {"data": {"files": [{"arquivoUrl": url} for url in urls]}}

    assert MAX_PRODUCT_PHOTOS >= 7
    assert extract_file_urls(payload) == urls


class _RepositoryStub:
    def __init__(self):
        self.messages: list[dict] = []
        self.audits: list[dict] = []

    def add_message(self, phone, **kwargs):
        self.messages.append({"phone": phone, **kwargs})

    def audit(self, event, phone, details):
        self.audits.append({"event": event, "phone": phone, "details": details})


class _ZapiStub:
    def __init__(self):
        self.text_calls: list[dict] = []
        self.image_calls: list[dict] = []

    async def send_text(self, phone, message, reply_to=None):
        self.text_calls.append({"phone": phone, "message": message, "reply_to": reply_to})
        return SendResult(sent=True, provider_message_id="text-1")

    async def send_image(self, phone, image, caption="", reply_to=None):
        self.image_calls.append(
            {"phone": phone, "image": image, "caption": caption, "reply_to": reply_to}
        )
        return SendResult(sent=True, provider_message_id=f"image-{len(self.image_calls)}")


@pytest.mark.asyncio
async def test_processor_sends_all_returned_product_photos():
    settings = Settings(outbound_mode="live")
    repository = _RepositoryStub()
    zapi = _ZapiStub()
    processor = MessageProcessor(settings, repository, zapi, agent=None)
    incoming = IncomingMessage(
        event_id="event-1",
        message_id="message-1",
        phone="5541999999999",
        kind="text",
        text="fotos do 16e preto",
    )
    urls = [f"https://cdn.example/iphone-16e-black-{index}.jpg" for index in range(7)]

    await processor._send_customer(incoming, "Seguem as fotos.", image_urls=urls)

    assert len(zapi.text_calls) == 1
    assert len(zapi.image_calls) == 7
    assert [call["image"] for call in zapi.image_calls] == urls
    assert zapi.image_calls[0]["reply_to"] == "message-1"
    assert all(call["reply_to"] is None for call in zapi.image_calls[1:])
    assert len(repository.messages) == 8
