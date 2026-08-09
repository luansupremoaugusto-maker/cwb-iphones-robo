from __future__ import annotations

import asyncio
from datetime import timedelta

from app.config import get_settings
from app.runtime import build_runtime
from app.storage.database import utc_now


async def run_worker() -> None:
    settings = get_settings()
    runtime = build_runtime(settings)
    last_cleanup = utc_now()
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

            claimed = runtime.repository.claim_next_job()
            if claimed is None:
                if utc_now() - last_cleanup > timedelta(hours=1):
                    runtime.repository.cleanup(utc_now() - timedelta(days=settings.retention_days))
                    last_cleanup = utc_now()
                await asyncio.sleep(0.5)
                continue
            job_id, payload = claimed
            try:
                batch_payloads = payload.get("_batch_payloads") if isinstance(payload, dict) else None
                if isinstance(batch_payloads, list):
                    await runtime.processor.process_batch(batch_payloads)
                else:
                    await runtime.processor.process_payload(payload)
            except Exception as exc:
                runtime.repository.fail_job(job_id, f"{type(exc).__name__}: {exc}")
            else:
                runtime.repository.finish_job(job_id)
    finally:
        await runtime.aclose()


if __name__ == "__main__":
    asyncio.run(run_worker())
