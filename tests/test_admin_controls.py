from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.runtime import build_runtime


def _settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        admin_username="admin",
        admin_password="secret",
        admin_csrf_secret="csrf-secret",
        outbound_mode="disabled",
    )


def test_repository_persists_bot_control_state_and_admin_sessions():
    runtime = build_runtime(_settings(), offline=True)
    try:
        initial = runtime.repository.get_bot_control_state()
        assert initial["mode"] == "active"

        state = runtime.repository.set_bot_control_state(
            "maintenance", "Atualização programada", "admin"
        )
        assert state["mode"] == "maintenance"
        assert state["reason"] == "Atualização programada"
        assert runtime.repository.get_bot_control_state()["operator"] == "admin"

        runtime.repository.register_admin_session(
            "session-1", "admin", runtime.repository.session_expiry(3600)
        )
        sessions = runtime.repository.list_active_admin_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "session-1"
        assert runtime.repository.revoke_admin_sessions() == 1
        assert runtime.repository.list_active_admin_sessions() == []
    finally:
        asyncio.run(runtime.aclose())


@pytest.mark.asyncio
async def test_processor_holds_inbound_messages_while_bot_is_paused():
    runtime = build_runtime(_settings(), offline=True)
    try:
        runtime.repository.set_bot_control_state("paused", "Pausa de teste", "admin")

        await runtime.processor.process_payload(
            {
                "messageId": "paused-message-1",
                "phone": "5511999999999",
                "text": {"message": "Olá, quero saber os disponíveis"},
            }
        )

        events = runtime.repository.list_audit_events(event_type="message_held", limit=5)
        assert events[0]["detail"]["control_mode"] == "paused"
        assert runtime.repository.recent_messages("5511999999999")[0]["content"] == (
            "Olá, quero saber os disponíveis"
        )
        assert not any(
            event["event_type"] == "agent_response"
            for event in runtime.repository.list_audit_events(limit=20)
        )
    finally:
        await runtime.aclose()
