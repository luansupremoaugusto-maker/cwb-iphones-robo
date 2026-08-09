from __future__ import annotations

import pytest

from app.adapters.zapi import normalize_received_callback
from app.config import Settings
from app.processor import MessageProcessor
from app.runtime import build_runtime


def test_admin_command_parser_accepts_bulk_resume_commands():
    incoming = normalize_received_callback(
        {"messageId": "admin-bulk-1", "phone": "5511999999999", "text": {"message": "#retomar_todos"}}
    )
    alias = normalize_received_callback(
        {"messageId": "admin-bulk-2", "phone": "5511999999999", "text": {"message": "#liberar_todos"}}
    )

    assert MessageProcessor._parse_admin_command(incoming) == ("retomar_todos", None)
    assert MessageProcessor._parse_admin_command(alias) == ("liberar_todos", None)


@pytest.mark.asyncio
async def test_bulk_resume_releases_human_conversations_but_keeps_closed():
    settings = Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        admin_phones="5511999999999",
        outbound_mode="disabled",
    )
    runtime = build_runtime(settings, offline=True)
    admin = "5511999999999"
    pending = "5511888888888"
    active = "5511777777777"
    closed = "5511666666666"
    unauthorized = "5511555555555"
    try:
        runtime.repository.set_conversation_status(pending, "human_pending", "aguardando atendente")
        runtime.repository.set_conversation_status(active, "human_active", "atendente assumiu")
        runtime.repository.set_conversation_status(closed, "closed", "encerrada")

        await runtime.processor.process_payload(
            {"messageId": "admin-bulk-3", "phone": admin, "text": {"message": "#retomar_todos"}}
        )
        assert runtime.repository.get_conversation(pending).status == "bot_active"
        assert runtime.repository.get_conversation(active).status == "bot_active"
        assert runtime.repository.get_conversation(closed).status == "closed"

        runtime.repository.set_conversation_status(unauthorized, "human_active", "teste de autorização")
        await runtime.processor.process_payload(
            {"messageId": "admin-bulk-4", "phone": unauthorized, "text": {"message": "#liberar_todos"}}
        )
        assert runtime.repository.get_conversation(unauthorized).status == "human_active"
    finally:
        await runtime.aclose()
