from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.runtime import build_runtime
from app.schemas import AgentDecision
from app.storage.database import MessageBatchRecord, Repository, build_engine, utc_now


def test_debounce_batch_waits_and_coalesces_events():
    repository = Repository(build_engine("sqlite:///:memory:"))
    repository.initialize()

    first_id, _ = repository.register_inbound_event("batch-1", {"messageId": "m1", "phone": "5511999999999"})
    second_id, _ = repository.register_inbound_event("batch-2", {"messageId": "m2", "phone": "5511999999999"})
    first_job = repository.enqueue_job(first_id, phone="5511999999999", debounce_seconds=10)
    assert repository.claim_next_job() is None

    second_job = repository.enqueue_job(second_id, phone="5511999999999", debounce_seconds=10)
    assert second_job == first_job

    with Session(repository.engine) as session:
        batch = session.get(MessageBatchRecord, first_job)
        assert batch is not None
        batch.available_at = utc_now() - timedelta(seconds=1)
        session.commit()

    claimed = repository.claim_next_job()
    assert claimed is not None
    assert claimed[0] == -first_job
    assert [item["messageId"] for item in claimed[1]["_batch_payloads"]] == ["m1", "m2"]


@pytest.mark.asyncio
async def test_processor_calls_agent_once_and_sends_one_reply_for_batch():
    settings = Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        mercado_phone_api_key=None,
        outbound_mode="disabled",
    )
    runtime = build_runtime(settings, offline=True)
    calls: list[str] = []

    class FakeAgent:
        async def respond(self, text, history=None, image_description=None):
            calls.append(text)
            return AgentDecision(reply="Recebi suas mensagens juntas.")

    runtime.processor.agent = FakeAgent()
    try:
        await runtime.processor.process_batch(
            [
                {"messageId": "b1", "phone": "5511888888888", "text": {"message": "Oi"}},
                {"messageId": "b2", "phone": "5511888888888", "text": {"message": "Quero um iPhone 15"}},
            ]
        )
        assert calls == ["Oi\nQuero um iPhone 15"]
        history = runtime.repository.recent_messages("5511888888888", limit=10)
        assert [item["role"] for item in history].count("user") == 2
        assert [item["role"] for item in history].count("assistant") == 1
    finally:
        await runtime.aclose()
