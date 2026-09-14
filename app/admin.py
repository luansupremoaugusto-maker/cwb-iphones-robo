from __future__ import annotations

import csv
import hashlib
import hmac
import io
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.config import Settings, normalize_phone
from app.storage.database import Repository


_INDIVIDUAL_ACTIONS = {
    "assume": "human_active",
    "resume": "bot_active",
    "close": "closed",
}

CATALOG_SECTION_LABELS = {
    "seminovos": "Seminovos",
    "lacrados_pronta_entrega": "Lacrados para pronta entrega",
    "lacrados": "Lacrados por encomenda",
}

CONVERSATION_STATUS_LABELS = {
    "bot_active": "Robô ativo",
    "human_pending": "Aguardando atendimento",
    "human_active": "Em atendimento humano",
    "closed": "Encerrada",
}

_PUBLIC_ITEM_FIELDS = (
    "nome",
    "capacidade",
    "condicao",
    "quantidade",
    "precos_brl",
    "cores",
    "cor",
    "saude_bateria",
    "fotos_disponiveis",
    "disponibilidade",
)


class AdminCommandRequest(BaseModel):
    action: str | None = None
    phone: str | None = None
    justification: str | None = Field(default=None, max_length=250)


class AdminCommandPreviewRequest(BaseModel):
    action: str | None = None
    phone: str | None = None


class AdminControlRequest(BaseModel):
    action: str | None = None
    reason: str | None = Field(default=None, max_length=255)
    justification: str | None = Field(default=None, max_length=250)


class AdminRefreshRequest(BaseModel):
    source: str = "all"
    justification: str | None = Field(default=None, max_length=250)


