from __future__ import annotations

import math
from typing import Any


FIXED_INSTALLMENT_RATES: dict[int, float] = {
    1: 0.0495,
    2: 0.0562,
    3: 0.0676,
    4: 0.0756,
    5: 0.0832,
    6: 0.0925,
    7: 0.0994,
    8: 0.1082,
    9: 0.1173,
    10: 0.1240,
    11: 0.1296,
    12: 0.1405,
    13: 0.1500,
    14: 0.1580,
    15: 0.1640,
    16: 0.1710,
    17: 0.1800,
    18: 0.1920,
}


def format_installment_rates() -> str:
    lines = ["Taxas do cart\u00e3o na m\u00e1quina f\u00edsica:"]
    for count, rate in FIXED_INSTALLMENT_RATES.items():
        percent = f"{rate * 100:.2f}".replace(".", ",")
        lines.append(f"{count}x: {percent}%")
    lines.extend(
        [
            "",
            "Qual modelo e capacidade voc\u00ea gostaria de simular?",
        ]
    )
    return "\n".join(lines)


def _simulate_for_price(item: Any, price: float, installments: int) -> dict[str, Any]:
    count = int(installments)
    if not 1 <= count <= 18:
        return {"encontrado": False, "motivo": "O parcelamento deve estar entre 1x e 18x"}

    rate = FIXED_INSTALLMENT_RATES.get(count)
    total = price / (1 - rate)
    return {
        "encontrado": True,
        "nome": item.name,
        "capacidade": item.capacity,
        "preco_avista_brl": round(price, 2),
        "vezes": count,
        "taxa_percentual": round(rate * 100, 2),
        "valor_total_brl": round(total, 2),
        "valor_parcela_brl": round(total / count, 2),
    }


def simulate_installment(item: Any, installments: int) -> dict[str, Any]:
    price = item.price_brl
    if price is None:
        return {"encontrado": False, "motivo": "Produto sem preço confirmado para parcelamento"}
    return _simulate_for_price(item, float(price), installments)


def _remaining_after_entry(item: Any, entry_amount_brl: float) -> tuple[float, float] | dict[str, Any]:
    price = item.price_brl
    try:
        entry = float(entry_amount_brl)
    except (TypeError, ValueError):
        return {"encontrado": False, "motivo": "Não consegui entender o valor da entrada à vista"}

    if price is None:
        return {"encontrado": False, "motivo": "Produto sem preço confirmado para parcelamento"}
    if not math.isfinite(entry) or entry < 0:
        return {"encontrado": False, "motivo": "O valor da entrada deve ser positivo"}
    if entry >= float(price):
        return {
            "encontrado": False,
            "motivo": "A entrada deve ser menor que o preço total para existir saldo a parcelar",
        }
    return float(price), round(float(price) - entry, 2)


def simulate_installment_with_entry(
    item: Any,
    entry_amount_brl: float,
    installments: int,
) -> dict[str, Any]:
    remaining = _remaining_after_entry(item, entry_amount_brl)
    if isinstance(remaining, dict):
        return remaining
    total_price, remaining_price = remaining
    result = _simulate_for_price(item, remaining_price, installments)
    if result.get("encontrado"):
        result.update(
            {
                "preco_total_brl": round(total_price, 2),
                "entrada_avista_brl": round(float(entry_amount_brl), 2),
                "saldo_restante_brl": remaining_price,
            }
        )
    return result


def simulate_installment_table(item: Any) -> dict[str, Any]:
    """Calcula todas as opções aprovadas, de 1x até 18x."""
    price = item.price_brl
    if price is None:
        return {"encontrado": False, "motivo": "Produto sem preço confirmado para parcelamento"}

    rows = [_simulate_for_price(item, float(price), count) for count in range(1, 19)]
    return {
        "encontrado": True,
        "nome": item.name,
        "capacidade": item.capacity,
        "preco_avista_brl": round(float(price), 2),
        "parcelas": rows,
    }


def simulate_installment_table_with_entry(item: Any, entry_amount_brl: float) -> dict[str, Any]:
    """Calcula 1x a 18x somente sobre o saldo após a entrada à vista."""
    remaining = _remaining_after_entry(item, entry_amount_brl)
    if isinstance(remaining, dict):
        return remaining
    total_price, remaining_price = remaining
    rows = [_simulate_for_price(item, remaining_price, count) for count in range(1, 19)]
    return {
        "encontrado": True,
        "nome": item.name,
        "capacidade": item.capacity,
        "preco_total_brl": round(total_price, 2),
        "entrada_avista_brl": round(float(entry_amount_brl), 2),
        "saldo_restante_brl": remaining_price,
        "preco_avista_brl": remaining_price,
        "parcelas": rows,
    }


def format_brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _format_header(result: dict[str, Any]) -> list[str]:
    name = result.get("nome") or "produto"
    capacity = f" {result['capacidade']}" if result.get("capacidade") else ""
    lines = [f"Parcelamento do {name}{capacity}"]
    if result.get("entrada_avista_brl") is not None:
        lines.extend(
            [
                f"Preço total: {format_brl(float(result['preco_total_brl']))}",
                f"Entrada à vista: {format_brl(float(result['entrada_avista_brl']))}",
                f"Saldo restante para parcelar: {format_brl(float(result['saldo_restante_brl']))}",
            ]
        )
    else:
        lines.append(f"Preço à vista: {format_brl(float(result['preco_avista_brl']))}")
    lines.append("")
    return lines


def format_installment_result(result: dict[str, Any]) -> str:
    if not result.get("encontrado"):
        return str(result.get("motivo") or "Não foi possível calcular o parcelamento.")
    row = result
    lines = _format_header(result)
    lines.append(
        f"{row['vezes']}x de {format_brl(float(row['valor_parcela_brl']))} "
        f"(total {format_brl(float(row['valor_total_brl']))})"
    )
    lines.append("")
    lines.append("Valores calculados para pagamento no cartão de crédito.")
    return "\n".join(lines)


def format_installment_table(result: dict[str, Any]) -> str:
    """Monta a mensagem pronta para WhatsApp com 1x a 18x."""
    if not result.get("encontrado"):
        return str(result.get("motivo") or "Não foi possível calcular o parcelamento.")

    lines = _format_header(result)
    for row in result.get("parcelas", []):
        lines.append(
            f"{row['vezes']}x de {format_brl(float(row['valor_parcela_brl']))} "
            f"(total {format_brl(float(row['valor_total_brl']))})"
        )
    lines.append("")
    lines.append("Valores calculados para pagamento no cartão de crédito.")
    return "\n".join(lines)
