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
    AdminCommandPreviewRequest,
    AdminControlRequest,
    AdminRefreshRequest,
    AdminCommandService,
    admin_audit_payload,
    admin_conversations_payload,
    admin_dashboard_payload,
    admin_role_allows,
    admin_sessions_payload,
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
_ADMIN_NO_STORE_HEADERS = {"Cache-Control": "no-store"}


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


def _admin_session_token(username: str, settings: Any, repository: Any | None = None) -> str:
    expires_at = str(int(time.time()) + ADMIN_SESSION_MAX_AGE)
    session_id = secrets.token_urlsafe(24)
    payload = f"{username}|{expires_at}|{session_id}"
    signature = hmac.new(
        settings.admin_csrf_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    encoded = base64.urlsafe_b64encode(f"{payload}|{signature}".encode("utf-8"))
    if repository is not None:
        repository.register_admin_session(
            session_id,
            username,
            repository.session_expiry(ADMIN_SESSION_MAX_AGE),
        )
    return encoded.decode("ascii").rstrip("=")


def _admin_session_operator(value: str | None, settings: Any, repository: Any | None = None) -> str | None:
    if not value:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8")
        username, expires_at, session_id, signature = decoded.split("|", 3)
        if int(expires_at) < int(time.time()):
            return None
    except (ValueError, UnicodeDecodeError):
        return None
    payload = f"{username}|{expires_at}|{session_id}"
    expected = hmac.new(
        settings.admin_csrf_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not secrets.compare_digest(signature, expected):
        return None
    if not secrets.compare_digest(username, settings.admin_username.strip()):
        return None
    if repository is not None and not repository.touch_admin_session(session_id):
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
    repository = getattr(getattr(request.app.state, "runtime", None), "repository", None)
    return _admin_session_operator(request.cookies.get(ADMIN_SESSION_COOKIE), settings, repository)


def _require_admin_operator(request: Request) -> str:
    current: Runtime = request.app.state.runtime
    settings = current.settings
    if not settings.admin_panel_configured:
        raise HTTPException(status_code=404, detail="Not found")
    operator = _admin_operator(request, settings)
    if operator is None:
        raise _admin_unauthorized()
    return operator


def _require_admin_owner(request: Request) -> str:
    operator = _require_admin_operator(request)
    current: Runtime = request.app.state.runtime
    if str(current.settings.admin_role).strip().lower() != "owner":
        raise HTTPException(status_code=403, detail="Esta ação exige o perfil proprietário")
    return operator


def _require_justification(value: str | None) -> str:
    justification = str(value or "").strip()
    if not justification:
        raise HTTPException(status_code=400, detail="Informe uma justificativa para a ação")
    return justification[:250]


def _require_admin_csrf(request: Request, settings: Any) -> None:
    expected = build_admin_csrf_token(settings)
    provided = request.headers.get("x-admin-csrf", "")
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="Token CSRF inválido")


async def _admin_catalog_payload(current: Runtime) -> dict[str, Any]:
    result = await current.cache.list_available_products(include_photos=True)
    return public_catalog_payload(
        result,
        mercado_refresh=getattr(current.cache, "last_refresh", None),
        sheets_refresh=getattr(current.google_sheets, "last_refresh", None),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def _admin_json(payload: Any) -> JSONResponse:
    return JSONResponse(payload, headers=_ADMIN_NO_STORE_HEADERS)


def _admin_source_health(current: Runtime) -> dict[str, dict[str, Any]]:
    now = time.time()
    mercado_refresh = getattr(current.cache, "last_refresh", None)
    sheets_refresh = getattr(current.google_sheets, "last_refresh", None)
    mercado_configured = bool(current.settings.mercado_phone_api_key)
    sheets_configured = bool(getattr(current.google_sheets, "configured", True))

    def freshness(last_refresh: Any, configured: bool, threshold: int) -> bool:
        if not configured or not getattr(current, "settings", None):
            return False
        try:
            return not last_refresh or now - float(last_refresh) > max(1, int(threshold))
        except (TypeError, ValueError):
            return True

    return {
        "database": {"ok": current.repository.healthcheck()},
        "mercado_phone": {
            "ok": mercado_configured and _source_has_snapshot(current.cache),
            "configured": mercado_configured,
            "stale": freshness(
                mercado_refresh,
                mercado_configured,
                max(
                    current.settings.mercado_cache_ttl_seconds,
                    current.settings.mercado_refresh_interval_seconds * 2,
                ),
            ),
            "last_error": bool(getattr(current.cache, "last_error", None)),
            "last_refresh": mercado_refresh,
            "items": len(getattr(current.cache, "items", None) or []),
        },
        "google_sheets": {
            "ok": _source_has_snapshot(current.google_sheets),
            "configured": sheets_configured,
            "stale": freshness(
                sheets_refresh,
                sheets_configured and bool(getattr(current.google_sheets, "enabled", True)),
                max(
                    current.settings.google_sheets_cache_ttl_seconds,
                    current.settings.google_sheets_refresh_interval_seconds * 2,
                ),
            ),
            "last_error": bool(getattr(current.google_sheets, "last_error", None)),
            "last_refresh": sheets_refresh,
            "items": len(getattr(current.google_sheets, "items", None) or []),
        },
        "zapi": {
            "ok": bool(current.settings.zapi_instance_id and current.settings.zapi_token),
        },
        "openai": {"ok": bool(current.settings.openai_api_key)},
    }


def _admin_monitoring(current: Runtime, sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    errors = current.repository.audit_error_summary(hours=24)
    last_event = errors.get("last_event")
    if isinstance(last_event, dict):
        last_event = {
            "event_type": last_event.get("event_type"),
            "created_at": (
                last_event.get("created_at").isoformat()
                if isinstance(last_event.get("created_at"), datetime)
                else last_event.get("created_at")
            ),
        }
    return {
        "stale_sources": [
            source
            for source in ("mercado_phone", "google_sheets")
            if sources.get(source, {}).get("stale")
        ],
        "source_errors": [
            source
            for source in ("mercado_phone", "google_sheets")
            if sources.get(source, {}).get("last_error")
        ],
        "recent_errors": {
            "window_hours": errors.get("window_hours", 24),
            "count": errors.get("count", 0),
            "by_type": errors.get("by_type", {}),
            "last_event": last_event,
        },
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
            value=_admin_session_token(username, settings, current.repository),
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
        return HTMLResponse(
            render_admin_page(build_admin_csrf_token(settings)),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/admin/api/dashboard")
    async def admin_dashboard(request: Request) -> dict[str, Any]:
        _require_admin_operator(request)
        current: Runtime = request.app.state.runtime
        sources = _admin_source_health(current)
        payload = admin_dashboard_payload(
            current.repository.conversation_status_counts(),
            sources,
            datetime.now(timezone.utc).isoformat(),
            monitoring=_admin_monitoring(current, sources),
            control=current.repository.get_bot_control_state(),
        )
        payload["role"] = str(current.settings.admin_role)
        payload["permissions"] = {
            "owner_controls": str(current.settings.admin_role).strip().lower() == "owner",
            "individual_commands": True,
        }
        return _admin_json(
            payload
        )

    @app.get("/admin/api/control")
    async def admin_control_get(request: Request) -> dict[str, Any]:
        _require_admin_operator(request)
        current: Runtime = request.app.state.runtime
        return _admin_json(
            {
                "state": current.repository.get_bot_control_state(),
                "role": str(current.settings.admin_role),
                "permissions": {
                    "owner_controls": str(current.settings.admin_role).strip().lower() == "owner",
                    "individual_commands": True,
                },
            }
        )

    @app.get("/admin/api/sessions")
    async def admin_sessions(request: Request) -> dict[str, Any]:
        _require_admin_operator(request)
        current: Runtime = request.app.state.runtime
        return _admin_json(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "items": admin_sessions_payload(current.repository.list_active_admin_sessions()),
            }
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
        return _admin_json(
            {
                "status": status_filter,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "items": admin_conversations_payload(
                    current.repository.list_conversations(statuses, limit=limit)
                ),
            }
        )

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
        return _admin_json(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "items": admin_audit_payload(
                    current.repository.list_audit_events(limit=limit, event_type=normalized_event_type)
                ),
            }
        )

    @app.get("/admin/api/catalog")
    async def admin_catalog(request: Request) -> dict[str, Any]:
        _require_admin_operator(request)
        current: Runtime = request.app.state.runtime
        try:
            return _admin_json(await _admin_catalog_payload(current))
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
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": 'attachment; filename="catalogo-disponiveis.csv"',
            },
        )

    @app.post("/admin/api/commands")
    async def admin_command(request: Request, command: AdminCommandRequest) -> dict[str, Any]:
        operator = _require_admin_operator(request)
        current: Runtime = request.app.state.runtime
        _require_admin_csrf(request, current.settings)
        action = str(command.action or "").strip().lower()
        if not admin_role_allows(current.settings.admin_role, action):
            raise HTTPException(status_code=403, detail="Esta ação exige o perfil proprietário")
        justification = _require_justification(command.justification)
        service = getattr(current.processor, "admin_commands", None)
        if service is None:
            service = AdminCommandService(current.repository)
        try:
            return _admin_json(
                service.execute(
                    command.action or "",
                    operator=operator,
                    channel="web",
                    phone=command.phone,
                    justification=justification,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("admin command failed: %s", type(exc).__name__)
            raise HTTPException(status_code=500, detail="Não foi possível executar o comando") from exc

    @app.post("/admin/api/commands/preview")
    async def admin_command_preview(
        request: Request,
        command: AdminCommandPreviewRequest,
    ) -> dict[str, Any]:
        _require_admin_operator(request)
        current: Runtime = request.app.state.runtime
        _require_admin_csrf(request, current.settings)
        action = str(command.action or "").strip().lower()
        if not admin_role_allows(current.settings.admin_role, action):
            raise HTTPException(status_code=403, detail="Esta ação exige o perfil proprietário")
        service = getattr(current.processor, "admin_commands", None)
        if service is None:
            service = AdminCommandService(current.repository)
        try:
            return _admin_json(service.preview(action, phone=command.phone))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/admin/api/control")
    async def admin_control(
        request: Request,
        command: AdminControlRequest,
    ) -> Response:
        operator = _require_admin_owner(request)
        current: Runtime = request.app.state.runtime
        _require_admin_csrf(request, current.settings)
        action = str(command.action or "").strip().lower()
        justification = _require_justification(command.justification)
        state_modes = {
            "pause_bot": "paused",
            "resume_bot": "active",
            "maintenance_on": "maintenance",
            "maintenance_off": "active",
        }
        if action == "logout_sessions":
            revoked_count = current.repository.revoke_admin_sessions()
            current.repository.audit(
                "admin_sessions_revoked",
                None,
                {
                    "operator": operator,
                    "channel": "web",
                    "justification": justification,
                    "revoked_count": revoked_count,
                },
            )
            response = _admin_json(
                {
                    "action": action,
                    "revoked_count": revoked_count,
                    "message": f"{revoked_count} sessão(ões) administrativa(s) desconectada(s).",
                }
            )
            response.delete_cookie(ADMIN_SESSION_COOKIE, path="/admin")
            return response
        if action not in state_modes:
            raise HTTPException(status_code=400, detail="Ação de controle inválida")
        mode = state_modes[action]
        reason = str(command.reason or justification).strip()[:255]
        state = current.repository.set_bot_control_state(mode, reason, operator)
        current.repository.audit(
            "admin_control",
            None,
            {
                "action": action,
                "mode": mode,
                "operator": operator,
                "channel": "web",
                "justification": justification,
            },
        )
        return _admin_json(
            {
                "action": action,
                "state": state,
                "message": "Estado global do robô atualizado.",
            }
        )

    @app.post("/admin/api/monitoring/refresh")
    async def admin_monitoring_refresh(
        request: Request,
        command: AdminRefreshRequest,
    ) -> dict[str, Any]:
        operator = _require_admin_operator(request)
        current: Runtime = request.app.state.runtime
        _require_admin_csrf(request, current.settings)
        source = str(command.source or "").strip().lower()
        if source not in {"mercado_phone", "google_sheets", "all"}:
            raise HTTPException(status_code=400, detail="Fonte de atualização inválida")
        justification = _require_justification(command.justification)
        refreshed: dict[str, Any] = {}
        try:
            if source in {"mercado_phone", "all"}:
                if not current.settings.mercado_phone_api_key:
                    raise ValueError("Mercado Phone não está configurado")
                refreshed["mercado_phone"] = await current.cache.refresh(force=True)
            if source in {"google_sheets", "all"}:
                if not current.google_sheets.enabled:
                    raise ValueError("Google Sheets está desativado")
                refreshed["google_sheets"] = await current.google_sheets.refresh(force=True)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            current.repository.audit(
                "admin_source_refresh_error",
                source,
                {"operator": operator, "channel": "web", "error_type": type(exc).__name__},
            )
            logger.exception("admin source refresh failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Não foi possível atualizar a fonte") from exc
        current.repository.audit(
            "admin_source_refresh",
            source,
            {
                "operator": operator,
                "channel": "web",
                "justification": justification,
                "refreshed": refreshed,
            },
        )
        return _admin_json(
            {
                "source": source,
                "refreshed": refreshed,
                "message": "Fonte(s) atualizada(s) com sucesso.",
            }
        )

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
