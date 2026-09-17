from __future__ import annotations

import csv
import hashlib
import hmac
import html
import io
import unicodedata
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import arabic_reshaper
from pydantic import BaseModel, Field
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

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

_CATALOG_SECTION_SLUGS = {
    "seminovos": "seminovos",
    "lacrados_pronta_entrega": "lacrados-pronta-entrega",
    "lacrados": "lacrados-por-encomenda",
}

_CATALOG_PDF_FALLBACK_FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(_CATALOG_PDF_FALLBACK_FONT))


class CatalogPdfFontUnavailable(RuntimeError):
    """Raised when a catalog character needs a font missing from the runtime."""


def _register_catalog_pdf_font(
    name: str,
    candidates: Iterable[str],
    fallback: str | None,
) -> str | None:
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, str(path)))
        except (OSError, TTFError, ValueError):
            continue
        return name
    return fallback


_NOTO_FONT_DIRS = (
    Path(__file__).resolve().parent / "fonts",
    Path("/usr/share/fonts/truetype/noto"),
)


def _noto_font_paths(filename: str) -> tuple[str, ...]:
    return tuple(str(directory / filename) for directory in _NOTO_FONT_DIRS)


_CATALOG_PDF_FONTS = {
    "default": _register_catalog_pdf_font(
        "CatalogNotoSans",
        _noto_font_paths("NotoSans-Regular.ttf"),
        _CATALOG_PDF_FALLBACK_FONT,
    ),
    "arabic": _register_catalog_pdf_font(
        "CatalogNotoSansArabic",
        _noto_font_paths("NotoSansArabic-Regular.ttf"),
        None,
    ),
    "devanagari": _register_catalog_pdf_font(
        "CatalogNotoSansDevanagari",
        _noto_font_paths("NotoSansDevanagari-Regular.ttf"),
        None,
    ),
    "symbols": _register_catalog_pdf_font(
        "CatalogNotoSansSymbols",
        _noto_font_paths("NotoSansSymbols2-Regular.ttf"),
        None,
    ),
}

_CATALOG_PDF_FONT = _CATALOG_PDF_FONTS["default"]


def _catalog_pdf_font_for_char(character: str) -> str:
    codepoint = ord(character)
    if (
        0x0600 <= codepoint <= 0x08FF
        or 0xFB1D <= codepoint <= 0xFDFF
        or 0xFE70 <= codepoint <= 0xFEFF
    ):
        font = _CATALOG_PDF_FONTS["arabic"]
        if font is None:
            raise CatalogPdfFontUnavailable("Noto Sans Arabic is required for catalog PDF export")
        return font
    if 0x0900 <= codepoint <= 0x097F:
        font = _CATALOG_PDF_FONTS["devanagari"]
        if font is None:
            raise CatalogPdfFontUnavailable("Noto Sans Devanagari is required for catalog PDF export")
        return font
    if (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2000 <= codepoint <= 0x206F
        or 0x2100 <= codepoint <= 0x27BF
    ):
        font = _CATALOG_PDF_FONTS["symbols"]
        if font is None:
            raise CatalogPdfFontUnavailable("Noto Sans Symbols is required for catalog PDF export")
        return font
    if 0x2E80 <= codepoint <= 0x9FFF:
        return _CATALOG_PDF_FALLBACK_FONT
    return _CATALOG_PDF_FONT

CATALOG_EXPORT_COLUMNS = (
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
)

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


class AdminRecoveryDraftRequest(BaseModel):
    phone: str | None = None


class AdminRecoverySendRequest(BaseModel):
    phone: str | None = None
    message: str = Field(min_length=1, max_length=4000)
    expected_last_message_id: int = Field(ge=1)


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


def _catalog_export_row(label: str, item: dict[str, Any]) -> dict[str, Any]:
    item_colors = item.get("cores") or []
    if not item_colors and item.get("cor"):
        item_colors = [item["cor"]]
    prices = item.get("precos_brl") or []
    battery = item.get("saude_bateria")
    battery_text = "" if battery is None else str(battery)
    if battery_text and not battery_text.endswith("%"):
        battery_text += "%"
    return {
        "Categoria": label,
        "Produto": item.get("nome") or "",
        "Capacidade": item.get("capacidade") or "",
        "Condição": item.get("condicao") or "",
        "Cor(es)": ", ".join(str(color) for color in item_colors),
        "Preço(s)": " | ".join(_format_brl(price) for price in prices),
        "Quantidade": "" if item.get("quantidade") is None else item.get("quantidade"),
        "Saúde da bateria": battery_text,
        "Fotos disponíveis": item.get("fotos_disponiveis") or 0,
        "Disponibilidade": item.get("disponibilidade") or "",
    }


def _selected_catalog_sections(sections: Iterable[str] | None) -> tuple[str, ...]:
    if sections is None:
        return tuple(CATALOG_SECTION_LABELS)
    requested = {str(section).strip().lower() for section in sections if str(section).strip()}
    invalid = sorted(requested - set(CATALOG_SECTION_LABELS))
    if invalid:
        raise ValueError(f"Categoria(s) de catálogo inválida(s): {', '.join(invalid)}")
    return tuple(section for section in CATALOG_SECTION_LABELS if section in requested)


