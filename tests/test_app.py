from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.runtime import build_runtime


def test_webhook_validates_secret_and_deduplicates():
    settings = Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        zapi_webhook_secret="secret-test",
        zapi_expected_instance_id="",
        outbound_mode="disabled",
    )
    runtime = build_runtime(settings, offline=True)
    app = create_app(runtime)
    payload = {"messageId": "m-1", "phone": "5511999999999", "text": {"message": "Tem iPhone?"}}

    with TestClient(app) as client:
        wrong = client.post("/webhooks/zapi/wrong", json=payload)
        first = client.post("/webhooks/zapi/secret-test", json=payload)
        second = client.post("/webhooks/zapi/secret-test", json=payload)

    assert wrong.status_code == 404
    assert first.status_code == 200 and first.json()["accepted"] is True
    assert "job_id" in first.json()
    assert second.status_code == 200 and second.json()["duplicate"] is True


def test_ready_requires_loaded_and_fresh_catalog_sources():
    class FakeRepository:
        @staticmethod
        def healthcheck():
            return True

    class FakeRuntime:
        settings = Settings(
            openai_api_key="configured",
            mercado_phone_api_key="configured",
            zapi_instance_id="instance",
            zapi_token="token",
            zapi_client_token="client-token",
        )
        repository = FakeRepository()
        cache = SimpleNamespace(ready=False)
        google_sheets = SimpleNamespace(ready=False, items=[])

        async def aclose(self):
            return None

    app = create_app(FakeRuntime())

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["ready"] is False
    assert response.json()["checks"]["mercado_phone"] is False
    assert response.json()["checks"]["google_sheets"] is False
