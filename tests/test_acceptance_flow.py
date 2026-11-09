from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.adapters.openai_media import OpenAIMediaService
from app.config import Settings
from app.runtime import build_runtime


@pytest.mark.asyncio
async def test_handoff_pause_and_authorized_resume_commands():
    settings = Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        admin_phones="5511999999999",
        outbound_mode="disabled",
    )
    runtime = build_runtime(settings, offline=True)
    customer = "5511888888888"
    admin = "5511999999999"
    try:
        await runtime.processor.process_payload(
            {"messageId": "c1", "phone": customer, "text": {"message": "Quero falar com um atendente"}}
        )
        assert runtime.repository.get_conversation(customer).status == "human_pending"

        await runtime.processor.process_payload(
            {"messageId": "a1", "phone": admin, "text": {"message": f"#assumir {customer}"}}
        )
        assert runtime.repository.get_conversation(customer).status == "human_active"

        await runtime.processor.process_payload(
            {"messageId": "a2", "phone": admin, "text": {"message": f"#retomar {customer}"}}
        )
        assert runtime.repository.get_conversation(customer).status == "bot_active"

        await runtime.processor.process_payload(
            {"messageId": "a3", "phone": admin, "text": {"message": f"#fechar {customer}"}}
        )
        assert runtime.repository.get_conversation(customer).status == "closed"
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_audio_and_image_use_openai_only_for_transcription_or_description():
    transcription = AsyncMock(return_value=SimpleNamespace(text="iPhone treze azul"))
    vision = AsyncMock(
        return_value=SimpleNamespace(
            output_text="Possível iPhone azul; modelo incerto.",
            usage=SimpleNamespace(
                input_tokens=40,
                output_tokens=6,
                total_tokens=46,
                input_tokens_details=SimpleNamespace(cached_tokens=4),
                output_tokens_details=SimpleNamespace(reasoning_tokens=2),
            ),
        )
    )
    client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=transcription)),
        responses=SimpleNamespace(create=vision),
    )
    settings = Settings(openai_api_key=None)
    usage_events = []
    media = OpenAIMediaService(settings, client=client, usage_recorder=lambda **event: usage_events.append(event))

    transcript = await media.transcribe(b"audio", "audio/ogg")
    description = await media.describe_image(b"image", "image/jpeg", "celular")

    assert transcript == "iPhone treze azul"
    assert "modelo incerto" in description
    assert transcription.await_args.kwargs["model"] == "gpt-transcribe"
    vision_args = vision.await_args.kwargs
    assert vision_args["model"] == "gpt-6-luna"
    assert vision_args["input"][0]["content"][1]["type"] == "input_image"
    assert [event["stage"] for event in usage_events] == ["audio_transcription", "image_analysis"]
    assert usage_events[0]["requests"] == 1
    assert usage_events[0]["total_tokens"] is None
    assert usage_events[1]["total_tokens"] == 46
    assert usage_events[1]["cached_input_tokens"] == 4
    assert all("prompt" not in event and "phone" not in event for event in usage_events)