def normalize_catalog_sections(value: str | None) -> tuple[str, ...] | None:
    """Parse the comma-separated category filter used by CSV/PDF endpoints."""
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
        return f"catalogo-{_CATALOG_SECTION_SLUGS[selected[0]]}.csv"
    return "catalogo-selecionado.csv"


def catalog_csv_bytes(payload: dict[str, Any], *, sections: Iterable[str] | None = None) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=CATALOG_EXPORT_COLUMNS,
        delimiter=";",
        lineterminator="\r\n",
    )
    writer.writeheader()
    for section in _selected_catalog_sections(sections):
        label = CATALOG_SECTION_LABELS[section]
        for item in payload.get(section, []):
            writer.writerow(_catalog_export_row(label, item))
    return output.getvalue().encode("utf-8-sig")


def catalog_pdf_filename(sections: Iterable[str] | None) -> str:
    selected = _selected_catalog_sections(sections)
    if selected == tuple(CATALOG_SECTION_LABELS):
        return "catalogo-disponiveis.pdf"
    if len(selected) == 1:
        return f"catalogo-{_CATALOG_SECTION_SLUGS[selected[0]]}.pdf"
    return "catalogo-selecionado.pdf"


def _catalog_pdf_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if (
        _CATALOG_PDF_FONTS["arabic"] is not None
        and any(unicodedata.bidirectional(char) in {"R", "AL"} for char in text)
    ):
        text = get_display(arabic_reshaper.reshape(text))
    return text


def _catalog_pdf_markup(value: Any) -> str:
    text = _catalog_pdf_text(value)
    if not text:
        return "-"
    spans: list[str] = []
    current_font: str | None = None
    current_text: list[str] = []

    def flush() -> None:
        if not current_text or current_font is None:
            return
        escaped = html.escape("".join(current_text))
        if current_font == _CATALOG_PDF_FONT:
            spans.append(escaped)
        else:
            spans.append(f'<font name="{current_font}">{escaped}</font>')

    for character in text:
        font = _catalog_pdf_font_for_char(character)
        if unicodedata.combining(character) and current_font is not None:
            font = current_font
        if font != current_font:
            flush()
            current_text.clear()
            current_font = font
        current_text.append(character)
    flush()
    return "".join(spans)


def catalog_pdf_bytes(payload: dict[str, Any], *, sections: Iterable[str] | None = None) -> bytes:
    selected_sections = _selected_catalog_sections(sections)
    output = io.BytesIO()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CatalogPdfTitle",
        parent=styles["Title"],
        fontName=_CATALOG_PDF_FONT,
        fontSize=16,
        leading=19,
        spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "CatalogPdfMeta",
        parent=styles["BodyText"],
        fontName=_CATALOG_PDF_FONT,
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#667085"),
        spaceAfter=8,
    )
    header_style = ParagraphStyle(
        "CatalogPdfHeader",
        parent=styles["BodyText"],
        fontName=_CATALOG_PDF_FONT,
        fontSize=7,
        leading=8,
        textColor=colors.white,
    )
    cell_style = ParagraphStyle(
        "CatalogPdfCell",
        parent=styles["BodyText"],
        fontName=_CATALOG_PDF_FONT,
        fontSize=7,
        leading=8,
        spaceAfter=0,
    )

    def cell(value: Any) -> Paragraph:
        return Paragraph(_catalog_pdf_markup(value), cell_style)

    category_text = ", ".join(CATALOG_SECTION_LABELS[section] for section in selected_sections)
    story: list[Any] = [
        Paragraph(_catalog_pdf_markup("Catálogo de disponíveis"), title_style),
        Paragraph(_catalog_pdf_markup(f"Categorias exportadas: {category_text}"), meta_style),
    ]
    table_data: list[list[Any]] = [[
        Paragraph(_catalog_pdf_markup(column), header_style) for column in CATALOG_EXPORT_COLUMNS
    ]]
    for section in selected_sections:
        label = CATALOG_SECTION_LABELS[section]
        for item in payload.get(section, []):
            row = _catalog_export_row(label, item)
            table_data.append([cell(row[column]) for column in CATALOG_EXPORT_COLUMNS])

    if len(table_data) == 1:
        story.append(Paragraph("Nenhum aparelho encontrado nas categorias selecionadas.", cell_style))
    else:
        table = Table(
            table_data,
            colWidths=[90, 100, 60, 80, 85, 90, 55, 70, 65, 80],
            repeatRows=1,
            hAlign="LEFT",
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2457d6")),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#d0d5dd")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ]))
        story.append(table)

    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title="Catálogo de disponíveis",
        author="CWB.IPHONES",
    )
    document.build(story)
    return output.getvalue()


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
            "last_message_id": record.get("last_message_id"),
            "last_message": record.get("last_message") or "",
            "last_message_direction": record.get("last_message_direction"),
            "last_message_at": _iso_datetime(record.get("last_message_at")),
        }
        for record in records
    ]


def admin_recovery_payload(
    records: list[dict[str, Any]],
    *,
    total: int,
    offset: int,
    limit: int,
    older_than_hours: float,
    generated_at: str,
) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "total": int(total),
        "offset": int(offset),
        "limit": int(limit),
        "older_than_hours": float(older_than_hours),
        "has_more": int(offset) + len(records) < int(total),
        "items": admin_conversations_payload(records),
    }


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
