from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.admin import build_admin_csrf_token
from app.config import Settings
from app.main import create_app
from app.runtime import build_runtime


CATALOG = {
    "seminovos": [
        {
            "nome": "iPhone 15",
            "capacidade": "128 GB",
            "condicao": "Seminovo",
            "quantidade": 1,
            "precos_brl": [1900.0],
            "cores": ["Preto"],
            "cor": "Preto",
            "saude_bateria": 95,
            "fotos_disponiveis": 2,
        }
    ],
    "lacrados_pronta_entrega": [
        {
            "nome": "iPhone 17",
            "capacidade": "256 GB",
            "condicao": "Lacrado",
            "quantidade": 1,
            "precos_brl": [4999.0],
            "cores": ["Azul"],
            "cor": "Azul",
            "saude_bateria": None,
            "fotos_disponiveis": 1,
        }
    ],
    "lacrados": [
        {
            "nome": "iPhone 18",
            "capacidade": "256 GB",
            "condicao": "Lacrado por encomenda",
            "quantidade": None,
            "precos_brl": [5999.0],
            "cores": ["Preto"],
            "cor": "Preto",
            "saude_bateria": None,
            "fotos_disponiveis": 0,
        }
    ],
}


class FakeCache:
    last_refresh = 100.0

    async def list_available_products(self):
        return CATALOG


@pytest.fixture
def configured_runtime():
    settings = Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        admin_username="admin",
        admin_password="secret",
        admin_csrf_secret="csrf-secret",
        outbound_mode="disabled",
    )
    runtime = build_runtime(settings, offline=True)
    runtime.cache = FakeCache()
    runtime.google_sheets.last_refresh = 200.0
    runtime.repository.set_conversation_status("5511888888888", "human_pending", "aguardando")
    runtime.repository.set_conversation_status("5511777777777", "human_active", "em atendimento")
    runtime.repository.set_conversation_status("5511666666666", "closed", "encerrada")
    return runtime


@pytest.fixture
def unconfigured_runtime():
    settings = Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        admin_username=None,
        admin_password=None,
        admin_csrf_secret=None,
        outbound_mode="disabled",
    )
    return build_runtime(settings, offline=True)


def test_admin_routes_fail_closed_without_configuration(unconfigured_runtime):
    with TestClient(create_app(unconfigured_runtime)) as client:
        assert client.get("/admin").status_code == 404
        assert client.get("/admin/api/catalog").status_code == 404


def test_admin_catalog_requires_basic_auth_and_returns_json_and_csv(configured_runtime):
    with TestClient(create_app(configured_runtime)) as client:
        json_response = client.get("/admin/api/catalog", auth=("admin", "secret"))
        csv_response = client.get("/admin/api/catalog.csv", auth=("admin", "secret"))

    assert json_response.status_code == 200
    assert json_response.json()["seminovos"][0]["nome"] == "iPhone 15"
    assert csv_response.status_code == 200
    assert "catalogo-disponiveis.csv" in csv_response.headers["content-disposition"]


def test_admin_page_offers_browser_login_without_basic_auth(configured_runtime):
    with TestClient(create_app(configured_runtime)) as client:
        response = client.get("/admin")

    assert response.status_code == 200
    assert "Acesso administrativo" in response.text
    assert 'name="username"' in response.text
    assert 'name="password"' in response.text


def test_admin_login_sets_session_and_opens_catalog(configured_runtime):
    with TestClient(create_app(configured_runtime), base_url="https://testserver") as client:
        login = client.post(
            "/admin/login",
            data={"username": "admin", "password": "secret"},
            follow_redirects=False,
        )
        page = client.get("/admin")
        catalog = client.get("/admin/api/catalog")

    assert login.status_code == 303
    assert login.headers["location"] == "/admin"
    assert page.status_code == 200
    assert "Catálogo de disponíveis" in page.text
    assert catalog.status_code == 200


def test_admin_login_rejects_invalid_credentials(configured_runtime):
    with TestClient(create_app(configured_runtime)) as client:
        response = client.post(
            "/admin/login",
            data={"username": "admin", "password": "wrong"},
        )

    assert response.status_code == 401
    assert "Credenciais administrativas inválidas" in response.text


def test_admin_command_requires_csrf_and_executes_release_all(configured_runtime):
    with TestClient(create_app(configured_runtime)) as client:
        blocked = client.post(
            "/admin/api/commands",
            json={"action": "release_all"},
            auth=("admin", "secret"),
        )
        token = build_admin_csrf_token(configured_runtime.settings)
        accepted = client.post(
            "/admin/api/commands",
            json={"action": "release_all"},
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert blocked.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["released_count"] == 2
    assert configured_runtime.repository.get_conversation("5511666666666").status == "closed"
