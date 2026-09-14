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

    async def list_available_products(self, *, include_photos=False):
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
    assert json_response.headers["cache-control"] == "no-store"
    assert csv_response.status_code == 200
    assert "catalogo-disponiveis.csv" in csv_response.headers["content-disposition"]
    assert csv_response.headers["cache-control"] == "no-store"


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
    assert page.headers["cache-control"] == "no-store"
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
            json={"action": "release_all", "justification": "Conferir a fila antes da abertura"},
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert blocked.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["released_count"] == 2
    assert accepted.headers["cache-control"] == "no-store"
    assert configured_runtime.repository.get_conversation("5511666666666").status == "closed"


def test_admin_dashboard_returns_conversation_counts_and_source_health(configured_runtime):
    with TestClient(create_app(configured_runtime)) as client:
        response = client.get("/admin/api/dashboard", auth=("admin", "secret"))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["conversations"] == {
        "total": 3,
        "bot_active": 0,
        "human_pending": 1,
        "human_active": 1,
        "human_total": 2,
        "closed": 1,
    }
    assert payload["sources"]["database"]["ok"] is True
    assert isinstance(payload["sources"]["zapi"]["ok"], bool)


def test_admin_queue_returns_human_conversations_with_latest_message(configured_runtime):
    configured_runtime.repository.get_or_create_conversation("5511888888888", "Maria")
    configured_runtime.repository.add_message(
        "5511888888888",
        "inbound",
        "text",
        "Quero confirmar o prazo de entrega.",
    )

    with TestClient(create_app(configured_runtime)) as client:
        response = client.get("/admin/api/conversations?status=human", auth=("admin", "secret"))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    items = response.json()["items"]
    item = next(item for item in items if item["phone"] == "5511888888888")
    assert item["chat_name"] == "Maria"
    assert item["status"] == "human_pending"
    assert item["status_label"] == "Aguardando atendimento"
    assert item["last_message"] == "Quero confirmar o prazo de entrega."
    assert item["last_message_direction"] == "inbound"


def test_admin_audit_endpoint_returns_recent_events(configured_runtime):
    configured_runtime.repository.audit(
        "admin_command",
        "5511888888888",
        {"action": "assume", "channel": "web", "operator": "admin", "released_count": 0},
    )

    with TestClient(create_app(configured_runtime)) as client:
        response = client.get("/admin/api/audit?event_type=admin_command", auth=("admin", "secret"))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    item = response.json()["items"][0]
    assert item["event_type"] == "admin_command"
    assert item["subject"] == "5511888888888"
    assert item["detail"]["operator"] == "admin"
    assert item["detail"]["action"] == "assume"


def test_admin_dashboard_does_not_report_mercado_phone_ok_without_credentials(configured_runtime):
    configured_runtime.settings.mercado_phone_api_key = None
    configured_runtime.cache.items = [{"nome": "cache antigo"}]

    with TestClient(create_app(configured_runtime)) as client:
        response = client.get("/admin/api/dashboard", auth=("admin", "secret"))

    assert response.status_code == 200
    assert response.json()["sources"]["mercado_phone"]["ok"] is False


def test_admin_command_preview_requires_csrf_and_does_not_mutate(configured_runtime):
    configured_runtime.repository.set_conversation_status("5511888888888", "human_pending", "aguardando")
    token = build_admin_csrf_token(configured_runtime.settings)
    with TestClient(create_app(configured_runtime)) as client:
        blocked = client.post(
            "/admin/api/commands/preview",
            json={"action": "release_all"},
            auth=("admin", "secret"),
        )
        response = client.post(
            "/admin/api/commands/preview",
            json={"action": "release_all"},
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert blocked.status_code == 403
    assert response.status_code == 200
    assert response.json()["affected_count"] == 2
    assert configured_runtime.repository.get_conversation("5511888888888").status == "human_pending"


def test_admin_web_command_requires_justification(configured_runtime):
    token = build_admin_csrf_token(configured_runtime.settings)
    with TestClient(create_app(configured_runtime)) as client:
        response = client.post(
            "/admin/api/commands",
            json={"action": "release_all"},
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert response.status_code == 400
    assert "justificativa" in response.json()["detail"].lower()


def test_admin_operator_cannot_release_all(configured_runtime):
    configured_runtime.settings.admin_role = "operator"
    token = build_admin_csrf_token(configured_runtime.settings)
    with TestClient(create_app(configured_runtime)) as client:
        response = client.post(
            "/admin/api/commands",
            json={"action": "release_all", "justification": "teste"},
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert response.status_code == 403


def test_admin_control_and_sessions_endpoints_are_authenticated(configured_runtime):
    token = build_admin_csrf_token(configured_runtime.settings)
    with TestClient(create_app(configured_runtime)) as client:
        control = client.get("/admin/api/control", auth=("admin", "secret"))
        sessions = client.get("/admin/api/sessions", auth=("admin", "secret"))
        paused = client.post(
            "/admin/api/control",
            json={
                "action": "pause_bot",
                "reason": "Pausa operacional programada",
                "justification": "Pausa operacional programada",
            },
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert control.status_code == 200
    assert control.json()["state"]["mode"] == "active"
    assert sessions.status_code == 200
    assert isinstance(sessions.json()["items"], list)
    assert paused.status_code == 200
    assert paused.json()["state"]["mode"] == "paused"
    assert configured_runtime.repository.get_bot_control_state()["mode"] == "paused"


def test_admin_dashboard_reports_recent_errors(configured_runtime):
    configured_runtime.repository.audit(
        "outbound_error",
        "5511888888888",
        {"status": 503, "error": "provider indisponível"},
    )

    with TestClient(create_app(configured_runtime)) as client:
        response = client.get("/admin/api/dashboard", auth=("admin", "secret"))

    assert response.status_code == 200
    assert response.json()["monitoring"]["recent_errors"]["count"] == 1
    assert response.json()["monitoring"]["recent_errors"]["by_type"] == {"outbound_error": 1}


def test_browser_admin_session_is_registered_and_revoked_by_logout_all(configured_runtime):
    token = build_admin_csrf_token(configured_runtime.settings)
    with TestClient(create_app(configured_runtime), base_url="https://testserver") as client:
        login = client.post(
            "/admin/login",
            data={"username": "admin", "password": "secret"},
            follow_redirects=False,
        )
        sessions = client.get("/admin/api/sessions")
        logout = client.post(
            "/admin/api/control",
            json={"action": "logout_sessions", "justification": "Encerramento de acessos"},
            headers={"X-Admin-CSRF": token},
        )
        after_logout = client.get("/admin/api/sessions")

    assert login.status_code == 303
    assert sessions.status_code == 200
    assert len(sessions.json()["items"]) == 1
    assert logout.status_code == 200
    assert logout.json()["revoked_count"] == 1
    assert after_logout.status_code == 401
