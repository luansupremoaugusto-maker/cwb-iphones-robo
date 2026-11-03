from __future__ import annotations

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
