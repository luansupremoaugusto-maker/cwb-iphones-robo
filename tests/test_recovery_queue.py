from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin import build_admin_csrf_token
from app.adapters.zapi import ZapiError
from app.config import Settings
from app.main import create_app
from app.runtime import build_runtime
from app.schemas import AgentDecision
from app.storage.database import ConversationRecord, MessageRecord, utc_now


def _runtime():
    return build_runtime(
        Settings(
            database_url="sqlite:///:memory:",
            openai_api_key=None,
            admin_username="admin",
            admin_password="secret",
            admin_csrf_secret="csrf-secret",
            outbound_mode="disabled",
        ),
        offline=True,
    )


def _make_pending(runtime, phone: str, message: str, age_hours: int) -> int:
    runtime.repository.set_conversation_status(phone, "human_pending", "aguardando")
    message_id = runtime.repository.add_message(phone, "inbound", "text", message)
    timestamp = utc_now() - timedelta(hours=age_hours)
    with Session(runtime.repository.engine) as session:
        conversation = session.scalar(
            select(ConversationRecord).where(ConversationRecord.phone == phone)
        )
        stored_message = session.get(MessageRecord, message_id)
        assert conversation is not None
        assert stored_message is not None
        conversation.updated_at = timestamp
        stored_message.created_at = timestamp
        session.commit()
    return message_id


def _age_messages(runtime, phone: str, message_ids: list[int], age_hours: int) -> None:
    timestamp = utc_now() - timedelta(hours=age_hours)
    with Session(runtime.repository.engine) as session:
        conversation = session.scalar(
            select(ConversationRecord).where(ConversationRecord.phone == phone)
        )
        assert conversation is not None
        conversation.updated_at = timestamp
        for message_id in message_ids:
            stored_message = session.get(MessageRecord, message_id)
            assert stored_message is not None
            stored_message.created_at = timestamp
        session.commit()


