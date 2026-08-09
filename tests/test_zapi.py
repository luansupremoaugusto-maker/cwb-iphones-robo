from __future__ import annotations

from app.adapters.zapi import ZapiClient, normalize_received_callback
from app.config import Settings
from app.processor import MessageProcessor


def test_zapi_callback_normalizes_text_image_and_audio():
    text = normalize_received_callback(
        {"messageId": "m1", "phone": "+55 (11) 99999-9999", "text": {"message": "Olá"}}
    )
    image = normalize_received_callback(
        {"messageId": "m2", "phone": "5511999999999", "image": {"imageUrl": "https://img.test/a.jpg", "caption": "iPhone"}}
    )
    audio = normalize_received_callback(
        {"messageId": "m3", "phone": "5511999999999", "audio": {"audioUrl": "https://audio.test/a.ogg"}}
    )

    assert text.kind == "text" and text.phone == "5511999999999" and text.text == "Olá"
    assert image.kind == "image" and image.media_url == "https://img.test/a.jpg"
    assert audio.kind == "audio" and audio.media_url == "https://audio.test/a.ogg"


def test_admin_command_parser_accepts_supported_commands():
    incoming = normalize_received_callback(
        {"messageId": "admin-1", "phone": "5511999999999", "text": {"message": "#assumir 5511888888888"}}
    )
    assert MessageProcessor._parse_admin_command(incoming) == ("assumir", "5511888888888")


async def test_zapi_test_only_suppresses_unapproved_recipient():
    settings = Settings(outbound_mode="test_only", test_phones="5511999999999")
    client = ZapiClient(settings)
    try:
        result = await client.send_text("5511888888888", "teste")
    finally:
        await client.aclose()
    assert result.suppressed is True
    assert result.sent is False
