from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

import app.instagram as instagram_module
from app.admin import build_admin_csrf_token
from app.config import Settings
from app.instagram import BRASILIA, InstagramService
from app.main import create_app
from app.runtime import build_runtime
from app.storage.database import Repository, build_engine


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        admin_username="admin",
        admin_password="admin-password",
        admin_csrf_secret="c" * 64,
        instagram_app_secret=None,
        instagram_media_dir=str(tmp_path / "instagram"),
        openai_api_key="test-openai-key",
        outbound_mode="disabled",
    )


@pytest.fixture
def repository(settings):
    repo = Repository(build_engine(settings.database_url))
    repo.initialize()
    return repo


@pytest.mark.asyncio
async def test_secret_and_access_token_are_encrypted_and_unified_login_uses_config_id(
    settings, repository
):
    client = httpx.AsyncClient()
    service = InstagramService(settings, repository, http_client=client)
    app_secret = "meta-app-secret-for-test-only"
    access_token = "system-user-token-for-test-only"
    try:
        service.store_app_secret(app_secret)
        service.store_token(access_token)

        secret_record = repository.get_setting("instagram_app_secret")
        token_record = repository.get_setting("instagram_auth")
        assert app_secret not in str(secret_record)
        assert access_token not in str(token_record)
        assert service._configured_app_secret() == app_secret
        assert service._decrypt_token() == access_token
        assert service.status()["app_secret_source"] == "database"

        params = parse_qs(urlparse(service.oauth_url("state-value")).query)
        assert params["config_id"] == [settings.instagram_config_id]
        assert params["response_type"] == ["code"]
        assert params["override_default_response_type"] == ["true"]
        assert params["state"] == ["state-value"]
        assert "scope" not in params
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_unified_login_discovers_only_the_instagram_account_shared_with_app(
    settings, repository
):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer short-lived-test-token"
        assert request.url.path.endswith("/me/assigned_instagram_accounts")
        assert request.url.params["fields"] == "id,username"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "178900001", "username": "cwb.iphones"},
                    {"id": "178900002", "username": "another.business"},
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = InstagramService(settings, repository, http_client=client)
    try:
        account = await service.discover_account("short-lived-test-token")
        assert account == {"id": "178900001", "username": "cwb.iphones"}
        assert repository.get_setting("instagram_account") == account
        assert len(requests) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_daily_publisher_publishes_once_and_skips_a_published_day(
    settings, repository, tmp_path, monkeypatch
):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if request.method == "GET" and path.endswith("/178900001"):
            return httpx.Response(200, json={"id": "178900001", "username": "cwb.iphones"})
        if request.method == "GET" and path.endswith("/178900001/media"):
            return httpx.Response(200, json={"data": [{"caption": "Uma dica anterior"}]})
        if request.method == "POST" and path.endswith("/178900001/media"):
            return httpx.Response(200, json={"id": "container-1"})
        if request.method == "GET" and path.endswith("/container-1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if request.method == "POST" and path.endswith("/178900001/media_publish"):
            return httpx.Response(200, json={"id": "published-media-1"})
        raise AssertionError(f"Unexpected Meta request: {request.method} {path}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = InstagramService(settings, repository, http_client=client)
    service.store_token("daily-publisher-token")
    repository.set_setting("instagram_account", {"id": "178900001", "username": "cwb.iphones"})
    repository.set_setting("instagram_autopublish_enabled", True)

    async def generate_content(date_key: str, recent: list[str]) -> dict[str, str]:
        assert date_key == "2026-11-04"
        assert recent == ["Uma dica anterior"]
        return {
            "title": "Cuide da bateria",
            "subtitle": "Ajuste o brilho para sua rotina",
            "caption": "Uma dica simples para o dia a dia. Salve e compartilhe! #iPhone",
        }

    def render_image(filename: str, content: dict[str, str]):
        path = tmp_path / filename
        path.write_bytes(b"test-image")
        return path

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 4, 12, 1, tzinfo=BRASILIA)

    monkeypatch.setattr(instagram_module, "datetime", FixedDateTime)
    monkeypatch.setattr(service, "_generate_content", generate_content)
    monkeypatch.setattr(service, "_render_image", render_image)
    try:
        await service.publish_due()
        daily = repository.get_setting("instagram_daily_post:2026-11-04")
        assert daily["status"] == "published"
        assert daily["media_id"] == "published-media-1"
        assert repository.get_setting("instagram_last_published")["date"] == "2026-11-04"
        assert len(requests) == 5

        await service.publish_due()
        assert len(requests) == 5
    finally:
        await client.aclose()


def test_app_secret_route_requires_owner_auth_and_csrf_and_never_returns_secret(settings):
    runtime = build_runtime(settings, offline=True)
    secret = "meta-app-secret-for-route-test"
    csrf = build_admin_csrf_token(settings)
    with TestClient(create_app(runtime)) as client:
        unauthenticated = client.post(
            "/admin/api/instagram/app-secret", json={"app_secret": secret}
        )
        missing_csrf = client.post(
            "/admin/api/instagram/app-secret",
            auth=("admin", "admin-password"),
            json={"app_secret": secret},
        )
        saved = client.post(
            "/admin/api/instagram/app-secret",
            auth=("admin", "admin-password"),
            headers={"x-admin-csrf": csrf},
            json={"app_secret": secret},
        )

    assert unauthenticated.status_code == 401
    assert missing_csrf.status_code == 403
    assert saved.status_code == 200
    assert secret not in saved.text
    assert runtime.repository.get_setting("instagram_app_secret")["secret_ciphertext"] != secret