def build_admin_csrf_token(settings: Settings) -> str:
    if not settings.admin_panel_configured:
        raise ValueError("Painel administrativo não configurado")
    message = f"admin:{settings.admin_username.strip()}".encode("utf-8")
    return hmac.new(
        settings.admin_csrf_secret.strip().encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def _public_item(item: Any, *, section: str) -> dict[str, Any]:
    source = item if isinstance(item, dict) else {}
    result: dict[str, Any] = {}
    for field in _PUBLIC_ITEM_FIELDS:
        value = source.get(field)
        if isinstance(value, list):
            value = list(value)
        result[field] = value
    quantity = source.get("quantidade")
    if section == "lacrados":
        result["disponibilidade"] = "Por encomenda"
    elif isinstance(quantity, (int, float)):
        result["disponibilidade"] = "Em estoque" if quantity > 0 else "Sem estoque"
    else:
        result["disponibilidade"] = "Não informado"
    return result


def public_catalog_payload(
    result: dict[str, Any],
    mercado_refresh: float | None,
    sheets_refresh: float | None,
    generated_at: str,
) -> dict[str, Any]:
    sections = {
        section: [_public_item(item, section=section) for item in result.get(section, [])]
        for section in CATALOG_SECTION_LABELS
    }
    return {
        **sections,
        "total_modelos": sum(len(items) for items in sections.values()),
        "generated_at": generated_at,
        "sources": {
            "mercado_phone_last_refresh": mercado_refresh,
            "google_sheets_last_refresh": sheets_refresh,
        },
    }


def _format_brl(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "")
    formatted = f"{number:,.2f}"
    return f"R$ {formatted.replace(',', '_').replace('.', ',').replace('_', '.')}"


def _selected_catalog_sections(sections: Iterable[str] | None) -> tuple[str, ...]:
    if sections is None:
        return tuple(CATALOG_SECTION_LABELS)
    requested = {str(section).strip().lower() for section in sections if str(section).strip()}
    invalid = sorted(requested - set(CATALOG_SECTION_LABELS))
    if invalid:
        raise ValueError(f"Categoria(s) de catálogo inválida(s): {', '.join(invalid)}")
    return tuple(section for section in CATALOG_SECTION_LABELS if section in requested)


def normalize_catalog_sections(value: str | None) -> tuple[str, ...] | None:
    """Parse the comma-separated category filter used by the CSV endpoint."""
    if value is None:
        return None
    parts = [part.strip().lower() for part in value.split(",") if part.strip()]
    if not parts:
        raise ValueError("Selecione ao menos uma categoria para exportar")
    return _selected_catalog_sections(parts)


def catalog_csv_filename(sections: Iterable[str] | None) -> str:
    selected = _selected_catalog_sections(sections)
    if selected == tuple(CATALOG_SECTION_LABELS):
        return "catalogo-disponiveis.csv"
    if len(selected) == 1:
        slugs = {
            "seminovos": "seminovos",
            "lacrados_pronta_entrega": "lacrados-pronta-entrega",
            "lacrados": "lacrados-por-encomenda",
        }
        return f"catalogo-{slugs[selected[0]]}.csv"
    return "catalogo-selecionado.csv"


def catalog_csv_bytes(payload: dict[str, Any], *, sections: Iterable[str] | None = None) -> bytes:
    columns = [
        "Categoria",
        "Produto",
        "Capacidade",
        "Condição",
        "Cor(es)",
        "Preço(s)",
        "Quantidade",
        "Saúde da bateria",
        "Fotos disponíveis",
        "Disponibilidade",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, delimiter=";", lineterminator="\r\n")
    writer.writeheader()
    for section in _selected_catalog_sections(sections):
        label = CATALOG_SECTION_LABELS[section]
        for item in payload.get(section, []):
            colors = item.get("cores") or []
            if not colors and item.get("cor"):
                colors = [item["cor"]]
            prices = item.get("precos_brl") or []
            battery = item.get("saude_bateria")
            battery_text = "" if battery is None else str(battery)
            if battery_text and not battery_text.endswith("%"):
                battery_text += "%"
            writer.writerow(
                {
                    "Categoria": label,
                    "Produto": item.get("nome") or "",
                    "Capacidade": item.get("capacidade") or "",
                    "Condição": item.get("condicao") or "",
                    "Cor(es)": ", ".join(str(color) for color in colors),
                    "Preço(s)": " | ".join(_format_brl(price) for price in prices),
                    "Quantidade": "" if item.get("quantidade") is None else item.get("quantidade"),
                    "Saúde da bateria": battery_text,
                    "Fotos disponíveis": item.get("fotos_disponiveis") or 0,
                    "Disponibilidade": item.get("disponibilidade") or "",
                }
            )
    return output.getvalue().encode("utf-8-sig")


def _iso_datetime(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


def admin_dashboard_payload(
    counts: dict[str, int],
    sources: dict[str, dict[str, Any]],
    generated_at: str,
    *,
    monitoring: dict[str, Any] | None = None,
    control: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conversations = {
        "total": int(counts.get("total", 0)),
        "bot_active": int(counts.get("bot_active", 0)),
        "human_pending": int(counts.get("human_pending", 0)),
        "human_active": int(counts.get("human_active", 0)),
        "human_total": int(counts.get("human_pending", 0)) + int(counts.get("human_active", 0)),
        "closed": int(counts.get("closed", 0)),
    }
    return {
        "generated_at": generated_at,
        "conversations": conversations,
        "sources": sources,
        "monitoring": monitoring or {},
        "control": control or {},
    }


def admin_conversations_payload(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "phone": record.get("phone"),
            "chat_name": record.get("chat_name"),
            "status": record.get("status"),
            "status_label": CONVERSATION_STATUS_LABELS.get(
                str(record.get("status") or ""), str(record.get("status") or "")
            ),
            "paused_reason": record.get("paused_reason"),
            "updated_at": _iso_datetime(record.get("updated_at")),
            "last_message": record.get("last_message") or "",
            "last_message_direction": record.get("last_message_direction"),
            "last_message_at": _iso_datetime(record.get("last_message_at")),
        }
        for record in records
    ]


def admin_audit_payload(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": int(record.get("id") or 0),
            "event_type": record.get("event_type") or "",
            "subject": record.get("subject"),
            "detail": dict(record.get("detail") or {}),
            "created_at": _iso_datetime(record.get("created_at")),
        }
        for record in records
    ]


def admin_sessions_payload(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "session_id": record.get("session_id"),
            "operator": record.get("operator"),
            "created_at": _iso_datetime(record.get("created_at")),
            "last_seen_at": _iso_datetime(record.get("last_seen_at")),
            "expires_at": _iso_datetime(record.get("expires_at")),
        }
        for record in records
    ]


