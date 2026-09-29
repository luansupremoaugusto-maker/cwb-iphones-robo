from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.runtime import build_runtime


def test_group_callback_records_safe_diagnostics_without_entering_customer_flow():
    settings = Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        zapi_webhook_secret="secret-test",
        zapi_expected_instance_id="instance-test",
        zapi_group_diagnostics_enabled=True,
        outbound_mode="disabled",
    )
    runtime = build_runtime(settings, offline=True)
    app = create_app(runtime)
    supplier_list = "LISTA SEMINOVOS ATACADO DRTEC\nIPHONE 13 128GB: R$ 1.750,00"
    payload = {
        "type": "ReceivedCallback",
        "instanceId": "instance-test",
        "messageId": "supplier-group-message-1",
        "phone": "120363019502650977-group",
        "participantPhone": "5544999999999",
        "fromMe": False,
        "isGroup": True,
        "chatName": "ATACADO DRTEC",
        "text": {"message": supplier_list},
    }

    with TestClient(app) as client:
        response = client.post("/webhooks/zapi/secret-test", json=payload)

    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["ignored"] is True
    assert runtime.repository.claim_next_job() is None

    events = runtime.repository.list_audit_events(event_type="zapi_group_webhook_diagnostic")
    assert len(events) == 1
    assert events[0]["subject"] is None
    assert events[0]["detail"] == {
        "type": "ReceivedCallback",
        "message_id": "supplier-group-message-1",
        "group_id": "120363019502650977-group",
        "chat_name": "ATACADO DRTEC",
        "message_kind": "text",
        "text_chars": len(supplier_list),
    }
    assert "R$ 1.750,00" not in str(events[0]["detail"])


def test_group_diagnostics_are_disabled_by_default():
    settings = Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        zapi_webhook_secret="secret-test",
        zapi_expected_instance_id="instance-test",
        outbound_mode="disabled",
    )
    runtime = build_runtime(settings, offline=True)
    app = create_app(runtime)
    payload = {
        "type": "ReceivedCallback",
        "instanceId": "instance-test",
        "messageId": "supplier-group-message-2",
        "phone": "120363019502650977-group",
        "fromMe": False,
        "isGroup": True,
        "chatName": "ATACADO DRTEC",
        "text": {"message": "lista de fornecedor"},
    }

    with TestClient(app) as client:
        response = client.post("/webhooks/zapi/secret-test", json=payload)

    assert response.status_code == 200
    assert response.json()["ignored"] is True
    assert runtime.repository.claim_next_job() is None
    assert runtime.repository.list_audit_events(event_type="zapi_group_webhook_diagnostic") == []
