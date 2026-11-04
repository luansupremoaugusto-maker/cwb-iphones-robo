from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.config import Settings
from app.schemas import AgentDecision
from app.main import create_app
from app.runtime import build_runtime


def _settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        zapi_webhook_secret="secret-test",
        zapi_expected_instance_id="instance-test",
        outbound_mode="disabled",
        message_batch_wait_seconds=0,
    )


def test_webhook_queues_manual_outgoing_and_ignores_bot_echoes():
    runtime = build_runtime(_settings(), offline=True)
    app = create_app(runtime)
    phone = "5511999999999"
    manual_payload = {
        "instanceId": "instance-test",
        "type": "ReceivedCallback",
        "messageId": "manual-1",
        "phone": phone,
        "fromMe": True,
        "fromApi": False,
        "text": {"message": "Oi, sou o Luan."},
    }
    api_echo = {
        **manual_payload,
        "messageId": "robot-1",
        "fromApi": True,
        "text": {"message": "Resposta do robô."},
    }

    with TestClient(app) as client:
        first = client.post("/webhooks/zapi/secret-test", json=manual_payload)
        duplicate = client.post("/webhooks/zapi/secret-test", json=manual_payload)
        echo = client.post("/webhooks/zapi/secret-test", json=api_echo)

    assert first.status_code == 200
    assert first.json()["direction"] == "outbound"
    assert duplicate.json()["duplicate"] is True
    assert echo.json()["ignored"] is True

    claimed = runtime.repository.claim_next_job()
    assert claimed is not None
    assert claimed[1]["messageId"] == "manual-1"


async def test_whatsapp_business_greeting_does_not_hold_initial_customer_message():
    runtime = build_runtime(_settings(), offline=True)
    phone = "5511888888887"
    calls: list[tuple[str, list[dict[str, str]]]] = []

    class CapturingAgent:
        async def respond(self, text, history=None, image_description=None):
            calls.append((text, list(history or [])))
            return AgentDecision(reply="Bom dia! Como posso ajudar?")

    runtime.processor.agent = CapturingAgent()
    try:
        await runtime.processor.process_batch(
            [
                {
                    "messageId": "customer-greeting-1",
                    "phone": phone,
                    "fromMe": False,
                    "text": {"message": "Bom dia"},
                },
                {
                    "messageId": "whatsapp-auto-greeting-1",
                    "phone": phone,
                    "fromMe": True,
                    "fromApi": False,
                    "text": {
                        "message": "Cwb.iphones agradece seu contato. Como podemos ajudar?"
                    },
                },
            ]
        )

        assert calls == [("Bom dia", [])]
        ignored_events = runtime.repository.list_audit_events(
            event_type="ignored_event",
            limit=10,
        )
        assert ignored_events[0]["subject"] == phone
        assert ignored_events[0]["detail"] == {
            "reason": "whatsapp_business_greeting",
            "message_id": "whatsapp-auto-greeting-1",
        }
    finally:
        await runtime.aclose()


async def test_manual_message_becomes_context_and_holds_pending_bot_reply():
    runtime = build_runtime(_settings(), offline=True)
    phone = "5511888888888"
    calls: list[tuple[str, list[dict[str, str]]]] = []

    class CapturingAgent:
        async def respond(self, text, history=None, image_description=None):
            calls.append((text, list(history or [])))
            return AgentDecision(reply="Pode deixar, vou verificar.")

    runtime.processor.agent = CapturingAgent()
    try:
        await runtime.processor.process_batch(
            [
                {
                    "messageId": "customer-1",
                    "phone": phone,
                    "fromMe": False,
                    "text": {"message": "Tem iPhone 15?"},
                },
                {
                    "messageId": "manual-2",
                    "phone": phone,
                    "fromMe": True,
                    "fromApi": False,
                    "text": {"message": "Oi, sou o Luan. Vou verificar para você."},
                },
            ]
        )

        assert calls == []
        detail = runtime.repository.conversation_detail(phone)
        assert detail is not None
        assert [item["direction"] for item in detail["messages"]] == ["inbound", "outbound"]
        assert detail["messages"][1]["origin"] == "manual_whatsapp"

        await runtime.processor.process_batch(
            [{"messageId": "customer-2", "phone": phone, "text": {"message": "E o preço?"}}]
        )

        assert len(calls) == 1
        assert calls[0][0] == "E o preço?"
        assert {"role": "assistant", "content": "Oi, sou o Luan. Vou verificar para você."} in calls[0][1]
    finally:
        await runtime.aclose()