def admin_role_allows(role: str, action: str) -> bool:
    normalized_role = str(role or "operator").strip().lower()
    normalized_action = str(action or "").strip().lower()
    return normalized_role == "owner" or normalized_action != "release_all"


class AdminCommandService:
    """Apply the allowlisted conversation commands used by admin surfaces."""

    def __init__(self, repository: Repository):
        self.repository = repository

    @staticmethod
    def _normalize_action(action: str) -> str:
        normalized = str(action or "").strip().lower()
        if normalized != "release_all" and normalized not in _INDIVIDUAL_ACTIONS:
            raise ValueError("Ação administrativa desconhecida")
        return normalized

    @staticmethod
    def _normalize_phone(action: str, phone: str | None) -> str | None:
        if action == "release_all":
            if phone:
                raise ValueError("Liberar todos não aceita telefone")
            return None
        normalized_phone = normalize_phone(phone)
        if not 10 <= len(normalized_phone) <= 15:
            raise ValueError("Telefone inválido")
        return normalized_phone

    def preview(self, action: str, *, phone: str | None = None) -> dict[str, Any]:
        normalized_action = self._normalize_action(action)
        normalized_phone = self._normalize_phone(normalized_action, phone)
        if normalized_action == "release_all":
            counts = self.repository.conversation_status_counts()
            affected_count = int(counts.get("human_pending", 0)) + int(counts.get("human_active", 0))
            return {
                "action": normalized_action,
                "phone": None,
                "current_status": None,
                "target_status": "bot_active",
                "affected_count": int(affected_count),
                "message": (
                    f"{affected_count} conversa(s) em atendimento humano "
                    "serão liberadas para o robô."
                ),
            }

        current = self.repository.get_conversation(normalized_phone or "")
        target_status = _INDIVIDUAL_ACTIONS[normalized_action]
        current_status = current.status if current is not None else None
        return {
            "action": normalized_action,
            "phone": normalized_phone,
            "current_status": current_status,
            "target_status": target_status,
            "affected_count": 1,
            "message": (
                f"A conversa {normalized_phone} será alterada para {target_status}."
                if current is not None
                else f"A conversa {normalized_phone} será criada como {target_status}."
            ),
        }

    def execute(
        self,
        action: str,
        *,
        operator: str,
        channel: str,
        phone: str | None = None,
        justification: str | None = None,
    ) -> dict[str, Any]:
        action = self._normalize_action(action)
        normalized_justification = str(justification or "").strip()[:250] or None

        if action == "release_all":
            self._normalize_phone(action, phone)
            released_count = self.repository.release_all_human_conversations(
                f"Comando {action} via {channel} por {operator}"
            )
            result = {
                "action": action,
                "phone": None,
                "status": "bot_active",
                "released_count": released_count,
                "message": (
                    f"{released_count} conversa(s) em atendimento humano "
                    "foram liberadas para o robô."
                ),
            }
            detail = {
                "action": action,
                "channel": channel,
                "operator": operator,
                "released_count": released_count,
            }
            if normalized_justification:
                detail["justification"] = normalized_justification
            self.repository.audit("admin_command", None, detail)
            return result

        normalized_phone = self._normalize_phone(action, phone)
        assert normalized_phone is not None
        status = _INDIVIDUAL_ACTIONS[action]
        self.repository.set_conversation_status(
            normalized_phone,
            status,
            f"Comando {action} via {channel} por {operator}",
        )
        detail = {
            "action": action,
            "channel": channel,
            "operator": operator,
            "released_count": 0,
        }
        if normalized_justification:
            detail["justification"] = normalized_justification
        self.repository.audit("admin_command", normalized_phone, detail)
        return {
            "action": action,
            "phone": normalized_phone,
            "status": status,
            "released_count": 0,
            "message": f"Conversa {normalized_phone}: status alterado para {status}.",
        }
