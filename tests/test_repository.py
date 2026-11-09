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


def test_repository_aggregates_llm_usage_by_period_and_stage():
    repository = Repository(build_engine("sqlite:///:memory:"))
    repository.initialize()
    repository.record_llm_usage(
        stage="customer_agent",
        model="gpt-6-luna",
        requests=2,
        input_tokens=50,
        output_tokens=30,
        total_tokens=80,
        cached_input_tokens=10,
        reasoning_tokens=5,
        duration_ms=1200,
    )
    repository.record_llm_usage(
        stage="audio_transcription",
        model="gpt-transcribe",
        requests=1,
        duration_ms=800,
    )
    repository.record_llm_usage(
        stage="image_analysis",
        model="gpt-6-luna",
        status="error",
        duration_ms=300,
        error_type="TimeoutError",
    )
    repository.set_llm_daily_token_budget(100)

    result = repository.llm_usage_summary(days=30)

    assert result["today"]["calls"] == 3
    assert result["today"]["requests"] == 3
    assert result["today"]["input_tokens"] == 50
    assert result["today"]["output_tokens"] == 30
    assert result["today"]["total_tokens"] == 80
    assert result["today"]["cached_input_tokens"] == 10
    assert result["today"]["reasoning_tokens"] == 5
    assert result["today"]["unknown_token_calls"] == 2
    assert result["today"]["errors"] == 1
    assert result["month_to_date"]["calls"] == 3
    assert result["daily"][0]["date"] == utc_now().date().isoformat()
    assert len(result["monthly"]) == 12
    assert result["monthly"][0]["month"] == utc_now().strftime("%Y-%m")
    assert result["monthly"][0]["calls"] == 3
    assert result["monthly_stages"][0]["month"] == utc_now().strftime("%Y-%m")
    assert {item["stage"] for item in result["stages"]} == {
        "customer_agent",
        "audio_transcription",
        "image_analysis",
    }
    assert result["budget"] == {
        "daily_token_limit": 100,
        "used_tokens": 80,
        "percent": 80.0,
        "status": "warning",
        "tracking_incomplete": True,
    }


def test_llm_usage_rejects_noninstrumented_stage_and_can_disable_budget():
    repository = Repository(build_engine("sqlite:///:memory:"))
    repository.initialize()

    try:
        repository.record_llm_usage(stage="unknown_stage", model="gpt-6-luna", error_type="ValueError")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid stages must not enter the usage table")

    repository.set_llm_daily_token_budget(500)
    assert repository.set_llm_daily_token_budget(0) is None
    assert repository.llm_usage_summary()["budget"]["status"] == "unconfigured"
