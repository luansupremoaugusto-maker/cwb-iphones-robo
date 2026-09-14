from __future__ import annotations

import csv
import hashlib
import hmac
import io
from typing import Any

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
)


def build_admin_csrf_token(settings: Settings) -> str:
    if not settings.admin_panel_configured:
        raise ValueError("Painel administrativo não configurado")
    message = f"admin:{settings.admin_username.strip()}".encode("utf-8")
    return hmac.new(
        settings.admin_csrf_secret.strip().encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def _public_item(item: Any) -> dict[str, Any]:
    source = item if isinstance(item, dict) else {}
    result: dict[str, Any] = {}
    for field in _PUBLIC_ITEM_FIELDS:
        value = source.get(field)
        if isinstance(value, list):
            value = list(value)
        result[field] = value
    return result


def public_catalog_payload(
    result: dict[str, Any],
    mercado_refresh: float | None,
    sheets_refresh: float | None,
    generated_at: str,
) -> dict[str, Any]:
    sections = {
        section: [_public_item(item) for item in result.get(section, [])]
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


def catalog_csv_bytes(payload: dict[str, Any]) -> bytes:
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
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, delimiter=";", lineterminator="\r\n")
    writer.writeheader()
    for section, label in CATALOG_SECTION_LABELS.items():
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
                }
            )
    return output.getvalue().encode("utf-8-sig")


class AdminCommandService:
    """Apply the allowlisted conversation commands used by admin surfaces."""

    def __init__(self, repository: Repository):
        self.repository = repository

    def execute(
        self,
        action: str,
        *,
        operator: str,
        channel: str,
        phone: str | None = None,
    ) -> dict[str, Any]:
        action = str(action or "").strip().lower()
        if action != "release_all" and action not in _INDIVIDUAL_ACTIONS:
            raise ValueError("Ação administrativa desconhecida")

        if action == "release_all":
            if phone:
                raise ValueError("Liberar todos não aceita telefone")
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
            self.repository.audit(
                "admin_command",
                None,
                {
                    "action": action,
                    "channel": channel,
                    "operator": operator,
                    "released_count": released_count,
                },
            )
            return result

        normalized_phone = normalize_phone(phone)
        if not 10 <= len(normalized_phone) <= 15:
            raise ValueError("Telefone inválido")
        status = _INDIVIDUAL_ACTIONS[action]
        self.repository.set_conversation_status(
            normalized_phone,
            status,
            f"Comando {action} via {channel} por {operator}",
        )
        self.repository.audit(
            "admin_command",
            normalized_phone,
            {
                "action": action,
                "channel": channel,
                "operator": operator,
                "released_count": 0,
            },
        )
        return {
            "action": action,
            "phone": normalized_phone,
            "status": status,
            "released_count": 0,
            "message": f"Conversa {normalized_phone}: status alterado para {status}.",
        }
