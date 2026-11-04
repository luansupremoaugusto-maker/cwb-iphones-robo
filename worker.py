from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from app.adapters.zapi import normalize_received_callback
from app.config import get_settings
from app.runtime import build_runtime
from app.storage.database import JOB_LEASE_SECONDS, utc_now

logger = logging.getLogger(__name__)


async def _maintain_job_lease(repository: Any, job_id: int, attempt: int) -> None:
    while True:
        await asyncio.sleep(max(1, JOB_LEASE_SECONDS // 3))
        try:
            if not repository.renew_job_lease(job_id, expected_attempts=attempt):
                return
        except Exception:
            # A transient database error should not stop later renewal attempts.
            continue


async def _recover_unlinked_inbound_events(repository: Any, debounce_seconds: int) -> None:
    events = await asyncio.to_thread(repository.unlinked_inbound_events, limit=100)
    for event_id, payload in events:
        try:
            incoming = normalize_received_callback(payload)
            if (
                not incoming.phone
                or incoming.from_me
                or incoming.is_group
                or incoming.is_newsletter
                or incoming.is_status_reply
            ):
                continue
            await asyncio.to_thread(
                repository.enqueue_job,
                event_id,
                phone=incoming.phone,
                debounce_seconds=debounce_seconds,
            )
        except Exception as exc:
            logger.warning("orphan inbound event recovery failed: %s", type(exc).__name__)


def _payload_phone(payload: dict[str, Any]) -> str | None:
    batch_payloads = payload.get("_batch_payloads") if isinstance(payload, dict) else None
    candidates = batch_payloads if isinstance(batch_payloads, list) else [payload]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        try:
            incoming = normalize_received_callback(candidate)
        except Exception:
            continue
        if incoming.phone:
            return incoming.phone
    return None


def record_worker_error(
    runtime: Any,
    job_id: int,
    payload: dict[str, Any],
    exc: Exception,
    attempt: int | None = None,
) -> None:
    """Keep unexpected worker failures attached to the conversation trace."""
    phone = _payload_phone(payload)
    conversation = runtime.repository.get_conversation(phone) if phone else None
    runtime.repository.audit(
        "worker_error",
        phone,
        {
            "job_id": int(job_id),
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
            "protocol": conversation.protocol if conversation is not None else None,
        },
    )
    runtime.repository.fail_job(
        job_id,
        f"{type(exc).__name__}: {exc}",
        expected_attempts=attempt,
    )


async def run_worker() -> None:
    settings = get_settings()
    runtime = build_runtime(settings)
    last_cleanup = utc_now()
    last_orphan_recovery = utc_now() - timedelta(seconds=10)
    last_inventory_refresh = utc_now() - timedelta(seconds=settings.mercado_refresh_interval_seconds)
    last_sheets_refresh = utc_now() - timedelta(seconds=settings.google_sheets_refresh_interval_seconds)
    try:
        while True:
            if utc_now() - last_inventory_refresh >= timedelta(seconds=settings.mercado_refresh_interval_seconds):
                try:
                    count = await runtime.cache.refresh(force=True)
                    runtime.repository.audit("inventory_refresh", None, {"items": count})
                except Exception as exc:
                    runtime.repository.audit(
                        "inventory_refresh_error",
                        None,
                        {"error": f"{type(exc).__name__}: {exc}"[:1000]},
                    )
                finally:
                    last_inventory_refresh = utc_now()

            if (
                runtime.google_sheets.enabled
                and utc_now() - last_sheets_refresh
                >= timedelta(seconds=settings.google_sheets_refresh_interval_seconds)
            ):
                try:
                    result = await runtime.google_sheets.refresh(force=True)
                    runtime.repository.audit("google_sheets_refresh", None, result)
                except Exception as exc:
                    runtime.repository.audit(
                        "google_sheets_refresh_error",
                        None,
                        {"error": f"{type(exc).__name__}: {exc}"[:1000]},
                    )
                finally:
                    last_sheets_refresh = utc_now()

            if utc_now() - last_orphan_recovery >= timedelta(seconds=10):
                try:
                    await _recover_unlinked_inbound_events(
                        runtime.repository,
                        settings.message_batch_wait_seconds,
                    )
                except Exception as exc:
                    logger.warning("orphan inbound event scan failed: %s", type(exc).__name__)
                finally:
                    last_orphan_recovery = utc_now()

            claimed = runtime.repository.claim_next_job(include_attempt=True)
            if claimed is None:
                if utc_now() - last_cleanup > timedelta(hours=1):
                    runtime.repository.cleanup(utc_now() - timedelta(days=settings.retention_days))
                    last_cleanup = utc_now()
                await asyncio.sleep(0.5)
                continue
            job_id, payload, attempt = claimed
            lease_task = asyncio.create_task(_maintain_job_lease(runtime.repository, job_id, attempt))
            try:
                try:
                    batch_payloads = payload.get("_batch_payloads") if isinstance(payload, dict) else None
                    if isinstance(batch_payloads, list):
                        await runtime.processor.process_batch(batch_payloads)
                    else:
                        await runtime.processor.process_payload(payload)
                except Exception as exc:
                    record_worker_error(runtime, job_id, payload, exc, attempt)
                else:
                    runtime.repository.finish_job(job_id, expected_attempts=attempt)
            finally:
                lease_task.cancel()
                await asyncio.gather(lease_task, return_exceptions=True)
    finally:
        await runtime.aclose()


if __name__ == "__main__":
    asyncio.run(run_worker())