def test_recovery_queue_returns_only_old_pending_conversations_in_oldest_order():
    runtime = _runtime()
    _make_pending(runtime, "5511999999999", "Quero o iPhone 15", age_hours=72)
    _make_pending(runtime, "5511888888888", "Ainda tenho interesse", age_hours=48)
    _make_pending(runtime, "5511777777777", "Acabei de mandar mensagem", age_hours=2)
    runtime.repository.set_conversation_status("5511666666666", "human_active", "em atendimento")
    _make_pending(runtime, "5511555555555", "Não deve entrar", age_hours=96)
    runtime.repository.set_conversation_status("5511555555555", "bot_active", "liberada")

    with TestClient(create_app(runtime)) as client:
        response = client.get(
            "/admin/api/recovery?older_than_hours=24&limit=1&offset=0",
            auth=("admin", "secret"),
        )
        next_response = client.get(
            "/admin/api/recovery?older_than_hours=24&limit=1&offset=1",
            auth=("admin", "secret"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["has_more"] is True
    assert [item["phone"] for item in payload["items"]] == ["5511999999999"]
    assert all(item["status"] == "human_pending" for item in payload["items"])
    assert all(item["last_message_id"] for item in payload["items"])
    assert next_response.status_code == 200
    assert next_response.json()["has_more"] is False
    assert [item["phone"] for item in next_response.json()["items"]] == ["5511888888888"]


def test_recovery_queue_includes_old_bot_conversations_with_customer_history_for_review():
    runtime = _runtime()
    runtime.repository.get_or_create_conversation("5511000000001", "Pausa")
    unanswered_id = runtime.repository.add_message(
        "5511000000001", "inbound", "text", "Fiquei aguardando o retorno de vocês."
    )
    _age_messages(runtime, "5511000000001", [unanswered_id], age_hours=72)

    runtime.repository.get_or_create_conversation("5511000000002", "Respondido")
    inbound_id = runtime.repository.add_message(
        "5511000000002", "inbound", "text", "Tem iPhone 15?"
    )
    outbound_id = runtime.repository.add_message(
        "5511000000002", "outbound", "text", "Sim, temos."
    )
    _age_messages(runtime, "5511000000002", [inbound_id, outbound_id], age_hours=72)

    with TestClient(create_app(runtime)) as client:
        response = client.get(
            "/admin/api/recovery?older_than_hours=24",
            auth=("admin", "secret"),
        )

    assert response.status_code == 200
    phones = [item["phone"] for item in response.json()["items"]]
    assert "5511000000001" in phones
    assert "5511000000002" in phones


def test_recovery_queue_includes_old_bot_conversations_with_latest_outbound_reply_for_review():
    runtime = _runtime()
    runtime.repository.get_or_create_conversation("5511000000007", "Resposta automática")
    inbound_id = runtime.repository.add_message(
        "5511000000007", "inbound", "text", "Ainda tenho interesse no aparelho."
    )
    outbound_id = runtime.repository.add_message(
        "5511000000007", "outbound", "text", "Por nada! Qualquer coisa, é só chamar."
    )
    _age_messages(runtime, "5511000000007", [inbound_id, outbound_id], age_hours=72)

    with TestClient(create_app(runtime)) as client:
        response = client.get(
            "/admin/api/recovery?older_than_hours=24",
            auth=("admin", "secret"),
        )

    assert response.status_code == 200
    item = next(
        item for item in response.json()["items"] if item["phone"] == "5511000000007"
    )
    assert item["last_message_direction"] == "outbound"
    assert item["last_message"] == "Por nada! Qualquer coisa, é só chamar."


def test_recovery_draft_accepts_old_bot_conversation_with_unanswered_customer_message():
    runtime = _runtime()
    runtime.repository.get_or_create_conversation("5511000000003", "Cliente antigo")
    latest_id = runtime.repository.add_message(
        "5511000000003", "inbound", "text", "Ainda posso comprar o iPhone 15?"
    )
    _age_messages(runtime, "5511000000003", [latest_id], age_hours=72)
    recording_agent = _RecordingAgent()
    runtime.agent = recording_agent
    token = build_admin_csrf_token(runtime.settings)

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/admin/api/recovery/draft",
            json={"phone": "5511000000003"},
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "bot_active"
    assert response.json()["source_message_id"] == latest_id
    assert recording_agent.text == "Ainda posso comprar o iPhone 15?"


def test_recovery_draft_uses_customer_message_when_bot_replied_last():
    runtime = _runtime()
    runtime.repository.get_or_create_conversation("5511000000008", "Cliente antigo")
    customer_id = runtime.repository.add_message(
        "5511000000008", "inbound", "text", "Ainda tenho interesse no iPhone 15."
    )
    bot_reply_id = runtime.repository.add_message(
        "5511000000008", "outbound", "text", "Durante a viagem, continuo atendendo online."
    )
    _age_messages(runtime, "5511000000008", [customer_id, bot_reply_id], age_hours=72)
    recording_agent = _RecordingAgent()
    runtime.agent = recording_agent
    token = build_admin_csrf_token(runtime.settings)

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/admin/api/recovery/draft",
            json={"phone": "5511000000008"},
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_message_id"] == customer_id
    assert payload["last_message_id"] == bot_reply_id
    assert recording_agent.text == "Ainda tenho interesse no iPhone 15."


def test_recovery_send_accepts_old_bot_conversation_after_reviewed_draft():
    runtime = _runtime()
    runtime.repository.get_or_create_conversation("5511000000004", "Cliente antigo")
    latest_id = runtime.repository.add_message(
        "5511000000004", "inbound", "text", "Ainda posso comprar o iPhone 15?"
    )
    _age_messages(runtime, "5511000000004", [latest_id], age_hours=72)
    token = build_admin_csrf_token(runtime.settings)

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/admin/api/recovery/send",
            json={
                "phone": "5511000000004",
                "message": "Olá! Retomando seu atendimento.",
                "expected_last_message_id": latest_id,
            },
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert response.status_code == 200
    assert response.json()["suppressed"] is True
    assert runtime.repository.get_conversation("5511000000004").status == "human_active"


def test_recovery_skip_hides_conversation_until_new_customer_message():
    runtime = _runtime()
    phone = "5511000000005"
    latest_id = _make_pending(runtime, phone, "Ainda tem iPhone 15?", age_hours=72)
    token = build_admin_csrf_token(runtime.settings)

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/admin/api/recovery/skip",
            json={"phone": phone, "expected_last_message_id": latest_id},
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )
        hidden = client.get(
            "/admin/api/recovery?older_than_hours=24",
            auth=("admin", "secret"),
        )

    assert response.status_code == 200
    assert response.json()["skipped"] is True
    assert phone not in {item["phone"] for item in hidden.json()["items"]}

    outbound_id = runtime.repository.add_message(phone, "outbound", "text", "Tudo bem.")
    _age_messages(runtime, phone, [latest_id, outbound_id], age_hours=72)
    with TestClient(create_app(runtime)) as client:
        still_hidden = client.get(
            "/admin/api/recovery?older_than_hours=24",
            auth=("admin", "secret"),
        )
    assert phone not in {item["phone"] for item in still_hidden.json()["items"]}

    inbound_id = runtime.repository.add_message(phone, "inbound", "text", "E o preço?")
    _age_messages(runtime, phone, [latest_id, outbound_id, inbound_id], age_hours=72)
    with TestClient(create_app(runtime)) as client:
        visible_again = client.get(
            "/admin/api/recovery?older_than_hours=24",
            auth=("admin", "secret"),
        )
    assert phone in {item["phone"] for item in visible_again.json()["items"]}


def test_recovery_skip_rejects_stale_conversation_after_new_customer_message():
    runtime = _runtime()
    phone = "5511000000006"
    latest_id = _make_pending(runtime, phone, "Ainda tem iPhone 15?", age_hours=72)
    runtime.repository.add_message(phone, "inbound", "text", "E o preço?")
    token = build_admin_csrf_token(runtime.settings)

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/admin/api/recovery/skip",
            json={"phone": phone, "expected_last_message_id": latest_id},
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert response.status_code == 409
    assert "nova mensagem" in response.json()["detail"].lower()


class _RecordingAgent:
    def __init__(self):
        self.text = None
        self.history = None

    async def respond(self, text, history=None, image_description=None):
        self.text = text
        self.history = history
        return AgentDecision(reply="Resposta preparada com dados atuais.", confidence="high")


class _AttachmentAgent:
    async def respond(self, text, history=None, image_description=None):
        return AgentDecision(
            reply="Seguem as fotos do aparelho.",
            confidence="high",
            image_urls=["https://photos.example/iphone-15.jpg"],
        )


def test_recovery_draft_uses_latest_customer_message_and_keeps_history_context():
    runtime = _runtime()
    runtime.repository.set_conversation_status("5511444444444", "human_pending", "aguardando")
    runtime.repository.add_message("5511444444444", "inbound", "text", "Quero um iPhone 15")
    runtime.repository.add_message("5511444444444", "outbound", "text", "Qual capacidade você procura?")
    latest_id = runtime.repository.add_message(
        "5511444444444",
        "inbound",
        "text",
        "Pode ser 256 GB e parcelado?",
    )
    recording_agent = _RecordingAgent()
    runtime.agent = recording_agent
    token = build_admin_csrf_token(runtime.settings)

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/admin/api/recovery/draft",
            json={"phone": "5511444444444"},
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert recording_agent.text == "Pode ser 256 GB e parcelado?"
    assert recording_agent.history[-1]["content"] == "Qual capacidade você procura?"
    assert payload["source_message_id"] == latest_id
    assert payload["last_message_id"] == latest_id
    assert payload["draft"].startswith("Olá! Desculpe a demora")
    assert "Resposta preparada com dados atuais." in payload["draft"]
    assert payload["review_required"] is False


def test_recovery_draft_blocks_text_only_send_when_agent_found_attachments():
    runtime = _runtime()
    runtime.repository.set_conversation_status("5511444444445", "human_pending", "aguardando")
    runtime.repository.add_message(
        "5511444444445", "inbound", "text", "Pode mandar as fotos do iPhone 15?"
    )
    runtime.agent = _AttachmentAgent()
    token = build_admin_csrf_token(runtime.settings)

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/admin/api/recovery/draft",
            json={"phone": "5511444444445"},
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert response.status_code == 200
    assert response.json()["attachment_count"] == 1
    assert response.json()["review_required"] is True
    assert response.json()["send_allowed"] is False


def test_recovery_send_rejects_stale_draft_after_new_customer_message():
    runtime = _runtime()
    runtime.repository.set_conversation_status("5511333333333", "human_pending", "aguardando")
    original_id = runtime.repository.add_message(
        "5511333333333", "inbound", "text", "Tem iPhone 15?"
    )
    runtime.repository.add_message("5511333333333", "inbound", "text", "E o preço?")
    token = build_admin_csrf_token(runtime.settings)

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/admin/api/recovery/send",
            json={
                "phone": "5511333333333",
                "message": "Resposta antiga",
                "expected_last_message_id": original_id,
            },
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert response.status_code == 409
    assert "nova mensagem" in response.json()["detail"].lower()
    assert not any(
        item["role"] == "assistant" and item["content"] == "Resposta antiga"
        for item in runtime.repository.recent_messages("5511333333333")
    )


def test_recovery_send_persists_manual_reply_and_moves_conversation_to_active():
    runtime = _runtime()
    runtime.repository.set_conversation_status("5511222222222", "human_pending", "aguardando")
    latest_id = runtime.repository.add_message(
        "5511222222222", "inbound", "text", "Quero saber o valor"
    )
    token = build_admin_csrf_token(runtime.settings)

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/admin/api/recovery/send",
            json={
                "phone": "5511222222222",
                "message": "Olá! Retomando seu atendimento: vou confirmar o valor atual.",
                "expected_last_message_id": latest_id,
            },
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert response.status_code == 200
    assert response.json()["sent"] is False
    assert response.json()["suppressed"] is True
    assert runtime.repository.get_conversation("5511222222222").status == "human_active"
    assert runtime.repository.recent_messages("5511222222222")[-1]["content"].startswith("Olá!")


class _FailingZapi:
    async def send_text(self, phone, message, reply_to=None):
        raise ZapiError("falha simulada", status_code=503)


def test_recovery_send_reports_provider_failure_without_claiming_success():
    runtime = _runtime()
    runtime.repository.set_conversation_status("5511111111111", "human_pending", "aguardando")
    latest_id = runtime.repository.add_message(
        "5511111111111", "inbound", "text", "Ainda está disponível?"
    )
    runtime.processor.zapi = _FailingZapi()
    token = build_admin_csrf_token(runtime.settings)

    with TestClient(create_app(runtime)) as client:
        response = client.post(
            "/admin/api/recovery/send",
            json={
                "phone": "5511111111111",
                "message": "Olá! Vou confirmar para você.",
                "expected_last_message_id": latest_id,
            },
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )

    assert response.status_code == 502
    assert "não foi possível enviar" in response.json()["detail"].lower()
    assert runtime.repository.get_conversation("5511111111111").status == "human_pending"


def test_admin_page_contains_recovery_queue_and_draft_controls():
    from app.admin_page import render_admin_page

    html = render_admin_page("csrf-token")

    assert "Recuperação pós-viagem" in html
    assert 'id="recovery-queue-body"' in html
    assert "/admin/api/recovery" in html
    assert "/admin/api/recovery/draft" in html
    assert "/admin/api/recovery/send" in html
    assert "/admin/api/recovery/skip" in html
    assert "Preparar resposta" in html
    assert "Pular" in html
    assert "inclusive quando o robô respondeu por último" in html
    assert 'id="recovery-more"' in html
    assert html.index('id="recovery-editor"') < html.index('id="recovery-queue-body"')
