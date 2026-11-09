from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any


logger = logging.getLogger(__name__)


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _count(value: Any) -> int | None:
    if value is None:
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized if normalized >= 0 else None


def usage_metrics(usage: Any, *, requests_fallback: int | None = None) -> dict[str, int | None]:
    """Read token counters from Agents SDK or OpenAI response usage objects."""
    input_tokens = _count(_get(usage, "input_tokens", _get(usage, "prompt_tokens")))
    output_tokens = _count(_get(usage, "output_tokens", _get(usage, "completion_tokens")))
    total_tokens = _count(_get(usage, "total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    input_details = _get(usage, "input_tokens_details") or _get(usage, "prompt_tokens_details")
    output_details = _get(usage, "output_tokens_details") or _get(usage, "completion_tokens_details")
    cached_input_tokens = _count(_get(input_details, "cached_tokens"))
    reasoning_tokens = _count(_get(output_details, "reasoning_tokens"))
    requests = _count(_get(usage, "requests"))
    if requests is None:
        requests = requests_fallback

    return {
        "requests": requests,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": cached_input_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def agent_run_usage(result: Any) -> Any:
    wrapper = _get(result, "context_wrapper")
    return _get(wrapper, "usage")


def record_llm_usage_safely(
    recorder: Callable[..., Any] | None,
    *,
    stage: str,
    model: str,
    status: str,
    duration_ms: int,
    usage: Any = None,
    requests_fallback: int | None = None,
    error: Exception | None = None,
) -> None:
    if recorder is None:
        return
    metrics = usage_metrics(usage, requests_fallback=requests_fallback)
    try:
        recorder(
            stage=stage,
            model=model,
            status=status,
            duration_ms=max(0, int(duration_ms)),
            error_type=type(error).__name__ if error is not None else None,
            **metrics,
        )
    except Exception as exc:
        # Telemetry must never turn a successful customer response into a failure.
        logger.warning("LLM usage recording failed (%s)", type(exc).__name__)
