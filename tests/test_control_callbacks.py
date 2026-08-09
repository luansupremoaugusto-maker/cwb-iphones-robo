from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.runtime import build_runtime


def test_zapi_status_callback_is_audited_without_becoming_customer_job():
    settings = Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        zapi_webhook_secret="secret-test",
        outbound_mode="disabled",
    )
    runtime = build_runtime(settings, offline=True)
    app = create_app(runtime)

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/zapi/secret-test",
            json={"type": "DeliveryCallback", "messageId": "delivery-1", "phone": "5511999999999"},
        )

    assert response.status_code == 200
    assert response.json()["ignored"] is True
    assert runtime.repository.claim_next_job() is None
