"""Payments agent rules."""

from __future__ import annotations

from app.agent import (
    AgentDecision,
    Any,
    _ENTRY_AMOUNT_WORD_MULTIPLIERS,
    _ENTRY_AMOUNT_WORD_MULTIPLIER_PATTERN,
    _MODEL_PATTERN,
    _SHORT_IPHONE_MODEL_SELECTION_RE,
    _catalog_families,
    _catalog_family,
    _customer_product_request_context_query,
    _has_explicit_sealed_condition,
    _has_product_reference,
    _has_recent_catalog_sale_context,
    _has_seminovo_reference,
    _is_case_accessory_request,
    _is_explicit_catalog_price_request,
    _is_independent_payment_methods_request,
    _is_photo_request,
    _is_warranty_request,
    _model_key,
    _normalize,
    _requested_battery_health,
    _requested_capacity_keys,
    _requested_catalog_colors,
    _requested_ipad_model_keys,
    _requested_iphone_model_keys,
    _strip_unavailable_catalog_claims,
    _without_installment_count_list,
    format_brl,
    is_trade_in_request,
    re,
)

def _is_credit_only_installment_question(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or "parcel" not in normalized:
        return False
    has_only_marker = bool(re.search(r"\b(?:apenas|somente|so)\b", normalized))
    has_credit_marker = bool(
        re.search(r"\b(?:cartao\s+de\s+credito|credito)\b", normalized)
    )
    return has_only_marker and has_credit_marker

def _is_boleto_payment_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or not re.search(r"\bboletos?\b", normalized):
        return False
    if re.search(
        r"\b(?:nao|nunca|jamais)\s+(?:quero|queria|pretendo)\s+"
        r"(?:(?:usar|pagar|trabalhar)(?:\s+(?:com|no|o))?\s+)?boletos?\b",
        normalized,
    ):
        return False
    if re.search(
        r"\b(?:nao|nunca|jamais)\b.{0,35}\b(?:no|por|via|pelo|em)\s+boleto\b",
        normalized,
    ):
        return False
    has_installment_question = _requested_installments(text) is not None or bool(
        re.search(r"\bparcel\w*\b", normalized)
    )
    has_payment_intent = bool(
        re.search(
            r"\b(?:aceit\w*|compr\w*|aprov\w*|pag\w*|quero|queria|gostaria|"
            r"posso|pode|tem|teria|consegu\w*)\b",
            normalized,
        )
        or re.search(r"\btrabalh\w*\s+com\s+(?:o\s+)?boleto\b", normalized)
    )
    has_store_sale_intent = bool(
        re.search(
            r"\b(?:(?:voce|voces)\s+|(?:a|na)\s+loja\s+)?vend(?:e|em|emos|endo)\b",
            normalized,
        )
    ) and not _is_warranty_request(text)
    return has_installment_question or has_payment_intent or has_store_sale_intent

def _is_contextual_boleto_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    if not re.fullmatch(r"(?:e\s+)?como\s+funciona[?!.]*", _normalize(text)):
        return False

    customer_messages_after_boleto = 0
    for entry in reversed((history or [])[-8:]):
        if entry.get("role") != "user":
            continue
        customer_message = entry.get("content", "")
        if _is_boleto_payment_request(customer_message):
            return customer_messages_after_boleto <= 1
        customer_messages_after_boleto += 1
        if (
            customer_messages_after_boleto > 1
            or not _SHORT_IPHONE_MODEL_SELECTION_RE.fullmatch(
                _normalize(customer_message)
            )
        ):
            return False
    return False

def _is_payment_methods_question(text: str) -> bool:
    normalized = _normalize(text)
    if (
        not normalized
        or _is_payment_link_request(text)
        or (
            _is_explicit_catalog_price_request(text)
            and not _is_independent_payment_methods_request(text)
        )
    ):
        return False
    has_cash_method = bool(re.search(r"\b(?:pix|dinheiro|debito)\b", normalized))
    two_card_payment_question = bool(
        re.search(
            r"\b(?:passar|usar|pagar|dividir)\s+(?:(?:em|com)\s+)?"
            r"(?:dois|duas|2)\s+cartoes?\b",
            normalized,
        )
    )
    if any(
        marker in normalized
        for marker in (
            "simulacao",
            "simular",
            "quanto fica",
            "em quantas",
        )
    ):
        return False
    if (
        "parcel" in normalized
        and not has_cash_method
        and not _is_credit_only_installment_question(text)
        and not two_card_payment_question
    ):
        return False
    has_method = bool(re.search(r"\b(?:pix|dinheiro|debito|credito|cartao|cartoes)\b", normalized))
    has_generic_payment_method_phrase = bool(
        re.search(r"\b(?:forma[s]?|metodo[s]?|modelo[s]?)\s+de\s+pagamento\b", normalized)
    )
    has_short_payment_question = bool(
        re.fullmatch(
            r"(?:e\s+)?(?:pagamento|pagamento\s+como\s+funciona|"
            r"como\s+funciona(?:\s+o)?\s+pagamento"
            r"(?:\s+(?:dele|dela|desse|dessa|deste|desta)"
            r"(?:\s+(?:aparelho|celular|iphone|modelo|produto))?)?)[?!.]*",
            normalized,
        )
    )
    if not has_method and not has_generic_payment_method_phrase and not has_short_payment_question:
        return False
    if has_cash_method and any(marker in normalized for marker in ("taxa", "taxas", "tarifa", "juros", "parcel")):
        return True
    has_request = any(
        phrase in normalized
        for phrase in (
            "forma de pagamento",
            "formas de pagamento",
            "metodo de pagamento",
            "metodos de pagamento",
            "aceita",
            "aceitam",
            "posso pagar",
            "pagar com",
            "pagamento",
            "completar o valor",
            "dar o valor",
        )
    )
    return (
        has_request
        or two_card_payment_question
        or _is_credit_only_installment_question(text)
        or normalized in {"pix", "dinheiro", "debito", "cartao", "cartao de debito"}
    )

def _is_cash_discount_question(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    has_cash_method = bool(
        re.search(r"\b(?:pix|dinheiro|debito|a\s+vista)\b", normalized)
    )
    has_discount_marker = bool(
        re.search(r"\b(?:desconto|desconta|descontar|abatimento|promocao)\b", normalized)
    )
    has_price_equivalence_marker = bool(
        re.search(r"\bmesmo\s+(?:valor|preco)\b", normalized)
    )
    return has_cash_method and (has_discount_marker or has_price_equivalence_marker)

def _is_cash_price_question(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or _is_cash_discount_question(text):
        return False
    has_cash_method = bool(
        re.search(r"\b(?:pix|dinheiro|debito|a\s+vista)\b", normalized)
    )
    asks_for_amount = bool(
        re.search(
            r"\b(?:quanto|valor|preco|custa\w*|fica\w*|sai\w*)\b",
            normalized,
        )
    )
    return has_cash_method and asks_for_amount

def _cash_price_quotes_from_history(
    history: list[dict[str, str]] | None,
) -> list[str]:
    if not _has_recent_catalog_sale_context(history):
        return []

    latest_catalog_reply = next(
        (
            entry.get("content", "")
            for entry in reversed(history or [])
            if entry.get("role") == "assistant"
            and entry.get("content")
            and _has_product_reference(_normalize(entry.get("content", "")))
        ),
        "",
    )
    if not latest_catalog_reply:
        return []

    lines = latest_catalog_reply.splitlines()
    item_price_lines = [
        line.split("|", 1)[0]
        for line in lines
        if re.search(r"\|\s*bat\s*:", line, flags=re.IGNORECASE)
    ]
    cash_price_lines = [
        line
        for line in lines
        if re.search(r"\b(?:preco|valor)\s+a\s+vista\b", _normalize(line))
    ]
    price_lines = item_price_lines or cash_price_lines
    price_pattern = re.compile(
        r"\br\$\s*(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{2})?",
        flags=re.IGNORECASE,
    )
    matches = [
        re.sub(r"\s+", " ", match.group()).strip()
        for line in (price_lines or [latest_catalog_reply])
        for match in price_pattern.finditer(line)
    ]
    unique_prices: list[str] = []
    for price in matches:
        if price.casefold() not in {item.casefold() for item in unique_prices}:
            unique_prices.append(price)
    if price_lines:
        return unique_prices
    if len(unique_prices) == 1:
        return unique_prices
    return []

def _is_price_validity_question(text: str) -> bool:
    normalized = _normalize(text)
    if (
        not normalized
        or _is_explicit_catalog_price_request(text)
        or not re.search(r"\b(?:valor|preco|precos|cotacao)\b", normalized)
    ):
        return False
    return any(
        phrase in normalized
        for phrase in (
            "vale mais",
            "vale ainda",
            "ainda vale",
            "continua valendo",
            "continua esse valor",
            "continua o valor",
            "valor atual",
            "preco atual",
            "valor valido",
            "preco valido",
            "valor mudou",
            "preco mudou",
            "mudou o valor",
            "mudou o preco",
            "valor aumentou",
            "preco aumentou",
            "aumentou o valor",
            "aumentou o preco",
            "subiu o valor",
            "subiu o preco",
            "ainda esta valido",
            "ainda esta valendo",
        )
    )

def _is_payment_link_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    phrases = (
        "link de pagamento",
        "link para pagamento",
        "link do pagamento",
        "pagamento por link",
        "pagamento via link",
        "pagar por link",
        "pagar via link",
        "pagar com link",
        "pagar pelo link",
        "pagamento com link",
        "aceita link",
        "tem link",
        "por link",
        "gerar link",
        "gerar um link",
        "enviar link",
        "enviar um link",
        "mandar link",
        "mandar um link",
        "criar link",
        "criar um link",
        "cartao de credito online",
        "cartao online",
        "pagamento online",
        "cartao por pagamento online",
        "passar cartao por pagamento online",
        "passar cartao online",
        "credito online",
        "pagamento online por cartao",
        "pagamento online com cartao",
        "pagar online com cartao",
        "pagar com cartao online",
        "pagar por cartao online",
        "cartao de credito pela internet",
        "cartao pela internet",
        "pagamento pela internet",
    )
    return any(phrase in normalized for phrase in phrases)

def _has_payment_link_installment_context(history: list[dict[str, str]] | None) -> bool:
    """Recognize a prior assistant answer that already selected link payment."""
    for entry in reversed(history or []):
        if entry.get("role") != "assistant":
            continue
        normalized = _normalize(entry.get("content", ""))
        if not normalized or "link de pagamento" not in normalized:
            continue
        has_installment_table = bool(
            re.search(r"\b(?:1[0-2]|[1-9])\s*x\s+de\b", normalized)
        )
        if has_installment_table or "simulacao do parcelamento pelo link" in normalized:
            return True
    return False

def _is_payment_link_followup(text: str, history: list[dict[str, str]] | None) -> bool:
    """Keep link mode for a short installment question after its link table."""
    if _is_payment_link_request(text) or not _has_payment_link_installment_context(history):
        return False
    return (
        _requested_installments(text) is not None
        or _is_installment_rate_question(text)
        or _is_full_installment_request(text)
    )

def _is_installment_rate_question(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or _is_payment_link_request(text):
        return False
    if any(
        marker in normalized
        for marker in ("taxa de entrega", "taxa do frete", "frete", "motoboy", "sedex", "entrega")
    ):
        return False
    return any(
        marker in normalized
        for marker in ("taxa", "taxas", "juros", "tarifa")
    )

def _is_no_interest_installment_question(text: str) -> bool:
    normalized = _normalize(text)
    if not re.search(r"\bsem\s+juros?\b", normalized):
        return False
    return not re.search(r"\b(?:pix|dinheiro|debito)\b", normalized)

def _is_installment_fee_inclusion_question(text: str) -> bool:
    normalized = _normalize(text)
    if not _is_installment_rate_question(text) or _is_no_interest_installment_question(text):
        return False
    return bool(
        re.search(
            r"\b(?:isso|esse|essa|esses|essas|estes|estas|os\s+valores|as\s+parcelas|"
            r"a\s+tabela|o\s+valor|esse\s+total|essa\s+parcela)\b",
            normalized,
        )
    )

def _is_installment_rate_for_referenced_price(text: str) -> bool:
    """Recognize a machine-fee question that refers to a quoted catalog price."""
    if (
        not _is_installment_rate_question(text)
        or _is_no_interest_installment_question(text)
    ):
        return False
    if _customer_installment_price_reference(text) is not None:
        return True
    normalized = _normalize(text)
    return bool(
        re.search(
            r"\b(?:sob|sobre|para|com)\s+(?:esse|este|aquele)\s+(?:valor|preco)\b|"
            r"\b(?:nesse|neste|desse|deste|daquele)\s+(?:valor|preco)\b|"
            r"\bem\s+cima\s+(?:desse|deste|daquele)\s+(?:valor|preco)\b",
            normalized,
        )
    )

def _has_recent_card_installment_simulation(
    history: list[dict[str, str]] | None,
) -> bool:
    for entry in reversed(history or []):
        role = entry.get("role")
        normalized = _normalize(entry.get("content", ""))
        if role == "assistant":
            if (
                "parcelamento do " in normalized
                and "valores calculados para pagamento no cartao de credito" in normalized
                and "total r$" in normalized
            ):
                return True
            if (
                "taxa de juros da maquina" in normalized
                and "acrescimo varia" in normalized
                and "quantidade de parcelas" in normalized
            ):
                continue
            return False
        if role == "user" and not _is_installment_rate_question(entry.get("content", "")):
            return False
    return False

def _is_full_installment_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if re.search(r"\b(?:[1-9]|1[0-8])\s*x\b", normalized):
        return False
    phrases = (
        "como fica o parcelamento",
        "como funciona o parcelamento",
        "como funciona as parcelas",
        "como funcionam as parcelas",
        "como fica parcelado",
        "quanto fica parcelado",
        "quanto fica o parcelamento",
        "quais sao as parcelas",
        "quais as parcelas",
        "quantas vezes",
        "me passa o parcelamento",
        "simulacao de parcelamento",
        "simular parcelamento",
    )
    return any(phrase in normalized for phrase in phrases)

def _is_generic_installment_count_question(text: str) -> bool:
    normalized = _normalize(text)
    asks_how_installments_work = bool(
        re.fullmatch(
            r"como funciona(?:m)? (?:as parcelas|(?:o )?parcelamento)"
            r"(?: de (?:voces|vcs))?[?!.]*",
            normalized,
        )
    )
    return bool(
        normalized
        and ("quantas vezes" in normalized or asks_how_installments_work)
        and re.search(r"\bparcel\w*\b", normalized)
        and not _has_product_reference(normalized)
        and not _is_payment_link_request(text)
        and not _is_boleto_payment_request(text)
    )

def _is_specific_installment_request(text: str) -> bool:
    normalized = _normalize(text)
    return _requested_installments(text) is not None and bool(
        re.search(r"\b(?:quanto|valor|parcela|parcelado|parcelamento|fica)\b", normalized)
    )

def _parse_brl_amount(raw_value: str) -> float | None:
    value = re.sub(r"[^0-9,.]", "", raw_value or "")
    if not value:
        return None
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    elif value.count(".") > 1:
        value = value.replace(".", "")
    elif "." in value:
        integer, fraction = value.split(".", 1)
        if len(fraction) == 3 and integer.isdigit():
            value = value.replace(".", "")
    try:
        amount = float(value)
    except ValueError:
        return None
    return amount if amount >= 0 else None

def _parse_budget_amount(raw_value: str) -> float | None:
    """Parse a price ceiling, accepting comma-separated thousands from customers."""
    # Brazilian currency uses two decimal places; three digits after a comma in
    # a budget ("3,200") therefore indicates a thousands separator.
    if re.fullmatch(r"\d+,\d{3}", raw_value or ""):
        return float(raw_value.replace(",", ""))
    return _parse_brl_amount(raw_value)

def _extract_budget_limit(text: str) -> float | None:
    """Extract a maximum price from a natural-language budget request."""
    normalized = _normalize(text)
    marker_pattern = re.compile(
        r"\b(?:ate(?!\s+la\b)|no maximo(?: de)?|maximo(?: de)?|na faixa de|faixa de|"
        r"orcamento(?: de)?|em torno de|cerca de|por volta de)\b",
    )
    iphone_model_range = re.search(
        r"\b(?P<start>1[0-9])\s+(?P<range_marker>ate)\s+"
        r"(?:(?:o|a)\s+)?(?:iphones?\s*)?(?P<end>1[0-9])\b",
        normalized,
    )
    latest_budget_limit = None
    for marker in marker_pattern.finditer(normalized):
        amount_match = re.search(
            r"(?:r\$\s*)?(?P<value>\d+(?:[.,]\d+)?)(?:\s*(?P<scale>mil|k))?",
            normalized[marker.end() :],
        )
        if not amount_match:
            continue
        amount_start = marker.end() + amount_match.start("value")
        if (
            iphone_model_range is not None
            and re.search(r"\biphones?\b", normalized)
            and marker.start() == iphone_model_range.start("range_marker")
            and amount_start == iphone_model_range.start("end")
            and amount_match.group("value") == iphone_model_range.group("end")
            and not amount_match.group("scale")
        ):
            # "do 14 até o 16" names a model range; its endpoint is not a
            # sixteen-real budget limit. Keep looking for a later price marker.
            continue

        amount = _parse_budget_amount(amount_match.group("value"))
        if amount is None:
            continue
        # Do not treat delivery deadlines, installment counts, or quantities as
        # prices. This matters when a previous catalog answer is part of the
        # follow-up context, for example: "entrega em até 1 semana".
        amount_end = marker.end() + amount_match.end()
        prefix = normalized[:amount_start]
        suffix = normalized[amount_end:]
        # A model generation is commonly written immediately after "iPhone" or
        # followed by its variant, as in "orçamento do iPhone 15 Pro Max". It is
        # a product reference, not a fifteen-real price limit.
        if re.search(r"\biphones?\s*$", prefix) or re.match(
            r"\s*(?:pro(?:\s+max)?|max|plus|mini|air|e)\b", suffix
        ):
            continue
        if re.match(
            r"\s*(?:x\b|semanas?\b|dias?\b|horas?\b|mes(?:es)?\b|"
            r"vez(?:es)?\b|parcelas?\b|unidades?\b|aparelhos?\b|"
            r"celulares?\b|telefones?\b|iphones?\b|gb\b|tb\b|%)",
            suffix,
        ):
            continue
        if amount_match.group("scale") and amount < 1000:
            amount *= 1000
        latest_budget_limit = amount
    return latest_budget_limit

def _extract_latest_budget_limit(text: str) -> float | None:
    """Return the latest ceiling from newline-delimited customer context."""
    latest_limit = None
    for customer_message in (text or "").splitlines():
        amount = _extract_budget_limit(customer_message)
        if amount is not None:
            latest_limit = amount
    return latest_limit

def _requested_device_quantity(text: str) -> int | None:
    normalized = _normalize(text)
    patterns = (
        r"\b(?:preciso|necessito|quero|vou comprar)\s+(?:de\s+)?"
        r"(?P<count>\d{1,2})\s+(?:aparelhos?|celulares?|telefones?|iphones?|unidades?)\b",
        r"\b(?P<count>\d{1,2})\s+(?:aparelhos?|celulares?|telefones?|iphones?|unidades?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            count = int(match.group("count"))
            if count > 0:
                return count
    return None

def _parse_entry_amount(raw_value: str) -> float | None:
    normalized_value = _normalize(raw_value)
    spoken_thousands = re.fullmatch(
        rf"(?:(?P<multiplier>{_ENTRY_AMOUNT_WORD_MULTIPLIER_PATTERN})\s*)?"
        r"mil(?:\s+reais?)?",
        normalized_value,
    )
    if not spoken_thousands:
        return _parse_brl_amount(raw_value)

    multiplier = spoken_thousands.group("multiplier")
    if not multiplier:
        return 1000.0
    if multiplier in _ENTRY_AMOUNT_WORD_MULTIPLIERS:
        return float(_ENTRY_AMOUNT_WORD_MULTIPLIERS[multiplier] * 1000)
    numeric_multiplier = _parse_brl_amount(multiplier)
    return numeric_multiplier * 1000 if numeric_multiplier is not None else None

def _extract_entry_amount(text: str) -> float | None:
    normalized = _normalize(text)
    numeric_amount = r"(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?"
    spoken_thousands = (
        rf"(?:(?:{_ENTRY_AMOUNT_WORD_MULTIPLIER_PATTERN})\s*)?mil(?:\s+reais?)?"
    )
    amount = rf"(?:{numeric_amount}|{spoken_thousands})"
    has_entry_marker = "entrada" in normalized or "sinal" in normalized
    if has_entry_marker:
        patterns = (
            rf"\b(?:entrada|sinal)\s*(?:(?:a|à)\s+vista\s*)?(?:de|no valor de|:)??\s*(?:r\$\s*)?({amount})",
            rf"\b(?:r\$\s*)?({amount})\s*(?:de\s+)?(?:entrada|sinal)\b",
        )
    elif re.search(r"\b(?:restante|saldo|resto|falta)\b", normalized):
        # Some customers describe a cash down payment without calling it an
        # entry, e.g. "pagando 2mil à vista e parcelando o restante".
        patterns = (
            rf"\b(?:r\$\s*)?({amount})\s*(?:a|à)\s+vista\b",
            rf"\b(?:r\$\s*)?({amount})\s+(?:em|no)\s+dinheiro\b",
        )
    else:
        return None
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if match:
            return _parse_entry_amount(match.group(1))
    return None

def _requested_installments(text: str) -> int | None:
    normalized = _normalize(text)
    for pattern in (r"\b(1[0-8]|[1-9])\s*x\b", r"\b(1[0-8]|[1-9])\s+vezes\b"):
        match = re.search(pattern, normalized)
        if match:
            return int(match.group(1))
    return None

def _extract_installment_budget(text: str) -> tuple[int, float] | None:
    """Extract a maximum monthly payment paired with an installment count."""
    normalized = _normalize(text)
    amount_pattern = r"(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{1,2})?"
    count_pattern = r"(?P<count>1[0-8]|[1-9])\s*(?:x|parcelas?|vezes)\b"
    candidates: list[tuple[int, int, float]] = []
    for count_match in re.finditer(count_pattern, normalized):
        amount_match = re.match(
            rf"\s*(?:(?:de|por)\s*)?(?:(?:ate|no\s+maximo|maximo)\s*)?"
            rf"(?:(?:uns?|mais\s+ou\s+menos)\s*)?(?:r\$\s*)?"
            rf"(?P<amount>{amount_pattern})\b",
            normalized[count_match.end() :],
        )
        if amount_match:
            amount = _parse_brl_amount(amount_match.group("amount"))
            if amount is not None:
                candidates.append(
                    (count_match.start(), int(count_match.group("count")), amount)
                )

    reverse_pattern = re.compile(
        rf"\b(?:ate|no\s+maximo|maximo)\s*"
        rf"(?:(?:uns?|mais\s+ou\s+menos)\s*)?(?:r\$\s*)?"
        rf"(?P<amount>{amount_pattern})\s*(?:reais?\s*)?(?:em|no)?\s*"
        rf"{count_pattern}",
    )
    for reverse_match in reverse_pattern.finditer(normalized):
        amount = _parse_brl_amount(reverse_match.group("amount"))
        if amount is not None:
            candidates.append(
                (
                    reverse_match.start(),
                    int(reverse_match.group("count")),
                    amount,
                )
            )
    if not candidates:
        return None
    _position, count, amount = max(candidates, key=lambda candidate: candidate[0])
    return count, amount

def _is_installment_budget_options_request(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Recognize a product-selection request constrained by a monthly ceiling."""
    request_context = _customer_product_request_context_query(text, history)
    if _extract_installment_budget(request_context) is None:
        return False
    normalized_context = _normalize(request_context)
    normalized_current = _normalize(text)
    if not _has_product_reference(normalized_context):
        return False

    asks_for_options = bool(
        re.search(
            r"\b(?:qual|quais)\s+(?:(?:sao|as?)\s+)?"
            r"(?:opcoes?|alternativas?|modelos?|aparelhos?)\b",
            normalized_current,
        )
    )
    has_monthly_ceiling = bool(
        re.search(r"\b(?:ate|no\s+maximo|maximo|limite|orcamento|teto)\b", normalized_context)
    )
    has_purchase_intent = bool(
        re.search(r"\b(?:quero|queria|procuro|procurando|busco|buscando)\b", normalized_context)
    )
    asks_to_calculate = bool(
        re.search(r"\b(?:simula\w*|calcula\w*|quanto\s+(?:fica|custa))\b", normalized_current)
    )
    return asks_for_options or (has_monthly_ceiling and has_purchase_intent and not asks_to_calculate)

def _last_requested_installments(history: list[dict[str, str]] | None) -> int | None:
    for item in reversed(history or []):
        if item.get("role") != "user":
            continue
        requested = _requested_installments(item.get("content", ""))
        if requested is not None:
            return requested
    return None

def _is_both_unit_installment_request(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    recent_user_messages = [
        item.get("content", "").strip()
        for item in (history or [])
        if item.get("role") == "user" and item.get("content", "").strip()
    ][-8:]
    messages = [*recent_user_messages, text]
    explicitly_both = any(
        re.search(
            r"\b(?:os\s+dois|as\s+duas|esses?\s+dois|essas?\s+duas|"
            r"ambos|ambas|cada\s+um|cada\s+uma)\b",
            _normalize(message),
        )
        for message in messages
    )
    has_installment_context = bool(
        _has_installment_model_prompt(history)
        or _requested_installments(text) is not None
        or _last_requested_installments(history) is not None
        or _is_full_installment_request(text)
    )
    return explicitly_both and has_installment_context

def _is_installment_selection_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    if _requested_installments(text) is not None:
        return True
    if _last_requested_installments(history) is None:
        return False
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in ("bateria", "saude da bateria", "essa opcao", "essa unidade", "qual delas", "a de ", "o de ")
    )

def _has_installment_product_context(value: str) -> bool:
    normalized = _normalize(value)
    return bool(
        re.search(r"\b(?:iphones?|ipads?|macbooks?|airpods?|apple\s+watch)\b", normalized)
        or re.search(r"\b\d{1,2}\s*(?:e|pro|max|air|mini|plus)\b", normalized)
    )

def _has_installment_rate_prompt(history: list[dict[str, str]] | None) -> bool:
    return any(
        entry.get("role") == "assistant"
        and (
            "taxas do cartao na maquina fisica" in _normalize(entry.get("content", ""))
            or "cartao de credito pode ser parcelado somente na maquina fisica"
            in _normalize(entry.get("content", ""))
        )
        and "qual modelo" in _normalize(entry.get("content", ""))
        for entry in (history or [])
    )

def _is_rate_model_followup(text: str, history: list[dict[str, str]] | None) -> bool:
    if (
        _is_photo_request(text)
        or _is_installment_rate_question(text)
        or not _has_installment_rate_prompt(history)
    ):
        return False
    return _has_installment_product_context(text)

def _has_installment_model_prompt(history: list[dict[str, str]] | None) -> bool:
    """Recognize the assistant asking for the product to simulate."""
    for entry in reversed(history or []):
        if entry.get("role") != "assistant":
            continue
        normalized = _normalize(entry.get("content", ""))
        if not normalized or "capacidade" not in normalized:
            continue
        if not re.search(r"\bqu(?:al|ais)\b.{0,48}\b(?:modelos?|aparelhos?)\b", normalized):
            continue
        if any(marker in normalized for marker in ("simul", "parcel", "vezes")):
            return True
    return False

def _is_installment_offer_confirmation(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    normalized = _normalize(text)
    if not re.fullmatch(
        r"(?:sim|claro|claro que sim|pode|pode sim|pode ser|quero|quero sim|"
        r"sim por favor|pode por favor|claro pode)[?!.\s]*",
        normalized,
    ):
        return False
    if not history or history[-1].get("role") != "assistant":
        return False
    offer = _normalize(history[-1].get("content", ""))
    return bool(
        re.search(r"\b(?:simul\w*|parcel\w*)\b", offer)
        and re.search(r"\b(?:posso|quer|se quiser|se voce quiser|gostaria)\b", offer)
    )

def _recent_installment_entry_amount(
    history: list[dict[str, str]] | None,
) -> float | None:
    """Recover a confirmed cash entry for a just-accepted installment offer."""
    for item in reversed((history or [])[-12:]):
        content = item.get("content", "")
        if item.get("role") == "assistant":
            amount = _extract_entry_amount(content)
            if amount is not None:
                return amount
            normalized = _normalize(content)
            if _has_product_reference(normalized) and re.search(r"\br\s*\$", normalized):
                return None
        elif item.get("role") == "user":
            if _requested_installment_product_keys(content):
                return None
            amount = _extract_entry_amount(content)
            if amount is not None:
                return amount
    return None

def _history_installment_condition(
    text: str,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Carry a customer's latest condition choice into a product follow-up."""
    normalized = _normalize(text)
    if _has_explicit_sealed_condition(normalized) or _has_seminovo_reference(normalized):
        return None

    current_models = _requested_iphone_model_keys(text)
    if not current_models:
        return None

    def history_model_keys(content: str) -> tuple[tuple[int | str, str], ...]:
        models = _requested_iphone_model_keys(content)
        if models:
            return models
        if _catalog_family(content) != "iphone":
            return ()
        fallback = _model_key(content)
        return (fallback,) if fallback is not None else ()

    history_models: tuple[tuple[int | str, str], ...] = ()
    selected_condition: str | None = None
    for item in history or []:
        content = item.get("content", "")
        item_models = history_model_keys(content)
        if item_models and item.get("role") == "user":
            history_models = item_models
            selected_condition = None
        elif item_models and not history_models:
            history_models = item_models
        if item.get("role") != "user":
            continue
        if _has_explicit_sealed_condition(_normalize(content)):
            selected_condition = "novo lacrado" if set(current_models) & set(history_models) else None
        elif _has_seminovo_reference(_normalize(content)):
            selected_condition = "seminovo" if set(current_models) & set(history_models) else None

    return selected_condition

def _is_installment_model_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Continue an installment prompt unless the customer explicitly requests photos."""
    normalized = _normalize(text)
    refers_to_prior_photos = bool(
        re.search(r"\b(?:esses?|essas?|os?|as?)\s+(?:dois|duas)\s+", normalized)
        and re.search(r"\b(?:iphones?|aparelhos?|celulares?)\b", normalized)
        and re.search(r"\b(?:foto|fotos|imagem|imagens)\b", normalized)
        and not re.search(
            r"\b(?:manda|mande|mandar|envia|envie|enviar|mostra|mostrar|quero\s+ver|"
            r"tem|possui|disponivel)\b",
            normalized,
        )
    )
    return (
        (not _is_photo_request(text) or refers_to_prior_photos)
        and _has_installment_model_prompt(history)
        and _has_installment_product_context(text)
    )

def _format_ambiguous_installment_decision(result: dict[str, Any]) -> AgentDecision | None:
    candidates = result.get("candidatos") or []
    lines: list[str] = []
    references: list[str] = []
    for candidate in candidates[:3]:
        if not isinstance(candidate, dict):
            continue
        reference = str(candidate.get("referencia") or "")
        if reference and reference not in references:
            references.append(reference)
        name = str(candidate.get("nome") or "Produto")
        color = candidate.get("cor")
        capacity = candidate.get("capacidade")
        battery = candidate.get("saude_bateria")
        price = candidate.get("preco_brl")
        details = [name]
        if color:
            details.append(str(color))
        if capacity:
            details.append(str(capacity))
        if battery is not None:
            details.append(f"bateria {float(battery):g}%")
        if price is not None:
            details.append(format_brl(float(price)))
        lines.append(" — ".join(details))
    if not lines:
        return None
    return AgentDecision(
        reply=(
            "Encontrei mais de uma unidade compatível:\n"
            + "\n".join(f"• {line}" for line in lines)
            + "\nQual delas você quer simular?"
        ),
        product_references=references,
        confidence="medium",
    )

def _strip_catalog_history_constraints(value: str) -> str:
    cleaned = re.sub(
        r"\b(?:lacrados?|nov[oa]s?|encomendas?|seminovos?|usados?|entregas?|pagamentos?|"
        r"parcel\w*|taxas?|juros|garantia|reserv\w*|endereco|horario|"
        r"nota\s+fiscal|catalogo)\b",
        " ",
        _normalize(value),
    )
    return re.sub(r"\s+", " ", cleaned).strip()

def _has_recent_specific_installment_selection(
    history: list[dict[str, str]] | None,
) -> bool:
    """Keep a customer's specific model choice across later delivery questions."""
    for item in reversed(history or []):
        if item.get("role") != "user":
            continue
        selection = item.get("content", "").strip()
        if not selection or is_trade_in_request(selection):
            continue
        models = _requested_iphone_model_keys(selection)
        if not models:
            continue
        if len(models) != 1:
            return False
        normalized = _normalize(selection)
        return bool(
            len(_requested_capacity_keys(selection)) == 1
            or _requested_battery_health(selection) is not None
            or _requested_catalog_colors(selection, [])
            or _has_explicit_sealed_condition(normalized)
            or _has_seminovo_reference(normalized)
        )
    return False

def _requested_installment_product_keys(value: str) -> tuple[str, ...]:
    """Return specific product models for catalog families with installment pricing."""
    families = _catalog_families(value)
    if len(families) != 1:
        return ()

    family = families[0]
    if family == "iphone":
        return tuple(str(model) for model in _requested_iphone_model_keys(value))
    if family == "ipad":
        return _requested_ipad_model_keys(value)

    patterns = {
        "macbook": (
            r"\bmacbooks?\s+(?:air|pro|neo)(?:\s+m\d+)?"
            r"(?:\s+\d{2}(?:\s*(?:inch|in|pol))?)?\b"
        ),
        "airpods": (
            r"\bair\s*pods?\s+(?:pro(?:\s+\d+)?|max(?:\s+\d+)?|"
            r"\d+(?:\s+(?:anc|usb\s*c))?)\b"
        ),
        "apple_watch": (
            r"\bapple\s+watch\s+(?:(?:series|se|ultra)\s+\d+|se|ultra)"
            r"(?:\s+\d{2}\s*mm)?\b"
        ),
    }
    pattern = patterns.get(family)
    if not pattern:
        return ()
    return tuple(
        dict.fromkeys(
            re.sub(r"\s+", " ", match.group(0)).strip()
            for match in re.finditer(pattern, _normalize(value))
        )
    )

def _customer_installment_price_reference(text: str) -> float | None:
    """Recognize a price the customer is referring to in a follow-up."""
    normalized = _normalize(text)
    if not re.search(
        r"\br\s*\$|\bsem\s+juros?\b|\b(?:preco|valor|custa|custou)\b",
        normalized,
    ):
        return None

    amount_pattern = re.compile(
        r"(?<!\w)(?:r\s*\$\s*)?"
        r"(?P<amount>\d{1,3}(?:[.,]\d{3})*(?:,\d{2})?|\d{4,}(?:[.,]\d{2})?)"
        r"(?!\w)",
        flags=re.IGNORECASE,
    )
    for match in reversed(list(amount_pattern.finditer(text or ""))):
        amount = _parse_brl_amount(match.group("amount"))
        if amount is not None:
            return amount
    return None

def _selected_price_from_installment_choice(text: str, offer: str) -> float | None:
    """Resolve a bare price only when it answers a recent unit-selection prompt."""
    normalized_offer = _normalize(offer)
    asks_which_unit = bool(
        re.search(
            r"\bqual\s+(?:delas|deles|das\s+duas|dos\s+dois|unidade|opcao)\b",
            normalized_offer,
        )
    )
    asks_to_simulate = bool(re.search(r"\b(?:simul\w*|parcel\w*)\b", normalized_offer))
    if not asks_which_unit or not asks_to_simulate:
        return None

    amount_pattern = re.compile(
        r"(?<!\w)(?:r\s*\$\s*)?"
        r"(?P<amount>\d{1,3}(?:[.,]\d{3})*(?:,\d{2})?|\d{4,}(?:[.,]\d{2})?)"
        r"(?!\w)",
        flags=re.IGNORECASE,
    )
    offer_price_pattern = re.compile(
        r"\br\s*\$\s*(?P<amount>\d{1,3}(?:[.,]\d{3})*(?:,\d{2})?|"
        r"\d{4,}(?:[.,]\d{2})?)",
        flags=re.IGNORECASE,
    )
    selected_amounts = {
        amount
        for match in amount_pattern.finditer(text or "")
        if (amount := _parse_brl_amount(match.group("amount"))) is not None
    }
    offered_amounts = {
        amount
        for match in offer_price_pattern.finditer(offer or "")
        if (amount := _parse_brl_amount(match.group("amount"))) is not None
    }
    matches = selected_amounts.intersection(offered_amounts)
    return next(iter(matches)) if len(matches) == 1 else None

def _selected_installment_offer_context(
    history: list[dict[str, str]] | None,
) -> str | None:
    """Resolve a quoted catalog price to the matching offer in recent history."""
    price_reference: tuple[int, float] | None = None
    for index in range(len(history or []) - 1, -1, -1):
        item = history[index]
        if item.get("role") != "user":
            continue
        content = item.get("content", "")
        amount = _customer_installment_price_reference(content)
        if amount is None:
            previous_offer = next(
                (
                    entry.get("content", "")
                    for entry in reversed((history or [])[:index])
                    if entry.get("role") == "assistant"
                ),
                "",
            )
            amount = _selected_price_from_installment_choice(content, previous_offer)
        if amount is not None:
            price_reference = (index, amount)
            break
    if price_reference is None:
        return None

    price_message_index, selected_price = price_reference
    if any(
        item.get("role") == "user"
        and _has_product_reference(_normalize(item.get("content", "")))
        for item in (history or [])[price_message_index + 1 :]
    ):
        return None

    price_pattern = re.compile(
        r"\br\s*\$\s*(?P<amount>\d{1,3}(?:[.,]\d{3})*(?:,\d{2})?|\d{4,}(?:[.,]\d{2})?)",
        flags=re.IGNORECASE,
    )
    for item in reversed((history or [])[:price_message_index]):
        if item.get("role") != "assistant":
            continue
        content = item.get("content", "")
        segments = re.split(r"\n+|(?<=[.!?])\s+|[;|]\s*|(?=•)", content)
        matching_segments: list[str] = []
        for segment in segments:
            if not _has_installment_product_context(segment):
                continue
            for match in price_pattern.finditer(segment):
                amount = _parse_brl_amount(match.group("amount"))
                if amount is not None and abs(amount - selected_price) < 0.01:
                    normalized_segment = _normalize(segment)
                    if normalized_segment not in {
                        _normalize(candidate) for candidate in matching_segments
                    }:
                        matching_segments.append(segment.strip())
        if matching_segments:
            if len(matching_segments) != 1:
                return None

            selected_segment = matching_segments[0]

            def keep_selected_price(match: re.Match[str]) -> str:
                amount = _parse_brl_amount(match.group("amount"))
                if amount is not None and abs(amount - selected_price) < 0.01:
                    return match.group(0)
                return ""

            selected_context = price_pattern.sub(keep_selected_price, selected_segment)
            return re.sub(r"\s+", " ", selected_context).strip()
    return None

def _bare_iphone_model_in_installment_question(
    text: str,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Resolve an abbreviated model only when a recent iPhone offer anchors it."""
    normalized = _normalize(text)
    if (
        not _is_specific_installment_request(text)
        or _requested_iphone_model_keys(text)
        or any(family != "iphone" for family in _catalog_families(normalized))
    ):
        return None

    match = re.search(
        r"\b(?:do|no|para\s+o)\s+(?P<number>1[0-8])\s+parcel\w*\b",
        normalized,
    )
    if match is None:
        return None

    requested_model = (int(match.group("number")), "")
    for item in reversed(history or []):
        if item.get("role") != "assistant":
            continue
        content = item.get("content", "")
        if _catalog_family(content) != "iphone":
            continue
        if requested_model in _requested_iphone_model_keys(content):
            return f"iPhone {requested_model[0]}"
        return None
    return None

def _installment_context_query(
    text: str,
    history: list[dict[str, str]] | None,
    *,
    strip_assistant_constraints: bool = False,
) -> str:
    # An explicit product in the current message is the strongest context.
    # Do not append an older catalog answer: its last listed model can make a
    # follow-up such as "Do iPhone 12" inherit an unrelated iPhone XR.
    current = text.strip()
    abbreviated_model = _bare_iphone_model_in_installment_question(current, history)
    if abbreviated_model:
        current = f"{abbreviated_model}\n{current}"
    current_capacity_keys = _requested_capacity_keys(current)
    current_capacity_hint = (
        f"capacidade solicitada: {current_capacity_keys[0]}"
        if len(current_capacity_keys) == 1
        else ""
    )
    product_reference_text = _without_installment_count_list(_normalize(current))
    current_families = _catalog_families(product_reference_text)
    latest_history_families = next(
        (
            families
            for item in reversed(history or [])
            if item.get("content", "").strip()
            for families in (_catalog_families(item.get("content", "")),)
            if families
        ),
        (),
    )
    changed_product_family = bool(
        current_families
        and latest_history_families
        and current_families != latest_history_families
    )
    has_specific_product = bool(
        _requested_installment_product_keys(product_reference_text)
        or _requested_iphone_model_keys(product_reference_text)
        or (current_capacity_keys and _has_product_reference(product_reference_text))
        or changed_product_family
    )
    if has_specific_product or _is_case_accessory_request(current):
        history_condition = _history_installment_condition(current, history)
        return "\n".join(
            part for part in (current_capacity_hint, current, history_condition) if part
        ).strip()

    selected_offer = _selected_installment_offer_context(history)
    if selected_offer:
        return "\n".join(
            part for part in (current_capacity_hint, selected_offer, current) if part
        ).strip()

    # A customer may select one unit from a broad offer, ask about delivery,
    # then ask for installments. Keep that selection ahead of the older list.
    for item in reversed(history or []):
        if item.get("role") != "user":
            continue
        customer_context = item.get("content", "").strip()
        if not customer_context or is_trade_in_request(customer_context):
            continue
        if len(_requested_iphone_model_keys(customer_context)) != 1:
            continue
        normalized_selection = _normalize(customer_context)
        has_selection_detail = (
            len(_requested_capacity_keys(customer_context)) == 1
            or _requested_battery_health(customer_context) is not None
            or bool(_requested_catalog_colors(customer_context, []))
            or _has_explicit_sealed_condition(normalized_selection)
            or _has_seminovo_reference(normalized_selection)
        )
        if not has_selection_detail:
            continue
        history_condition = _history_installment_condition(customer_context, history)
        selected_capacity_keys = _requested_capacity_keys(customer_context)
        assistant_context = next(
            (
                entry.get("content", "")
                for entry in reversed(history or [])
                if entry.get("role") == "assistant"
                and re.search(
                    r"\b(?:iphone|ipad|produto)\w*\b",
                    _normalize(entry.get("content", "")),
                )
            ),
            "",
        )
        assistant_capacity_keys = _requested_capacity_keys(assistant_context)
        selection_capacity_hint = current_capacity_hint
        if not selection_capacity_hint and len(selected_capacity_keys) == 1:
            selection_capacity_hint = f"capacidade solicitada: {selected_capacity_keys[0]}"
        elif not selection_capacity_hint and len(assistant_capacity_keys) == 1:
            selection_capacity_hint = f"capacidade solicitada: {assistant_capacity_keys[0]}"
        return "\n".join(
            part
            for part in (
                selection_capacity_hint,
                customer_context,
                history_condition,
                current,
            )
            if part
        ).strip()

    for item in reversed(history or []):
        assistant_context = item.get("content", "")
        # Word boundaries keep "antecipado" from matching the substring "ipad".
        if item.get("role") == "assistant" and re.search(
            r"\b(?:iphone|ipad|macbook|airpods?|apple\s+watch|produto)\w*\b",
            _normalize(assistant_context),
        ):
            assistant_context = _strip_unavailable_catalog_claims(assistant_context)
            if strip_assistant_constraints:
                assistant_context = _strip_catalog_history_constraints(assistant_context)
            # Keep a unique capacity from the selected offer/table ahead of catalog ranking.
            assistant_capacity_keys = _requested_capacity_keys(assistant_context)
            assistant_capacity_hint = (
                f"capacidade solicitada: {assistant_capacity_keys[0]}"
                if not current_capacity_hint and len(assistant_capacity_keys) == 1
                else ""
            )
            return "\n".join(
                part
                for part in (
                    current_capacity_hint or assistant_capacity_hint,
                    assistant_context,
                    current,
                )
                if part
            ).strip()
    previous_user_text = [
        item.get("content", "").strip()
        for item in (history or [])
        if item.get("role") == "user" and item.get("content", "").strip()
    ]
    return "\n".join(
        part for part in (current_capacity_hint, *previous_user_text[-4:], current) if part
    ).strip()

def _multi_model_installment_queries(
    text: str,
    history: list[dict[str, str]] | None,
) -> tuple[str, ...]:
    """Split a multi-model installment request while keeping each model's details."""
    normalized = _normalize(text)
    requested_models = _requested_iphone_model_keys(normalized)
    if len(requested_models) <= 1:
        return ()

    mentions = []
    for match in _MODEL_PATTERN.finditer(normalized):
        raw_number = match.group("number") or match.group("legacy")
        if not raw_number:
            continue
        number: int | str = int(raw_number) if raw_number.isdigit() else raw_number.upper()
        variant = " ".join((match.group("variant") or "").split()).lower()
        mentions.append(((number, variant), match.start(), match.end()))
    if tuple(model for model, _start, _end in mentions) != requested_models:
        return ()

    separator_pattern = re.compile(r"\b(?:e|ou|or|and)\b|[,/;]")
    queries: list[str] = []
    segment_start = 0
    for index, (_model, _start, end) in enumerate(mentions[:-1]):
        next_start = mentions[index + 1][1]
        separators = list(separator_pattern.finditer(normalized, end, next_start))
        if not separators:
            return ()
        separator = separators[-1]
        boundary_start = separator.start()
        boundary_end = separator.end()
        segment = normalized[segment_start:boundary_start].strip()
        if _requested_iphone_model_keys(segment) != (requested_models[index],):
            return ()
        queries.append(segment)
        segment_start = boundary_end

    final_segment = normalized[segment_start:].strip()
    if _requested_iphone_model_keys(final_segment) != (requested_models[-1],):
        return ()
    queries.append(final_segment)

    history_condition = _history_installment_condition(text, history)
    if history_condition:
        queries = [f"{query}\n{history_condition}" for query in queries]
    return tuple(queries)
