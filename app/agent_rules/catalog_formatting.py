"""Catalog formatting agent rules."""

from __future__ import annotations

from app.agent import (
    Any,
    _is_made_to_order_sealed_item,
    _is_sealed_item,
    _normalize,
    format_brl,
    re,
    sort_capacity_variants,
)

def _public_item(item: Any) -> dict[str, Any]:
    return {
        "nome": item.name,
        "categoria": item.category,
        "condicao": item.condition,
        "capacidade": item.capacity,
        "cor": getattr(item, "color", None) or item.colors,
        "cores": item.colors,
        "saude_bateria": getattr(item, "battery_health", None),
        "preco_brl": item.price_brl,
        "quantidade": item.quantity,
        "disponibilidade": item.availability,
        "fotos_disponiveis": len(getattr(item, "photo_urls", []) or []),
        "fonte_preco": "planilha BOT - novos lacrados"
        if item.source == "google_sheets"
        else "catálogo de estoque",
    }

def _format_battery(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "não informada no cadastro"
    shown = str(int(number)) if number.is_integer() else str(number).replace(".", ",")
    return f"{shown}%"

def _sort_product_availability_items(items: list[Any]) -> list[Any]:
    """Keep availability sections intact and order each model's capacities ascending."""
    seminovos = sort_capacity_variants(item for item in items if not _is_sealed_item(item))
    pronta_entrega = sort_capacity_variants(
        item
        for item in items
        if _is_sealed_item(item) and not _is_made_to_order_sealed_item(item)
    )
    por_encomenda = sort_capacity_variants(
        item for item in items if _is_made_to_order_sealed_item(item)
    )
    return [*seminovos, *pronta_entrega, *por_encomenda]

def _format_product_availability(
    items: list[Any],
    *,
    header: str | None = None,
    installment_budget: tuple[int, float] | None = None,
    installment_quotes: dict[int, dict[str, Any]] | None = None,
    entry_amount: float | None = None,
) -> str:
    if not items:
        return "No momento não localizei esse produto no catálogo."
    items = _sort_product_availability_items(items)

    model_names = [
        str(getattr(item, "name", None) or "").strip()
        for item in items
        if str(getattr(item, "name", None) or "").strip()
    ]
    normalized_model_names = {_normalize(name) for name in model_names}
    if len(normalized_model_names) == 1 and model_names:
        model = model_names[0]
    elif model_names and all("iphone" in name for name in normalized_model_names):
        model = "iPhone"
    else:
        model = "produto"
    if header is not None:
        resolved_header = header
    elif installment_budget is not None:
        installment_count, monthly_limit = installment_budget
        entry_text = (
            f" e entrada de {format_brl(entry_amount)}"
            if entry_amount is not None
            else " sem entrada"
        )
        resolved_header = (
            f"Sim 😊 Encontrei estas opções que cabem em até "
            f"{installment_count}x de {format_brl(monthly_limit)}{entry_text}:"
        )
    else:
        resolved_header = f"Sim 😊 Encontrei estas opções de {model} disponíveis:"

    def item_line(item: Any) -> str:
        color = str(getattr(item, "color", None) or getattr(item, "colors", None) or "cor não informada")
        capacity = str(getattr(item, "capacity", None) or "").strip()
        if not capacity:
            name_and_description = f"{getattr(item, 'name', '')} {getattr(item, 'description', '')}"
            capacity_match = re.search(
                r"\b(\d+(?:[.,]\d+)?\s*(?:GB|TB))\b",
                name_and_description,
                flags=re.IGNORECASE,
            )
            capacity = capacity_match.group(1).upper() if capacity_match else "capacidade não informada"
        if getattr(item, "source", None) == "google_sheets":
            condition = "NOVO LACRADO"
            battery_text = "não se aplica"
        else:
            condition = str(getattr(item, "condition", None) or "estado não informado").upper()
            battery_text = _format_battery(getattr(item, "battery_health", None))
        price = getattr(item, "price_brl", None)
        price_text = format_brl(float(price)) if price is not None else "preço a confirmar"
        item_name = str(getattr(item, "name", None) or "produto").strip()
        line = f"• {item_name} — {color} — {capacity} — {condition} — {price_text} | Bat: {battery_text}"
        quote = (installment_quotes or {}).get(id(item))
        if quote:
            line += (
                f" — {quote['vezes']}x de "
                f"{format_brl(float(quote['valor_parcela_brl']))}"
            )
            if entry_amount is None:
                line += f" (total no cartão {format_brl(float(quote['valor_total_brl']))})"
            else:
                line += f" + entrada de {format_brl(entry_amount)}"
        return line

    groups = (
        ("📱 Seminovos disponíveis para pronta entrega:", [item for item in items if not _is_sealed_item(item)]),
        (
            "📦 Lacrados disponíveis para pronta entrega:",
            [
                item
                for item in items
                if _is_sealed_item(item) and not _is_made_to_order_sealed_item(item)
            ],
        ),
        ("📦 Novos lacrados por encomenda:", [item for item in items if _is_made_to_order_sealed_item(item)]),
    )
    populated_groups = [group for _heading, group in groups if group]
    if len(populated_groups) > 1 or groups[-1][1]:
        formatted_lines = [resolved_header]
        for heading, group in groups:
            if not group:
                continue
            formatted_lines.extend(["", heading])
            formatted_lines.extend(item_line(item) for item in group)
        if groups[-1][1]:
            formatted_lines.extend(
                [
                    "",
                    "Os lacrados são por encomenda, com prazo de 1 semana. Em qualquer envio, "
                    "inclusive por motoboy ou Sedex, o pagamento deve ser antecipado antes do "
                    "despacho; na retirada na loja, o pagamento é feito na hora.",
                ]
            )
        if installment_budget is not None:
            formatted_lines.extend(
                [
                    "",
                    "Parcelas calculadas para pagamento no cartão de crédito na máquina física.",
                ]
            )
        return "\n".join(formatted_lines)

    lines = [resolved_header]
    for item in items:
        lines.append(item_line(item))
    if installment_budget is not None:
        lines.extend(
            [
                "",
                "Parcelas calculadas para pagamento no cartão de crédito na máquina física.",
            ]
        )
    return "\n".join(lines)

def _unavailable_catalog_color_header(
    *,
    selected: list[Any],
    requested_models: tuple[tuple[int, str], ...],
    requested_ipad_models: tuple[str, ...],
    requested_families: set[str],
    requested_catalog_colors: tuple[str, ...],
    requested_capacities: set[str],
    has_available_requested_color: bool = False,
    requested_iphone_variant: str | None = None,
    requested_apple_watch_models: tuple[tuple[str, str, str | None], ...] = (),
) -> str:
    if requested_apple_watch_models:
        watch_labels = []
        for family, number, size in requested_apple_watch_models:
            family_label = {"series": "Series", "se": "SE", "ultra": "Ultra"}[family]
            label = f"Apple Watch {family_label} {number}"
            if size:
                label += f" {size} mm"
            watch_labels.append(label)
        model_name = " e ".join(watch_labels)
    elif len(requested_models) == 1:
        model_name = str(getattr(selected[0], "name", None) or "esse modelo").strip()
        model_name = model_name.title().replace("Iphone", "iPhone")
    elif len(requested_ipad_models) == 1:
        ipad_model = requested_ipad_models[0]
        model_name = f"iPad {ipad_model.title()}"
    elif not requested_models and requested_iphone_variant:
        model_name = f"iPhone {requested_iphone_variant.title()}"
    elif not requested_models and len(requested_families) == 1:
        family = next(iter(requested_families))
        model_name = {
            "iphone": "iPhone",
            "ipad": "iPad",
            "macbook": "MacBook",
            "apple_watch": "Apple Watch",
            "apple watch": "Apple Watch",
            "airpods": "AirPods",
        }.get(family, "aparelho")
    else:
        model_name = "aparelho"
    color_label = " e ".join(
        "titânio natural" if color == "natural" else color
        for color in requested_catalog_colors
    )
    capacity_labels = [
        re.sub(
            r"(\d+(?:[.,]\d+)?)(gb|tb)",
            lambda match: f"{match.group(1)} {match.group(2).upper()}",
            capacity,
            flags=re.IGNORECASE,
        )
        for capacity in requested_capacities
    ]
    capacity_text = f" de {' e '.join(capacity_labels)}" if capacity_labels else ""
    if has_available_requested_color:
        if len(requested_catalog_colors) == 1:
            missing_color_text = f"na cor {color_label}"
        else:
            missing_color_text = f"nas cores {color_label}"
        return (
            f"Não localizei o {model_name} {missing_color_text}{capacity_text}. "
            "Encontrei estas opções nas outras cores solicitadas:"
        )
    if len(requested_catalog_colors) > 1:
        return (
            f"No momento, não encontrei o {model_name} nas cores {color_label}{capacity_text}. "
            "Encontrei estas opções em outras cores:"
        )
    return (
        f"No momento, o {model_name} {color_label}{capacity_text} não está disponível. "
        "Encontrei estas opções em outras cores:"
    )

def _format_available_products(result: dict[str, Any]) -> str:
    def entry_line(entry: dict[str, Any]) -> str:
        color = entry.get("cor")
        colors = entry.get("cores") or []
        color_text = str(color or "")
        if not color_text and colors:
            color_text = ", ".join(str(value) for value in colors)
        if not color_text:
            color_text = "cor não informada"

        capacity = str(entry.get("capacidade") or "capacidade não informada")
        condition = str(entry.get("condicao") or "estado não informado").strip().upper()
        prices = entry.get("precos_brl") or []
        price_text = format_brl(float(prices[0])) if len(prices) == 1 else "preço a confirmar"
        battery = entry.get("saude_bateria")
        if battery is not None:
            battery_text = _format_battery(battery)
        elif "LACRADO" in condition:
            battery_text = "não se aplica"
        else:
            battery_text = "não informada"

        parts = [color_text, capacity, condition]
        quantity = entry.get("quantidade")
        if quantity not in (None, 1, 1.0):
            parts.append(f"{quantity} unidades")
        return f"  • - {' - '.join(parts)} — {price_text} | Bat: {battery_text}"

    def grouped_lines(entries: list[dict[str, Any]]) -> list[str]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for entry in entries:
            model = str(entry.get("nome") or "Produto")
            if model not in grouped:
                grouped[model] = []
                order.append(model)
            grouped[model].append(entry)
        lines: list[str] = []
        for model in order:
            lines.append(model)
            lines.extend(entry_line(entry) for entry in grouped[model])
        return lines

    lines = ["📋 Lista completa de produtos disponíveis:"]
    seminovos = result.get("seminovos") or []
    lacrados_pronta_entrega = result.get("lacrados_pronta_entrega") or []
    lacrados = result.get("lacrados") or []
    if seminovos:
        lines.extend(["", "📱 Seminovos disponíveis para venda:"])
        lines.extend(grouped_lines(seminovos))
    if lacrados_pronta_entrega:
        lines.extend(["", "📦 Lacrados disponíveis para pronta entrega:"])
        lines.extend(grouped_lines(lacrados_pronta_entrega))
    if lacrados:
        lines.extend(["", "📦 Novos lacrados por encomenda:"])
        lines.extend(grouped_lines(lacrados))
        lines.append(
            "\nOs lacrados são por encomenda, com prazo de 1 semana. Em qualquer envio, "
            "inclusive por motoboy ou Sedex, o pagamento deve ser antecipado antes do "
            "despacho; na retirada na loja, o pagamento é feito na hora."
        )
    if not seminovos and not lacrados_pronta_entrega and not lacrados:
        return "No momento não localizei modelos disponíveis para informar. Vou encaminhar para um atendente confirmar."
    return "\n".join(lines)
