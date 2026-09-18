from __future__ import annotations

from types import SimpleNamespace

from app.storage.database import Repository, build_engine
import worker


def test_worker_failure_is_audited_against_the_customer_phone():
    repository = Repository(build_engine("sqlite:///:memory:"))
    repository.initialize()
    runtime = SimpleNamespace(repository=repository)
    conversation = repository.get_or_create_conversation("5511999999999")

    record_worker_error = getattr(worker, "record_worker_error", None)
    assert callable(record_worker_error)
    record_worker_error(
        runtime,
        17,
        {
            "_batch_payloads": [
                {
                    "messageId": "worker-error-1",
                    "phone": "5511999999999",
                    "text": {"message": "Oi"},
                }
            ]
        },
        RuntimeError("worker exploded"),
    )

    events = repository.list_audit_events(event_type="worker_error", limit=5)
    assert len(events) == 1
    assert events[0]["subject"] == "5511999999999"
    assert events[0]["detail"] == {
        "job_id": 17,
        "error_type": "RuntimeError",
        "error": "worker exploded",
        "protocol": conversation.protocol,
    }
