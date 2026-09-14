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
from urllib.parse import parse_qs

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.admin import (
    AdminCommandRequest,
    AdminCommandService,
    admin_audit_payload,
    admin_conversations_payload,
    admin_dashboard_payload,
    build_admin_csrf_token,
    catalog_csv_bytes,
    public_catalog_payload,
)
from app.adapters.zapi import normalize_received_callback
from app.admin_page import render_admin_login_page, render_admin_page
from app.config import get_settings
from app.runtime import Runtime, build_runtime


CONTROL_CALLBACK_MARKERS = ("delivery", "status", "disconnect", "connection")
ADMIN_SESSION_COOKIE = "cwb_admin_session"
ADMIN_SESSION_MAX_AGE = 8 * 60 * 60
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


def _admin_unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Autenticação administrativa necessária",
        headers={"WWW-Authenticate": 'Basic realm="admin"'},
    )


def _admin_session_token(username: str, settings: Any) -> str:
    expires_at = str(int(time.time()) + ADMIN_SESSION_MAX_AGE)
    payload = f"{username}|{expires_at}"
    signature = hmac.new(
        settings.admin_csrf_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    encoded = base64.urlsafe_b64encode(f"{payload}|{signature}".encode("utf-8"))
    return encoded.decode("ascii").rstrip("=")


def _admin_session_operator(value: str | None, settings: Any) -> str | None:
    if not value:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8")
        username, expires_at, signature = decoded.split("|", 2)
        if int(expires_at) < int(time.time()):
            return None
    except (ValueError, UnicodeDecodeError):
        return None
    payload = f"{username}|{expires_at}"
    expected = hmac.new(
        settings.admin_csrf_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not secrets.compare_digest(signature, expected):
        return None
    if not secrets.compare_digest(username, settings.admin_username.strip()):
        return None
    return username


def _admin_operator(request: Request, settings: Any) -> str | None:
    credentials = _parse_basic_authorization(request.headers.get("authorization"))
    if credentials is not None:
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
    return _admin_session_operator(request.cookies.get(ADMIN_SESSION_COOKIE), settings)


def _require_admin_operator(request: Request) -> str:
    current: Runtime = request.app.state.runtime
    settings = current.settings
    if not settings.admin_panel_configured:
        raise HTTPException(status_code=404, detail="Not found")
    operator = _admin_operator(request, settings)
    if operator is None:
        raise _admin_unauthorized()
    return operator


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


def _admin_source_health(current: Runtime) -> dict[str, dict[str, Any]]:
    return {
        "database": {"ok": current.repository.healthcheck()},
        "mercado_phone": {
            "ok": _source_has_snapshot(current.cache),
            "last_refresh": getattr(current.cache, "last_refresh", None),
            "items": len(getattr(current.cache, "items", None) or []),
        },
        "google_sheets": {
            "ok": _source_has_snapshot(current.google_sheets),
            "last_refresh": getattr(current.google_sheets, "last_refresh", None),
            "items": len(getattr(current.google_sheets, "items", None) or []),
        },
        "zapi": {
            "ok": bool(current.settings.zapi_instance_id and current.settings.zapi_token),
        },
        "openai": {"ok": bool(current.settings.openai_api_key)},
    }


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

    @app.post("/admin/login", response_class=HTMLResponse)
    async def admin_login(request: Request) -> Response:
        current: Runtime = request.app.state.runtime
        settings = current.settings
        if not settings.admin_panel_configured:
            raise HTTPException(status_code=404, detail="Not found")
        fields = parse_qs((await request.body()).decode("utf-8", errors="replace"), keep_blank_values=True)
        username = fields.get("username", [""])[0].strip()
        password = fields.get("password", [""])[0]
        valid = secrets.compare_digest(username, settings.admin_username.strip()) and secrets.compare_digest(
            password, settings.admin_password
        )
        if not valid:
            return HTMLResponse(
                render_admin_login_page("Credenciais administrativas inválidas."),
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        response = Response(
            status_code=303,
            headers={"Location": "/admin", "Cache-Control": "no-store"},
        )
        response.set_cookie(
            key=ADMIN_SESSION_COOKIE,
            value=_admin_session_token(username, settings),
            max_age=ADMIN_SESSION_MAX_AGE,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/admin",
        )
        return response

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request) -> HTMLResponse:
        current: Runtime = request.app.state.runtime
        settings = current.settings
        if not settings.admin_panel_configured:
            raise HTTPException(status_code=404, detail="Not found")
        if _admin_operator(request, settings) is None:
            return HTMLResponse(
                render_admin_login_page(),
                headers={"Cache-Control": "no-store"},
            )
        return HTMLResponse(render_admin_page(build_admin_csrf_token(settings)))

    @app.get("/admin/api/dashboard")
    async def admin_dashboard(request: Request) -> dict[str, Any]:
        _require_admin_operator(request)
        current: Runtime = request.app.state.runtime
        return admin_dashboard_payload(
            current.repository.conversation_status_counts(),
            _admin_source_health(current),
            datetime.now(timezone.utc).isoformat(),
        )

    @app.get("/admin/api/conversations")
    async def admin_conversations(
        request: Request,
        status: str = "human",
        limit: int = 100,
    ) -> dict[str, Any]:
        _require_admin_operator(request)
        current: Runtime = request.app.state.runtime
        status_filter = str(status or "human").strip().lower()
        if status_filter == "human":
            statuses = ("human_pending", "human_active")
        elif status_filter == "all":
            statuses = None
        elif status_filter in {"bot_active", "human_pending", "human_active", "closed"}:
            statuses = (status_filter,)
        else:
            raise HTTPException(status_code=400, detail="Filtro de conversa inválido")
        return {
            "status": status_filter,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "items": admin_conversations_payload(current.repository.list_conversations(statuses, limit=limit)),
        }

    @app.get("/admin/api/audit")
    async def admin_audit(
        request: Request,
        limit: int = 50,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        _require_admin_operator(request)
        current: Runtime = request.app.state.runtime
        if limit < 1:
            raise HTTPException(status_code=400, detail="Limite de auditoria inválido")
        normalized_event_type = str(event_type or "").strip() or None
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "items": admin_audit_payload(
                current.repository.list_audit_events(limit=limit, event_type=normalized_event_type)
            ),
        }

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
