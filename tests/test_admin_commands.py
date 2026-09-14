from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin import AdminCommandService
from app.config import Settings
from app.runtime import build_runtime
from app.storage.database import AuditEventRecord


def _settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        outbound_mode="disabled",
    )


@pytest.mark.asyncio
async def test_release_all_changes_human_conversations_but_not_closed():
    runtime = build_runtime(_settings(), offline=True)
    try:
        runtime.repository.set_conversation_status("5511888888888", "human_pending", "aguardando")
        runtime.repository.set_conversation_status("5511777777777", "human_active", "em atendimento")
        runtime.repository.set_conversation_status("5511666666666", "closed", "encerrada")

        result = AdminCommandService(runtime.repository).execute(
            "release_all",
            operator="admin",
            channel="web",
        )

        assert result["released_count"] == 2
        assert runtime.repository.get_conversation("5511888888888").status == "bot_active"
        assert runtime.repository.get_conversation("5511777777777").status == "bot_active"
        assert runtime.repository.get_conversation("5511666666666").status == "closed"
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_individual_admin_command_normalizes_phone_and_records_web_audit():
    runtime = build_runtime(_settings(), offline=True)
    try:
        result = AdminCommandService(runtime.repository).execute(
            "assume",
            operator="admin",
            channel="web",
            phone="+55 (41) 97777-6666",
        )

        assert result["status"] == "human_active"
        assert runtime.repository.get_conversation("5541977776666").status == "human_active"
        with Session(runtime.repository.engine) as session:
            audit = session.scalar(
                select(AuditEventRecord).order_by(AuditEventRecord.id.desc())
            )
        assert audit is not None
        assert audit.event_type == "admin_command"
        assert audit.subject == "5541977776666"
        assert audit.detail == {
            "action": "assume",
            "channel": "web",
            "operator": "admin",
            "released_count": 0,
        }
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_admin_command_rejects_unknown_action_and_missing_phone():
    runtime = build_runtime(_settings(), offline=True)
    try:
        service = AdminCommandService(runtime.repository)
        with pytest.raises(ValueError):
            service.execute("run_shell", operator="admin", channel="web")
        with pytest.raises(ValueError):
            service.execute("close", operator="admin", channel="web")
        with pytest.raises(ValueError):
            service.execute("close", operator="admin", channel="web", phone="123")
    finally:
        await runtime.aclose()


@pytest.mark.asyncio
async def test_admin_command_preview_reports_impact_without_mutating_state():
    runtime = build_runtime(_settings(), offline=True)
    try:
        runtime.repository.set_conversation_status("5511888888888", "human_pending", "aguardando")
        runtime.repository.set_conversation_status("5511777777777", "human_active", "em atendimento")

        result = AdminCommandService(runtime.repository).preview("release_all")

        assert result == {
            "action": "release_all",
            "phone": None,
            "current_status": None,
            "target_status": "bot_active",
            "affected_count": 2,
            "message": "2 conversa(s) em atendimento humano serão liberadas para o robô.",
        }
        assert runtime.repository.get_conversation("5511888888888").status == "human_pending"
        assert runtime.repository.get_conversation("5511777777777").status == "human_active"
    finally:
        await runtime.aclose()