async def test_manual_outgoing_audio_is_skipped_but_customer_audio_is_transcribed():
    runtime = build_runtime(_settings(), offline=True)
    phone = "5511777777777"
    transcribe = AsyncMock(return_value="Quero saber o preço do aparelho.")
    download_media = AsyncMock(return_value=(b"audio-bytes", "audio/ogg"))
    runtime.processor.media = SimpleNamespace(transcribe=transcribe)
    runtime.processor.zapi.download_media = download_media

    try:
        await runtime.processor.process_payload(
            {
                "messageId": "manual-audio-1",
                "phone": phone,
                "fromMe": True,
                "fromApi": False,
                "audio": {"audioUrl": "https://example.test/manual.ogg", "mimeType": "audio/ogg"},
            }
        )

        transcribe.assert_not_awaited()
        download_media.assert_not_awaited()

        await runtime.processor.process_payload(
            {
                "messageId": "customer-audio-1",
                "phone": phone,
                "fromMe": False,
                "audio": {"audioUrl": "https://example.test/customer.ogg", "mimeType": "audio/ogg"},
            }
        )

        transcribe.assert_awaited_once_with(b"audio-bytes", "audio/ogg")
        download_media.assert_awaited_once_with("https://example.test/customer.ogg")
        detail = runtime.repository.conversation_detail(phone)
        assert detail is not None
        assert detail["messages"][0]["text"] == (
            "[Áudio enviado manualmente pelo atendimento; conteúdo não transcrito.]"
        )
        assert detail["messages"][1]["text"] == "Quero saber o preço do aparelho."
    finally:
        await runtime.aclose()


async def test_manual_message_context_is_reused_when_zapi_switches_from_lid_to_phone():
    runtime = build_runtime(_settings(), offline=True)
    app = create_app(runtime)
    customer_phone = "5511000000001"
    peer_lid = "999999999999999@lid"
    calls: list[tuple[str, list[dict[str, str]]]] = []

    class CapturingAgent:
        async def respond(self, text, history=None, image_description=None):
            calls.append((text, list(history or [])))
            return AgentDecision(reply="Você mencionou a cor azul.")

    runtime.processor.agent = CapturingAgent()
    manual_payload = {
        "instanceId": "instance-test",
        "type": "ReceivedCallback",
        "messageId": "manual-lid-1",
        "phone": peer_lid.removesuffix("@lid"),
        "fromMe": True,
        "fromApi": False,
        "text": {"message": "Para teste, prefiro a cor azul."},
    }
    customer_payload = {
        "instanceId": "instance-test",
        "type": "ReceivedCallback",
        "messageId": "customer-lid-2",
        "phone": customer_phone,
        "senderLid": peer_lid,
        "chatLid": peer_lid,
        "fromMe": False,
        "text": {"message": "Qual cor você mencionou?"},
    }

    try:
        with TestClient(app) as client:
            manual = client.post("/webhooks/zapi/secret-test", json=manual_payload)
            assert manual.status_code == 200
            assert manual.json()["direction"] == "outbound"

            claimed = runtime.repository.claim_next_job(include_attempt=True)
            assert claimed is not None
            job_id, payload, attempt = claimed
            batch_payloads = payload.get("_batch_payloads")
            await runtime.processor.process_batch(
                batch_payloads if isinstance(batch_payloads, list) else [payload]
            )
            runtime.repository.finish_job(job_id, expected_attempts=attempt)

            customer = client.post("/webhooks/zapi/secret-test", json=customer_payload)
            assert customer.status_code == 200

            claimed = runtime.repository.claim_next_job(include_attempt=True)
            assert claimed is not None
            job_id, payload, attempt = claimed
            batch_payloads = payload.get("_batch_payloads")
            await runtime.processor.process_batch(
                batch_payloads if isinstance(batch_payloads, list) else [payload]
            )
            runtime.repository.finish_job(job_id, expected_attempts=attempt)

        assert len(calls) == 1
        assert calls[0][0] == "Qual cor você mencionou?"
        assert {
            "role": "assistant",
            "content": "Para teste, prefiro a cor azul.",
        } in calls[0][1]

        detail = runtime.repository.conversation_detail(customer_phone)
        assert detail is not None
        assert any(
            item["origin"] == "manual_whatsapp"
            and item["text"] == "Para teste, prefiro a cor azul."
            for item in detail["messages"]
        )
    finally:
        await runtime.aclose()


async def test_existing_lid_conversation_is_adopted_without_a_saved_alias():
    runtime = build_runtime(_settings(), offline=True)
    legacy_lid = "888888888888888"
    customer_phone = "5511000000002"
    try:
        runtime.repository.add_message(
            legacy_lid,
            direction="outbound",
            kind="text",
            text="Mensagem manual registrada antes da associação de LID.",
            raw={"origin": "manual_whatsapp"},
        )

        canonical_phone = runtime.repository.resolve_callback_phone(
            customer_phone,
            (("lid", legacy_lid),),
            from_me=False,
        )

        assert canonical_phone == customer_phone
        assert {
            "role": "assistant",
            "content": "Mensagem manual registrada antes da associação de LID.",
        } in runtime.repository.recent_messages(customer_phone)
        detail = runtime.repository.conversation_detail(customer_phone)
        assert detail is not None
        assert detail["messages"][0]["origin"] == "manual_whatsapp"
    finally:
        await runtime.aclose()
