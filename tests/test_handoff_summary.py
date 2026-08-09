from __future__ import annotations

import time

import pytest

from app.config import Settings
from app.processor import _build_handoff_message
from app.runtime import build_runtime
from app.schemas import InventoryItem


def test_handoff_summary_has_client_context_and_safe_commands():
    message = _build_handoff_message(
        "5541999999999",
        "Cliente quer avaliar aparelho usado para parte do pagamento",
        "Quero dar meu iPhone 13 como entrada. IMEI: 123456789012345",
        chat_name="Giovani",
        history=[
            {"role": "user", "content": "Estou em dúvida entre dois modelos."},
            {"role": "assistant", "content": "Posso ajudar."},
        ],
    )

    assert "Cliente avaliando/negociando o usado" in message
    assert "5541999999999 (Giovani)" in message
    assert "Resumo do atendimento" in message
    assert "Estou em dúvida entre dois modelos." in message
    assert "IMEI [oculto]" in message
    assert "123456789012345" not in message
    assert "#assumir 5541999999999" in message
    assert "#retomar 5541999999999" in message
    assert "#fechar 5541999999999" in message


@pytest.mark.asyncio
async def test_processor_sends_structured_handoff_summary_to_admin():
    settings = Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        admin_phones="5541990000000",
        outbound_mode="disabled",
    )
    runtime = build_runtime(settings, offline=True)
    customer = "5541999999999"
    admin = "5541990000000"
    runtime.cache.items = [
        InventoryItem(
            external_id="summary-fixture-15",
            name="iPhone 15",
            description="iPhone 15",
            category="Celular",
            price_brl=3800.0,
            quantity=1,
            availability="Disponível",
            search_text="iphone 15 celular",
        )
    ]
    runtime.cache.last_refresh = time.time()
    try:
        await runtime.processor.process_payload(
            {
                "messageId": "summary-1",
                "phone": customer,
                "senderName": "Giovani",
                "text": {"message": "Estou em dúvida entre o iPhone 15 e o 15 Pro."},
            }
        )
        await runtime.processor.process_payload(
            {
                "messageId": "summary-2",
                "phone": customer,
                "senderName": "Giovani",
                "text": {"message": "Quero falar com um atendente."},
            }
        )
        admin_messages = runtime.repository.recent_messages(admin, limit=10)
    finally:
        await runtime.aclose()

    notification = "\n".join(item["content"] for item in admin_messages if item["role"] == "assistant")
    assert "Atendimento humano solicitado" in notification
    assert f"Cliente: {customer} (Giovani)" in notification
    assert "Estou em dúvida entre o iPhone 15 e o 15 Pro." in notification
    assert f"#assumir {customer}" in notification
