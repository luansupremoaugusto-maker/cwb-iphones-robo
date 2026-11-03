"""Conversation-context rules used by the customer agent."""

from __future__ import annotations

from app.agent import (
    _BARE_MODEL_CATALOG_FOLLOWUP_RE,
    _BARE_MODEL_SELECTION_REPLY_RE,
    _BARE_VARIANT_CATALOG_FOLLOWUP_RE,
    _CATALOG_BATTERY_UNIT_SELECTION_RE,
    _CATALOG_BUYER_INTEREST_NEGATION_RE,
    _CATALOG_BUYER_INTEREST_RE,
    _CATALOG_BUYER_TRADE_IN_SIGNAL_RE,
    _catalog_families,
    _has_explicit_sealed_condition,
    _has_product_reference,
    _has_recent_catalog_product_context,
    _has_seminovo_reference,
    _is_explicit_catalog_price_request,
    _is_photo_context_followup,
    _is_product_availability_request,
    _normalize,
    _product_context_query,
    _requested_capacity_keys,
    _requested_iphone_model_keys,
    is_photo_offer_confirmation,
    is_trade_in_request,
    re,
)

def _photo_price_selectors(value: str) -> tuple[int, ...]:
    """Extract explicit BRL price selectors without treating capacities as prices."""
    selectors: list[int] = []
    pattern = re.compile(
        r"(?<!\w)(?P<currency>r\$\s*)?"
        r"(?P<amount>\d{1,3}(?:\.\d{3})+|\d{3,6})"
        r"(?P<decimal>[,.]\d{2})?(?!\w)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(value or ""):
        decimal = match.group("decimal") or ""
        amount = match.group("amount")
        if not match.group("currency") and not (
            decimal.startswith(",")
            or (decimal.startswith(".") and len(amount.replace(".", "")) >= 4)
            or ("." in amount and not decimal)
        ):
            continue
        amount = int(amount.replace(".", ""))
        cents = int(decimal[1:]) if decimal else 0
        selector = amount * 100 + cents
        if selector not in selectors:
            selectors.append(selector)
    return tuple(selectors)

def _selected_catalog_row_context(
    text: str,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Keep a customer's exact choice from a preceding product list."""
    current = _normalize(text)
    if not re.search(r"\b(?:esse|essa|desse|dessa|deles|delas|deste|desta)\b", current):
        return None
    if not history:
        return None

    latest_user = next(
        (entry.get("content", "").strip() for entry in reversed(history) if entry.get("role") == "user"),
        "",
    )
    if not latest_user or _has_product_reference(_normalize(latest_user)):
        return None

    selected = _photo_price_selectors(latest_user)
    if not selected:
        return None
    catalog_entries = [
        entry.get("content", "")
        for entry in reversed(history)
        if entry.get("role") == "assistant"
        and any(_photo_price_selectors(line) for line in entry.get("content", "").splitlines())
    ]
    if not catalog_entries:
        return None

    lines = catalog_entries[0].splitlines()
    matches = [
        line.strip()
        for line in lines
        if set(selected).intersection(_photo_price_selectors(line))
    ]
    if len(matches) != 1:
        return None

    line_index = lines.index(matches[0])
    heading = next(
        (line.strip() for line in reversed(lines[:line_index]) if _has_product_reference(_normalize(line))),
        "",
    )
    return "\n".join(part for part in (heading, matches[0], latest_user, text) if part)

def _pending_multi_model_product_request_context(
    text: str,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Keep all iPhone models requested before the bot answers the batch."""
    entries = [
        (index, entry.get("role"), entry.get("content", "").strip())
        for index, entry in enumerate(history or [])
        if entry.get("content", "").strip()
    ]
    latest_assistant_index = max(
        (index for index, role, _content in entries if role == "assistant"),
        default=-1,
    )
    pending_users = [
        (index, content)
        for index, role, content in entries
        if role == "user" and index > latest_assistant_index
    ]
    current = text.strip()
    current_messages = [
        (len(entries) + offset, line.strip())
        for offset, line in enumerate(current.splitlines())
        if line.strip()
    ] or [(len(entries), current)]
    requested_messages: list[tuple[int, str]] = []
    previous_message_was_product_request = False

    def is_owned_device_context(content: str) -> bool:
        normalized = _normalize(content)
        return bool(
            re.search(
                r"^(?:(?:e|ou|mas)\s+)?(?:(?:o|a)\s+)?(?:meu|minha|meus|minhas)\b"
                r"|^(?:atualmente\s+)?(?:eu\s+)?(?:tenho|possuo|uso)\s+"
                r"(?:(?:um|uma|o|a)\s+)?(?:(?:meu|minha)\s+)?"
                r"(?:iphone|ipad|mac(?:book)?|airpods?|apple\s+watch)\b"
                r"|^(?:vendo|vou\s+vender|troco|ofereco)\s+"
                r"(?:(?:o|a)\s+)?(?:meu|minha)\b",
                normalized,
            )
        )

    def has_negated_purchase_intent(content: str) -> bool:
        return bool(
            re.search(
                r"\bnao\s+(?:quero|queria|gostaria|vou|pretendo)\s+(?:de\s+)?"
                r"(?:comprar|ver|saber|conhecer)\b",
                _normalize(content),
            )
        )

    def request_segments(content: str) -> list[str]:
        request_intent_start = (
            r"(?:(?:voc[eê]s?|vcs|a\s+loja)\s+)?"
            r"(?:(?:sabe|sabem)(?:\s+se)?\s+)?"
            r"(?:(?:voc[eê]s?|vcs|a\s+loja)\s+)?"
            r"(?:(?:o|a)\s+)?(?:gostaria|queria|quero|procuro|procurando|preciso|necessito|"
            r"possibilidade|quanto|qual|quais|pre[cç]o|valor|custa|t[eê]m|teria|"
            r"vende|possui|disponivel)\b"
        )
        segments = re.split(
            r"\s+\b(?:mas|por[eé]m|contudo|no\s+entanto)\b\s+"
            rf"|\?\s*(?={request_intent_start})"
            rf"|[,;]\s*(?={request_intent_start})"
            rf"|\s+\be\b\s+(?={request_intent_start})",
            content,
            flags=re.IGNORECASE,
        )
        if len(segments) > 1 and any(
            is_owned_device_context(segment)
            or is_trade_in_request(segment)
            or has_negated_purchase_intent(segment)
            for segment in segments
        ):
            return [segment.strip() for segment in segments if segment.strip()]
        return [content]

    for index, content in [*pending_users, *current_messages]:
        for segment in request_segments(content):
            normalized_segment = _normalize(segment)
            has_model_reference = bool(
                _has_product_reference(normalized_segment)
                or _extract_bare_catalog_model_reference(segment)
                or (
                    previous_message_was_product_request
                    and re.fullmatch(
                        r"(?:e|ou)\s+(?:o\s+)?1[0-9]",
                        normalized_segment,
                    )
                )
            )
            is_owned_context = is_owned_device_context(segment)
            is_trade_context = is_trade_in_request(normalized_segment)
            is_negated_purchase = has_negated_purchase_intent(segment)
            has_catalog_purchase_intent = not is_negated_purchase and any(
                phrase in normalized_segment
                for phrase in (
                    "gostaria de ver",
                    "gostaria de saber mais",
                    "gostaria de informacoes sobre",
                    "queria ver",
                    "quero ver",
                    "quero comprar",
                    "queria comprar",
                    "gostaria de comprar",
                    "possibilidade de comprar",
                    "procuro",
                    "procurando",
                    "preciso",
                    "necessito",
                )
            )
            has_stock_request_opening = bool(
                re.search(
                    r"^(?:(?:oi|ola|bom\s+dia|boa\s+tarde|boa\s+noite)[,! ]+)?"
                    r"(?:(?:voce|voces|vcs|a\s+loja)\s+)?"
                    r"(?:(?:sabe|sabem)\s+se\s+)?"
                    r"(?:(?:voce|voces|vcs|a\s+loja)\s+)?"
                    r"(?:tem|teria|vende|vendem|possui|possuem)\b",
                    normalized_segment,
                )
            )
            has_availability_detail = bool(
                re.search(
                    r"\b(?:disponivel|disponibilidade|em\s+estoque|estoque|"
                    r"a\s+venda|encomenda)\b",
                    normalized_segment,
                )
            )
            has_availability_question = bool(
                (
                    has_availability_detail
                    and (
                        "?" in segment
                        or re.search(
                            r"\b(?:qual|quais|sabe\s+se|voce|voces|vcs)\b",
                            normalized_segment,
                        )
                    )
                )
                or (
                    has_model_reference
                    and re.search(
                        r"\b(?:tem|teria|possui|vende)\s+"
                        r"(?:algum|alguma|alguns|algumas)\s*(?:[?!.,]|$)",
                        normalized_segment,
                    )
                )
            )
            has_request_style_availability = not (
                is_owned_context or is_trade_context or is_negated_purchase
            ) and (has_stock_request_opening or has_availability_question)
            explicit_product_request = has_model_reference and not (
                is_owned_context or is_negated_purchase
            ) and (
                _is_explicit_catalog_price_request(segment)
                or _is_catalog_buyer_interest(segment)
                or has_catalog_purchase_intent
                or has_request_style_availability
            )
            is_followup_model_request = bool(
                has_model_reference
                and not (is_owned_context or is_trade_context or is_negated_purchase)
                and previous_message_was_product_request
                and re.match(r"^(?:e|ou|tambem|mais)\b", _normalize(segment))
            )
            if explicit_product_request or is_followup_model_request:
                requested_messages.append((index, segment))
                previous_message_was_product_request = True
            else:
                previous_message_was_product_request = False

    candidate_messages = requested_messages
    current_models = ()
    if (
        _has_product_reference(_normalize(current))
        or _extract_bare_catalog_model_reference(current)
    ):
        current_models = _requested_iphone_model_keys(current)

    requested_models: list[tuple[int | str, str]] = []
    model_message_indexes: list[int] = []
    for index, content in candidate_messages:
        # Candidate messages already passed the contextual model-reference
        # check above, including short continuations such as "E 12".
        models = _requested_iphone_model_keys(content)
        if not models:
            continue
        model_message_indexes.append(index)
        for model in models:
            if model not in requested_models:
                requested_models.append(model)

    all_referenced_models = {
        model
        for _index, content in [*pending_users, *current_messages]
        if _has_product_reference(_normalize(content))
        or _extract_bare_catalog_model_reference(content)
        for model in _requested_iphone_model_keys(content)
    }
    contains_excluded_model = bool(all_referenced_models - set(requested_models))
    if (
        not requested_models
        or (len(requested_models) <= 1 and not contains_excluded_model)
        or (len(set(model_message_indexes)) <= 1 and not contains_excluded_model)
        or (set(requested_models).issubset(current_models) and not contains_excluded_model)
    ):
        return None

    def model_label(model: tuple[int | str, str]) -> str:
        generation, variant = model
        generation_label = (
            str(generation).upper() if isinstance(generation, str) else str(generation)
        )
        return " ".join(part for part in ("iPhone", generation_label, variant.title()) if part)

    first_model_message = min(model_message_indexes)
    context_messages = [
        content
        for index, content in requested_messages
        if index < len(entries)
        if index >= first_model_message
    ]
    requested_current_context = "\n".join(
        content
        for index, content in requested_messages
        if index >= len(entries) and index >= first_model_message
    )
    if requested_current_context and (
        not context_messages or requested_current_context != context_messages[-1]
    ):
        context_messages.append(requested_current_context)
    model_header = " / ".join(model_label(model) for model in requested_models)
    return "\n".join([model_header, *context_messages]).strip()

def _customer_product_condition_context_query(
    text: str,
    history: list[dict[str, str]] | None,
) -> str:
    """Carry conditions through contextual follow-ups, not complete model queries."""
    current = text.strip()
    normalized_current = _normalize(current)
    if _has_explicit_sealed_condition(normalized_current) or _has_seminovo_reference(
        normalized_current
    ):
        return current
    if (
        _has_product_reference(normalized_current)
        and _is_product_availability_request(current)
    ):
        # A complete model availability question such as "tem iPhone 16?" asks
        # across conditions. Keep the old filter only for short follow-ups that
        # rely on the previous product context.
        return current
    return _customer_product_request_context_query(text, history)

def _extract_bare_catalog_model_reference(text: str) -> str | None:
    """Turn follow-ups such as "a bateria do 15" into an explicit model."""
    pattern = re.compile(
        r"\b(?:do|da|dos|das|de|no|na|o|a|um|uma|esse|essa|este|esta)\s+"
        r"(?:iphone\s*)?(?P<number>\d{1,2})"
        r"(?P<variant>\s*(?:e|pro\s+max|pro|max|plus|mini|air))?\b",
        flags=re.IGNORECASE,
    )
    matches = []
    for match in pattern.finditer(text or ""):
        # A thousands separator lets the first digit of "1.000" or "1,500"
        # look like a bare iPhone generation after a preposition ("faixa de").
        # Ignore that component while preserving an actual model earlier in
        # the same request, such as "o 15 na faixa de 1.000 reais".
        amount_tail = (text or "")[match.end("number") :]
        if re.match(r"\s*[.,]\s*\d{3}(?!\d)", amount_tail):
            continue
        if re.match(r"\s*%|\s+por\s+cento\b", amount_tail, flags=re.IGNORECASE):
            continue
        if re.match(
            r"\s*(?:(?:a|de)\s+)?saude(?:\s+da)?\s+bateria\b",
            _normalize(amount_tail),
            flags=re.IGNORECASE,
        ):
            continue
        matches.append(match)
    if not matches:
        return None
    match = matches[-1]
    number = match.group("number")
    variant = " ".join((match.group("variant") or "").split()).lower()
    if variant == "e":
        return f"iPhone {number}e"
    if variant:
        return f"iPhone {number} {variant}"
    return f"iPhone {number}"

def _is_bare_model_catalog_followup(
    text: str | None,
    history: list[dict[str, str]] | None,
) -> bool:
    """Recognize a short model follow-up after a catalog answer."""
    normalized = _normalize(text)
    is_model_followup = bool(
        _BARE_MODEL_CATALOG_FOLLOWUP_RE.fullmatch(normalized)
        or _BARE_VARIANT_CATALOG_FOLLOWUP_RE.fullmatch(normalized)
    )
    return bool(
        history
        and normalized
        and is_model_followup
        and _has_recent_catalog_product_context(history)
    )

def _is_catalog_model_confirmation(
    text: str | None,
    history: list[dict[str, str]] | None,
) -> bool:
    """Recognize a short model confirmation after the bot asks which model."""
    normalized = _normalize(text)
    match = re.fullmatch(
        r"(?:iphone\s*)?(?P<number>\d{1,2})"
        r"(?:\s+(?P<variant>pro\s+max|pro|max|plus|mini|air|e))?\s+mesmo[.!?]*",
        normalized,
    )
    if not match or not history:
        return False

    latest_assistant = next(
        (
            _normalize(entry.get("content", ""))
            for entry in reversed(history)
            if entry.get("role") == "assistant" and entry.get("content", "").strip()
        ),
        "",
    )
    if not re.search(r"\b(?:quis dizer|outro modelo|qual modelo)\b", latest_assistant):
        return False

    latest_user = next(
        (
            entry.get("content", "")
            for entry in reversed(history)
            if entry.get("role") == "user" and entry.get("content", "").strip()
        ),
        "",
    )
    if not _has_product_reference(_normalize(latest_user)):
        return False

    generation = int(match.group("number"))
    requested_models = _requested_iphone_model_keys(latest_user)
    if match.group("variant"):
        variant = _normalize(match.group("variant"))
        return (generation, variant) in requested_models
    return any(model[0] == generation for model in requested_models)

def _is_bare_model_availability_request(text: str) -> bool:
    """Recognize a shorthand model only when the customer asks about sale/stock."""
    normalized = _normalize(text)
    if (
        not normalized
        or _has_product_reference(normalized)
        or _extract_bare_catalog_model_reference(normalized) is None
    ):
        return False
    return bool(
        re.search(
            r"\b(?:tem|teria|disponivel|disponibilidade|estoque|vende|vender|possui|"
            r"valor|valores|preco|precos|custa)\b"
            r"|\bquanto\s+(?:custa|fica)\b",
            normalized,
        )
    )

def _bare_model_selection_reply(text: str) -> str | None:
    normalized = _normalize(text).strip()
    match = _BARE_MODEL_SELECTION_REPLY_RE.fullmatch(normalized)
    if not match:
        return None
    model = f"iPhone {match.group('number')}"
    if match.group("e"):
        return f"{model}e"
    variant = " ".join((match.group("variant") or "").split())
    return f"{model} {variant}".strip()

def _is_product_interest_prompt(text: str) -> bool:
    normalized = _normalize(text)
    asks_interest = bool(
        re.search(r"\binteresse\b", normalized)
        and re.search(r"\b(?:modelo|produto|iphone|aparelho)\b", normalized)
    )
    asks_model = bool(
        re.search(r"\bqual\s+(?:modelo|produto|iphone|aparelho)\b", normalized)
        and re.search(r"\b(?:procura|procurando|busca|interessa)\b", normalized)
    )
    asks_specific = bool(
        re.search(r"\balgum\s+(?:modelo|produto|iphone|aparelho)\s+especifico\b", normalized)
    )
    return asks_interest or asks_model or asks_specific

def _is_catalog_buyer_interest(text: str | None) -> bool:
    normalized = _normalize(text or "")
    return bool(
        normalized
        and _CATALOG_BUYER_INTEREST_RE.search(normalized)
        and not _CATALOG_BUYER_INTEREST_NEGATION_RE.search(normalized)
        and not _CATALOG_BUYER_TRADE_IN_SIGNAL_RE.search(normalized)
        and not is_trade_in_request(normalized)
    )

def _product_interest_default_condition(text: str) -> str | None:
    normalized = _normalize(text)
    has_seminovo = _has_seminovo_reference(normalized)
    has_sealed = _has_explicit_sealed_condition(normalized)
    if has_seminovo and has_sealed:
        return None
    if has_sealed:
        return "lacrado"
    return "seminovo"

def _product_interest_model_context(
    text: str,
    history: list[dict[str, str]] | None,
) -> tuple[str, str | None] | None:
    """Resolve a bare model selection and its immediate generic price follow-up."""
    model = _bare_model_selection_reply(text)
    if model:
        if not history or history[-1].get("role") != "assistant":
            return None
        prompt = history[-1].get("content", "")
        if not _is_product_interest_prompt(prompt):
            return None
        return model, _product_interest_default_condition(prompt)

    normalized = _normalize(text)
    if _has_product_reference(normalized) or not re.search(
        r"\b(?:valor(?:es)?|precos?|quanto|custa)\b", normalized
    ):
        return None

    latest_user_index = next(
        (
            index
            for index in range(len(history or []) - 1, -1, -1)
            if history[index].get("role") == "user"
        ),
        None,
    )
    if latest_user_index is None:
        return None
    model = _bare_model_selection_reply(history[latest_user_index].get("content", ""))
    if not model:
        return None

    previous_assistant = next(
        (
            history[index].get("content", "")
            for index in range(latest_user_index - 1, -1, -1)
            if history[index].get("role") == "assistant"
        ),
        "",
    )
    if not _is_product_interest_prompt(previous_assistant):
        return None
    return model, _product_interest_default_condition(previous_assistant)

def _catalog_battery_unit_selection_query(
    text: str,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Resolve a short battery-health choice against the recent product offer."""
    match = _CATALOG_BATTERY_UNIT_SELECTION_RE.fullmatch(_normalize(text))
    if not match or not history or history[-1].get("role") != "assistant":
        return None

    try:
        requested_health = float(match.group("health").replace(",", "."))
    except ValueError:
        return None
    if not 0 <= requested_health <= 100:
        return None

    latest_offer = history[-1].get("content", "")
    offered_healths: list[float] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\r?\n", latest_offer):
        normalized_sentence = _normalize(sentence)
        if not re.search(r"\bbateria\b", normalized_sentence):
            continue
        for health_match in re.finditer(
            r"\b(\d{1,3}(?:[.,]\d+)?)\s*(?:%|por\s+cento)(?!\w)",
            normalized_sentence,
        ):
            try:
                offered_health = float(health_match.group(1).replace(",", "."))
            except ValueError:
                continue
            if 0 <= offered_health <= 100:
                offered_healths.append(round(offered_health, 2))

    if offered_healths.count(round(requested_health, 2)) != 1:
        return None

    latest_seminovo = _has_seminovo_reference(latest_offer)
    latest_sealed = _has_explicit_sealed_condition(latest_offer)
    if latest_seminovo and latest_sealed:
        return None
    latest_condition = (
        "seminovo" if latest_seminovo else "lacrado" if latest_sealed else None
    )

    recent_entries = list(enumerate(history[max(0, len(history) - 8) :], start=max(0, len(history) - 8)))
    context: tuple[tuple[int | str, str], str, str] | None = None
    for context_index, entry in reversed(recent_entries):
        if entry.get("role") != "assistant":
            continue
        candidate_offer = entry.get("content", "")
        models = _requested_iphone_model_keys(candidate_offer)
        capacities = _requested_capacity_keys(candidate_offer)
        candidate_seminovo = _has_seminovo_reference(candidate_offer)
        candidate_sealed = _has_explicit_sealed_condition(candidate_offer)
        if (
            len(models) != 1
            or len(capacities) != 1
            or candidate_seminovo == candidate_sealed
        ):
            continue

        candidate_condition = "seminovo" if candidate_seminovo else "lacrado"
        if latest_condition and latest_condition != candidate_condition:
            continue

        context_is_stale = False
        for _user_index, user_entry in recent_entries:
            if not (context_index < _user_index < len(history) - 1):
                continue
            if user_entry.get("role") != "user":
                continue
            user_content = user_entry.get("content", "")
            user_families = _catalog_families(user_content)
            user_models = _requested_iphone_model_keys(user_content)
            user_capacities = _requested_capacity_keys(user_content)
            if (
                (user_families and user_families != ("iphone",))
                or (
                    _has_product_reference(_normalize(user_content))
                    and user_models
                    and user_models != models
                )
                or (user_capacities and user_capacities != capacities)
            ):
                context_is_stale = True
                break
        if context_is_stale:
            continue

        context = (models[0], capacities[0], latest_condition or candidate_condition)
        break

    if context is None:
        return None

    (model_number, variant), capacity, condition = context
    model_label = f"iPhone {model_number}"
    if variant:
        model_label += f" {variant}"
    return (
        f"{model_label} {capacity.upper()} {condition} "
        f"bateria {requested_health:g}%"
    )

def _is_standalone_photo_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    normalized = _normalize(text)
    photo_confirmation = is_photo_offer_confirmation(text, history)
    if normalized not in {"foto", "fotos", "imagem", "imagens"} and not photo_confirmation:
        return False
    if not history:
        return False
    query = _product_context_query(text, history)
    has_product_context = _has_product_reference(_normalize(query))
    has_photo_offer = photo_confirmation or any(
        entry.get("role") == "assistant"
        and any(word in _normalize(entry.get("content", "")) for word in ("foto", "imagem"))
        for entry in history
    )
    return has_product_context and has_photo_offer

def _is_capacity_availability_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Recognize a stock question that follows a previous photo exchange."""
    if not _is_photo_context_followup(text, history):
        return False
    normalized = _normalize(text)
    if not _requested_capacity_keys(text) or any(
        marker in normalized for marker in ("foto", "imagem")
    ):
        return False
    return bool(
        re.search(
            r"\b(?:tem|teria|disponivel|disponibilidade|estoque|vende|vender|possui)\b",
            normalized,
        )
    )
