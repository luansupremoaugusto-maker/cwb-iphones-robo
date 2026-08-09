from __future__ import annotations

from datetime import timedelta

from app.storage.database import Repository, build_engine, utc_now


def test_repository_is_idempotent_and_persists_job_state():
    repository = Repository(build_engine("sqlite:///:memory:"))
    repository.initialize()

    event_id, created = repository.register_inbound_event("event-1", {"messageId": "m1"})
    duplicate_id, duplicate = repository.register_inbound_event("event-1", {"messageId": "m1"})
    assert (event_id, created) == (duplicate_id, True)
    assert duplicate is False

    job_id = repository.enqueue_job(event_id)
    claimed = repository.claim_next_job()
    assert claimed == (job_id, {"messageId": "m1"})
    repository.finish_job(job_id)
    assert repository.claim_next_job() is None

    repository.add_message("5511999999999", "inbound", "text", "Oi")
    repository.add_message("5511999999999", "outbound", "text", "Olá")
    assert repository.recent_messages("5511999999999") == [
        {"role": "user", "content": "Oi"},
        {"role": "assistant", "content": "Olá"},
    ]

    result = repository.cleanup(utc_now() + timedelta(seconds=1))
    assert result["conversations"] >= 1
