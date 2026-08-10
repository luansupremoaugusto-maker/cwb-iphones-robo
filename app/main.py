from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.adapters.zapi import normalize_received_callback
from app.config import get_settings
from app.runtime import Runtime, build_runtime


CONTROL_CALLBACK_MARKERS = ("delivery", "status", "disconnect", "connection")
logger = logging.getLogger(__name__)


def _source_has_snapshot(source: Any, *, require_rates: bool = False) -> bool:
    """Report whether a configured source has usable persisted data.

    Freshness is enforced by each catalog query through ``ensure_fresh``. The
    readiness probe should not become unhealthy merely because the in-memory
    snapshot crossed its short refresh threshold while the worker is alive.
    """
    if not getattr(source, "enabled", True):
        return True
    if not getattr(source, "configured", True):
        return False
    try:
        last_refresh = float(getattr(source, "last_refresh", 0.0))
    except (TypeError, ValueError):
        return False
    if not getattr(source, "items", None) or last_refresh <= 0:
        return False
    if require_rates and not getattr(source, "rates", None):
        return False
    return True


async def _prime_production_sources(runtime: Runtime) -> None:
    """Load the API process' own caches before reporting readiness."""
    if runtime.settings.mercado_phone_api_key:
        try:
            await runtime.cache.refresh(force=False)
        except Exception as exc:
            logger.warning("initial Mercado Phone refresh failed: %s", type(exc).__name__)

    if runtime.google_sheets.enabled:
        try:
            await runtime.google_sheets.refresh(force=False)
        except Exception as exc:
            logger.warning("initial Google Sheets refresh failed: %s", type(exc).__name__)


def create_app(runtime: Runtime | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        current = runtime or build_runtime(get_settings())
        app.state.runtime = current
        if runtime is None and current.settings.app_env == "production":
            await _prime_production_sources(current)
        yield
        await app.state.runtime.aclose()

    app = FastAPI(title="Robo loja WhatsApp", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        current: Runtime = request.app.state.runtime
        return {
            "status": "ok",
            "database": current.repository.healthcheck(),
            "google_sheets_cache": len(current.google_sheets.items),
        }

    @app.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        current: Runtime = request.app.state.runtime
        checks = {
            "database": current.repository.healthcheck(),
            "openai": bool(current.settings.openai_api_key),
            "mercado_phone": bool(current.settings.mercado_phone_api_key)
            and _source_has_snapshot(current.cache),
            # The prices cache intentionally keeps rates empty: installment
            # rates are fixed in app.installments, not loaded from Sheets.
            "google_sheets": _source_has_snapshot(current.google_sheets),
            "zapi": bool(
                current.settings.zapi_instance_id
                and current.settings.zapi_token
            ),
        }
        payload = {"ready": all(checks.values()), "checks": checks}
        return JSONResponse(payload, status_code=200 if payload["ready"] else 503)

    @app.post("/webhooks/zapi/{webhook_secret}")
    async def zapi_webhook(webhook_secret: str, request: Request) -> dict[str, Any]:
        current: Runtime = request.app.state.runtime
        if not hmac.compare_digest(webhook_secret, current.settings.zapi_webhook_secret):
            raise HTTPException(status_code=404, detail="not found")
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="payload must be an object")

        expected_instance = current.settings.zapi_expected_instance_id
        if expected_instance and str(payload.get("instanceId") or "") != expected_instance:
            current.repository.audit("ignored_event", None, {"reason": "wrong_instance"})
            return {"accepted": True, "ignored": True}

        callback_type = str(payload.get("type") or payload.get("event") or "").strip().lower()
        if callback_type and any(marker in callback_type for marker in CONTROL_CALLBACK_MARKERS):
            current.repository.audit(
                "zapi_callback",
                str(payload.get("phone") or "") or None,
                {"type": callback_type, "keys": sorted(str(key) for key in payload.keys())},
            )
            return {"accepted": True, "ignored": True, "callback": callback_type}

        incoming = normalize_received_callback(payload)
        if not incoming.phone or incoming.from_me or incoming.is_group or incoming.is_newsletter or incoming.is_status_reply:
            current.repository.audit("ignored_event", incoming.phone or None, {"reason": "provider_control_event"})
            return {"accepted": True, "ignored": True}

        external_id = incoming.event_id or hashlib.sha256(await request.body()).hexdigest()
        event_id, created = current.repository.register_inbound_event(external_id, payload)
        if not created:
            return {"accepted": True, "duplicate": True}
        job_id = current.repository.enqueue_job(
            event_id,
            phone=incoming.phone,
            debounce_seconds=current.settings.message_batch_wait_seconds,
        )
        return {
            "accepted": True,
            "job_id": job_id,
            "debounce_seconds": current.settings.message_batch_wait_seconds,
        }

    return app


app = create_app()


def build_smoke_settings():
    from app.config import Settings

    return Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        mercado_phone_api_key=None,
        outbound_mode="disabled",
    )


async def offline_smoke() -> None:
    from app.schemas import InventoryItem

    current = build_runtime(build_smoke_settings(), offline=True)
    try:
        current.cache.items = [
            InventoryItem(
                external_id="smoke-13",
                name="iPhone 13 128GB",
                description="iPhone 13 128GB",
                category="Celular",
                price_brl=1999.0,
                quantity=1,
                availability="Disponível para venda",
                updated_at="smoke",
                search_text="iphone 13 128gb celular",
            )
        ]
        current.cache.last_refresh = time.time()
        decision = await current.agent.respond("Tem iPhone 13?")
        print(decision.reply)
        print(f"handoff={decision.handoff}")
    finally:
        await current.aclose()


def cli() -> None:
    if os.getenv("PORT"):
        uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
    else:
        asyncio.run(offline_smoke())
