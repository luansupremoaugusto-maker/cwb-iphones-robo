from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.main import create_app
from app.runtime import build_runtime
from app.storage.database import Repository, build_engine


@pytest.fixture
def configured_runtime():
    runtime = build_runtime(
        Settings(
            database_url="sqlite:///:memory:",
            admin_username="admin",
            admin_password="secret",
            admin_csrf_secret="csrf-secret",
            outbound_mode="disabled",
        ),
        offline=True,
    )
    try:
        yield runtime
    finally:
        runtime.repository.engine.dispose()


def test_repository_assigns_protocol_and_returns_messages_with_audit_timeline():
    engine = build_engine("sqlite:///:memory:")
    try:
        repository = Repository(engine)
        repository.initialize()

        conversation = repository.get_or_create_conversation("5511999999999", "Maria")
        same_conversation = repository.get_or_create_conversation("5511999999999")
        repository.add_message("5511999999999", "inbound", "text", "Tem iPhone 15?")
        repository.add_message("5511999999999", "outbound", "text", "Temos sim.")
        repository.audit(
            "agent_response",
            "5511999999999",
            {"confidence": "high", "product_references": ["iphone-15-128"]},
        )

        assert re.fullmatch(r"CWB-\d{8}", conversation.protocol or "")
        assert same_conversation.protocol == conversation.protocol

        detail = repository.conversation_detail(conversation.protocol)

        assert detail is not None
        assert detail["protocol"] == conversation.protocol
        assert detail["phone"] == "5511999999999"
        assert [message["text"] for message in detail["messages"]] == [
            "Tem iPhone 15?",
            "Temos sim.",
        ]
        assert detail["audit"][0]["event_type"] == "agent_response"
    finally:
        engine.dispose()


def test_repository_backfills_protocols_in_a_database_created_before_the_feature():
    engine = build_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE conversations ("
                    "id INTEGER PRIMARY KEY, "
                    "phone VARCHAR(32) UNIQUE NOT NULL, "
                    "chat_name VARCHAR(255), "
                    "status VARCHAR(32) NOT NULL, "
                    "paused_reason VARCHAR(255), "
                    "created_at DATETIME NOT NULL, "
                    "updated_at DATETIME NOT NULL"
                    ")"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO conversations "
                    "(id, phone, status, created_at, updated_at) "
                    "VALUES (1, '5511999999999', 'bot_active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

        repository = Repository(engine)
        repository.initialize()
        repository.initialize()

        detail = repository.conversation_detail("5511999999999")

        assert detail is not None
        assert detail["protocol"] == "CWB-00000001"
    finally:
        engine.dispose()


def test_admin_can_open_any_conversation_by_protocol_or_phone(configured_runtime):
    customer = "5511999999999"
    conversation = configured_runtime.repository.get_or_create_conversation(customer, "João")
    configured_runtime.repository.add_message(customer, "inbound", "text", "Onde vocês ficam?")
    configured_runtime.repository.add_message(customer, "outbound", "text", "Estamos em Curitiba.")
    configured_runtime.repository.audit(
        "agent_response",
        customer,
        {"confidence": "high", "handoff": False},
    )

    with TestClient(create_app(configured_runtime)) as client:
        by_protocol = client.get(
            f"/admin/api/conversations/{conversation.protocol}",
            auth=("admin", "secret"),
        )
        by_phone = client.get(
            f"/admin/api/conversations/{customer}",
            auth=("admin", "secret"),
        )

    assert by_protocol.status_code == 200
    assert by_protocol.headers["cache-control"] == "no-store"
    payload = by_protocol.json()
    assert payload["protocol"] == conversation.protocol
    assert payload["phone"] == customer
    assert payload["chat_name"] == "João"
    assert payload["messages"][-1]["text"] == "Estamos em Curitiba."
    assert payload["diagnostics"] == {
        "message_count": 2,
        "inbound_count": 1,
        "outbound_count": 1,
        "audit_count": 1,
        "error_count": 0,
        "handoff_count": 0,
    }
    assert payload["audit"][0]["detail"]["confidence"] == "high"
    assert by_phone.status_code == 200
    assert by_phone.json()["protocol"] == conversation.protocol


def test_admin_conversation_lookup_returns_not_found_for_unknown_reference(configured_runtime):
    with TestClient(create_app(configured_runtime)) as client:
        response = client.get(
            "/admin/api/conversations/CWB-99999999",
            auth=("admin", "secret"),
        )

    assert response.status_code == 404
