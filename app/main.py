from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.admin import (
    AdminCommandRequest,
    AdminCommandService,
    build_admin_csrf_token,
    catalog_csv_bytes,
    public_catalog_payload,
)
from app.adapters.zapi import normalize_received_callback
from app.config import get_settings
from app.runtime import Runtime, build_runtime


CONTROL_CALLBACK_MARKERS = ("delivery", "status", "disconnect", "connection")
logger = logging.getLogger(__name__)


def _parse_basic_authorization(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    scheme, separator, encoded = value.partition(" ")
    if not separator or scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    username, separator, password = decoded.partition(":")
    if not separator:
        return None
    return username, password


def _require_admin_operator(request: Request) -> str:
    current: Runtime = request.app.state.runtime
    settings = current.settings
    if not settings.admin_panel_configured:
        raise HTTPException(status_code=404, detail="Not found")
    credentials = _parse_basic_authorization(request.headers.get("authorization"))
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Autenticação administrativa necessária",
            headers={"WWW-Authenticate": 'Basic realm="admin"'},
        )
    username, password = credentials
    if not (
        secrets.compare_digest(username, settings.admin_username.strip())
        and secrets.compare_digest(password, settings.admin_password)
    ):
        raise HTTPException(
            status_code=401,
            detail="Credenciais administrativas inválidas",
            headers={"WWW-Authenticate": 'Basic realm="admin"'},
        )
    return username


def _require_admin_csrf(request: Request, settings: Any) -> None:
    expected = build_admin_csrf_token(settings)
    provided = request.headers.get("x-admin-csrf", "")
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Token CSRF inválido")


async def _admin_catalog_payload(current: Runtime) -> dict[str, Any]:
    result = await current.cache.list_available_products()
    return public_catalog_payload(
        result,
        mercado_refresh=getattr(current.cache, "last_refresh", None),
        sheets_refresh=getattr(current.google_sheets, "last_refresh", None),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


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

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request) -> HTMLResponse:
        _require_admin_operator(request)
        return HTMLResponse(
            "<!doctype html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\"><title>Administração</title>"
            "</head><body><h1>Administração do robô</h1></body></html>"
        )

    @app.get("/admin/api/catalog")
    async def admin_catalog(request: Request) -> dict[str, Any]:
        _require_admin_operator(request)
        current: Runtime = request.app.state.runtime
        try:
            return await _admin_catalog_payload(current)
        except Exception as exc:
            logger.exception("admin catalog refresh failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Catálogo temporariamente indisponível") from exc

    @app.get("/admin/api/catalog.csv")
    async def admin_catalog_csv(request: Request) -> Response:
        _require_admin_operator(request)
        current: Runtime = request.app.state.runtime
        try:
            payload = await _admin_catalog_payload(current)
            data = catalog_csv_bytes(payload)
        except Exception as exc:
            logger.exception("admin catalog CSV export failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Catálogo temporariamente indisponível") from exc
        return Response(
            content=data,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="catalogo-disponiveis.csv"'},
        )

    @app.post("/admin/api/commands")
    async def admin_command(request: Request, command: AdminCommandRequest) -> dict[str, Any]:
        operator = _require_admin_operator(request)
        current: Runtime = request.app.state.runtime
        _require_admin_csrf(request, current.settings)
        service = getattr(current.processor, "admin_commands", None)
        if service is None:
            service = AdminCommandService(current.repository)
        try:
            return service.execute(
                command.action or "",
                operator=operator,
                channel="web",
                phone=command.phone,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("admin command failed: %s", type(exc).__name__)
            raise HTTPException(status_code=500, detail="Não foi possível executar o comando") from exc

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
