"""Catalog intents agent rules."""

from __future__ import annotations

from app.agent import (
    Any,
    _MODEL_PATTERN,
    _RequestedModelKey,
    _RequestedModelScope,
    _SEMINOVO_TRADE_IN_CATALOG_TARGET_RE,
    _catalog_families,
    _customer_product_request_context_query,
    _has_explicit_sealed_condition,
    _has_product_reference,
    _has_sealed_reference,
    _has_seminovo_reference,
    _is_accessory_catalog_query,
    _is_explicit_catalog_price_request,
    _normalize,
    _product_context_query,
    _requested_capacity_keys,
    _requested_catalog_colors,
    _requested_ipad_model_keys,
    _requested_iphone_model_keys,
    math,
    re,
)

def _is_image_history_entry(content: str) -> bool:
    normalized = _normalize(content)
    return normalized.startswith("descricao visual da imagem recebida:") or (
        normalized.startswith("legenda da imagem:")
        and "descricao visual da imagem recebida:" in normalized
    )

def _current_catalog_context(text: str, image_description: str | None = None) -> str:
    """Put visual context before explicit text so explicit text wins on conflict."""
    parts = [part.strip() for part in (image_description, text) if part and part.strip()]
    return "\n".join(parts).strip()

def _seminovo_trade_in_catalog_target_query(text: str) -> str | None:
    """Isolate requested used upgrade models from details about the owned phone."""
    match = _SEMINOVO_TRADE_IN_CATALOG_TARGET_RE.search(text or "")
    if not match:
        return None
    models = re.sub(r"\s*/\s*", "/", match.group("models").strip())
    return f"iPhone {models} seminovo disponíveis"

def _visual_catalog_search_query(
    image_description: str | None,
    items: list[Any] | None = None,
) -> str | None:
    """Reduce OCR from a product card to its model, capacity, and color."""
    if not image_description:
        return None
    normalized = _normalize(image_description)
    explicit_models: set[tuple[int | str, str]] = set()
    for match in _MODEL_PATTERN.finditer(normalized):
        if not match.group(0).lower().startswith("iphone"):
            continue
        legacy = match.group("legacy")
        if legacy:
            explicit_models.add((legacy.lower(), ""))
        elif match.group("number") is not None:
            explicit_models.add(
                (
                    int(match.group("number")),
                    " ".join((match.group("variant") or "").split()).lower(),
                )
            )
    if len(explicit_models) > 1:
        return None
    models = _requested_iphone_model_keys(image_description)
    if len(models) != 1:
        return None
    number, variant = models[0]
    parts = ["iPhone", str(number), variant]
    capacities: list[str] = []
    for match in re.finditer(r"\b\d+(?:[.,]\d+)?\s*(?:gb|tb)\b", normalized):
        for capacity in _requested_capacity_keys(match.group(0)):
            if capacity not in capacities:
                capacities.append(capacity)
    if len(capacities) == 1:
        match = re.fullmatch(r"(?P<number>\d+(?:\.\d+)?)(?P<unit>gb|tb)", capacities[0])
        if match:
            parts.extend((match.group("number"), match.group("unit").upper()))
    color = _current_catalog_color(image_description, items or [])
    if color:
        parts.append(color)
    return " ".join(part for part in parts if part)

def _visual_iphone_model_keys(
    image_description: str,
) -> tuple[tuple[int | str, str], ...]:
    """Read every explicitly labeled iPhone model from a catalog image description."""
    models: list[tuple[int | str, str]] = []
    for match in _MODEL_PATTERN.finditer(_normalize(image_description)):
        if not match.group(0).lower().startswith("iphone"):
            continue
        legacy = match.group("legacy")
        model = (
            (legacy.lower(), "")
            if legacy
            else (
                int(match.group("number")),
                " ".join((match.group("variant") or "").split()).lower(),
            )
        )
        if model not in models:
            models.append(model)
    return tuple(models)

def _visual_iphone_model_segments(
    image_description: str,
) -> dict[tuple[int | str, str], tuple[str, ...]]:
    """Keep each product's visual attributes with its labeled iPhone model."""
    normalized = _normalize(image_description)
    mentions: list[tuple[tuple[int | str, str], int]] = []
    for match in _MODEL_PATTERN.finditer(normalized):
        if not match.group(0).lower().startswith("iphone"):
            continue
        legacy = match.group("legacy")
        model = (
            (legacy.lower(), "")
            if legacy
            else (
                int(match.group("number")),
                " ".join((match.group("variant") or "").split()).lower(),
            )
        )
        mentions.append((model, match.start()))

    segments: dict[tuple[int | str, str], list[str]] = {}
    for index, (model, start) in enumerate(mentions):
        end = mentions[index + 1][1] if index + 1 < len(mentions) else len(normalized)
        segments.setdefault(model, []).append(normalized[start:end].strip(" ,;"))
    return {model: tuple(values) for model, values in segments.items()}

def _explicit_image_availability_query(
    text: str,
    image_description: str | None,
) -> str | None:
    """Use only model identity from a photo when the customer clearly asks about stock."""
    normalized_text = _normalize(text)
    if (
        not image_description
        or not re.search(r"\b(?:disponivel|disponibilidade|estoque)\b", normalized_text)
        or not re.search(r"\b(?:tem|teria|possui|sobrou|restou)\b", normalized_text)
    ):
        return None
    if re.search(
        r"\b(?:foto|fotos|imagem|imagens|parcel\w*|entrada|sinal|garantia|"
        r"reserva|endereco|horario|nota\s+fiscal|preco|valor|quanto|"
        r"bateria|cor|\d+\s*(?:gb|tb))\b",
        normalized_text,
    ):
        return None
    if not re.search(
        r"\b(?:desse|desses|dessa|dessas|dele|deles|dela|delas)\b",
        normalized_text,
    ):
        return None

    normalized_image = _normalize(image_description)
    if any(family != "iphone" for family in _catalog_families(normalized_image)):
        return None
    models = _visual_iphone_model_keys(image_description)
    if not models:
        return ""
    query = ", ".join(
        " ".join(part for part in ("iPhone", str(number), variant) if part)
        for number, variant in models
    )
    if not _has_seminovo_reference(text) and not _has_explicit_sealed_condition(text):
        image_seminovos = _has_seminovo_reference(image_description)
        image_sealed = _has_explicit_sealed_condition(image_description)
        if image_seminovos != image_sealed:
            query += " seminovo" if image_seminovos else " lacrado"
    return query

def _is_catalog_followup(text: str) -> bool:
    """Recognize a short price/condition/color follow-up for a prior product."""
    normalized = _normalize(text)
    if not normalized:
        return False
    if any(
        marker in normalized
        for marker in (
            "lacrado",
            "encomenda",
            "novo",
            "seminovo",
            "semi novo",
            "usado",
            "valor",
            "valores",
            "preco",
            "precos",
            "disponivel",
            "disponibilidade",
            "estoque",
            "tem no",
            "tem na",
            "tem em",
            "fonte",
            "carregador",
            "cor",
            "cores",
        )
    ):
        return True
    # A capacity-only availability answer such as "Tb tem de 256gb" refers
    # to the product discussed immediately before it.
    return bool(
        re.search(r"\bem\s+conta\b", normalized)
        or re.search(
            r"\btem\s+de\s+\d+(?:[.,]\d+)?\s*(?:gb|tb|g|t)\b",
            normalized,
        )
    )

def _is_catalog_price_followup(text: str) -> bool:
    normalized = _normalize(text)
    if not re.search(
        r"\b(?:valor(?:es)?|precos?|custa|quanto\s+(?:custa|fica|sai))\b",
        normalized,
    ):
        return False
    if (
        _has_product_reference(normalized)
        or _is_accessory_catalog_request(normalized)
        or re.search(r"\b(?:parcel\w*|juros|\d+\s*x|entrada)\b", normalized)
    ):
        return False
    has_deictic_reference = bool(
        re.search(r"\b(?:dele|dela|desse|dessa|esse|essa|disto|disso)\b", normalized)
    )
    short_price_question = bool(
        re.fullmatch(
            r"(?:e\s+)?(?:(?:qual|me\s+fala)\s+)?(?:o\s+)?(?:valor|precos?)\s*[?!.,]*"
            r"|(?:e\s+)?quanto(?:\s+(?:ele|ela))?\s+(?:custa|fica|sai)\s*[?!.,]*",
            normalized,
        )
    )
    return has_deictic_reference or short_price_question

def _is_other_color_request(text: str) -> bool:
    return bool(re.search(r"\boutra(?:s)?\s+cor(?:es)?\b", _normalize(text)))

def _is_catalog_color_list_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:quais|todas|lista(?:r)?)\s+(?:(?:sao|seriam)\s+)?(?:as\s+)?cores\b",
            _normalize(text),
        )
    )

def _is_battery_detail_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or "bateria" not in normalized:
        return False
    return bool(
        re.search(
            r"\b(?:original|originais?|trocad\w*|substitu\w*|saude|percentual|porcentagem|"
            r"quanto|qto|boa|ruim)\b",
            normalized,
        )
    )

def _is_catalog_availability_confirmation(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Recognize a short confirmation after an explicit availability prompt."""
    normalized = re.sub(r"[^\w\s]", " ", _normalize(text), flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized not in {"sim", "sim por favor", "pode", "pode sim", "claro", "ok", "okay", "beleza"}:
        return False

    for entry in reversed(history or []):
        if entry.get("role") != "assistant" or not entry.get("content"):
            continue
        answer = _normalize(entry.get("content", ""))
        if not _has_product_reference(answer):
            return False
        has_consultation_prompt = bool(re.search(r"\b(?:consultar|verificar|confirmar)\b", answer))
        has_availability_marker = bool(re.search(r"\b(?:disponibilidade|disponivel|estoque)\b", answer))
        has_other_flow = any(marker in answer for marker in ("foto", "imagem", "atendente", "encaminhar"))
        return has_consultation_prompt and has_availability_marker and not has_other_flow
    return False

def _extract_catalog_product_id(text: str) -> str | None:
    """Extract the stock item id from a Mercado Phone catalog message."""
    normalized = _normalize(text)
    url_match = re.search(r"[?&]\s*produto_id\s*=\s*(\d+)", normalized)
    if url_match:
        return url_match.group(1)

    code_match = re.search(
        r"\bcodigo\s*(?:\(\s*estoque\s*\)|estoque)?\s*[:#-]\s*(\d{4,})\b",
        normalized,
    )
    return code_match.group(1) if code_match else None

def _has_pronta_entrega_reference(text: str) -> bool:
    return bool(re.search(r"\bpronta\s+entrega\b", _normalize(text)))

def _is_pronta_entrega_only_request(text: str) -> bool:
    normalized = _normalize(text)
    return _has_pronta_entrega_reference(text) and not re.search(r"\bencomenda\b", normalized)

def _is_contextual_pronta_entrega_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Recognize a short ready-stock question about the product just discussed."""
    normalized = _normalize(text)
    if (
        not normalized
        or not _has_pronta_entrega_reference(text)
        or not re.search(
            r"\b(?:algum|alguma|nenhum|nenhuma|tem|teria|disponivel|"
            r"disponibilidade|estoque|existe|existem)\b",
            normalized,
        )
        or re.search(
            r"\b(?:sedex|motoboy|fretes?|taxas?|envio|enviar|envia\w*|"
            r"mandar|entregam|entregar|prazo)\b",
            normalized,
        )
    ):
        return False
    if _has_product_reference(normalized):
        return True
    context_query = _product_context_query(text, history)
    return _has_product_reference(_normalize(context_query))

def _is_available_list_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if _is_variant_catalog_list_request(text):
        return True
    if _is_generic_iphone_list_request(text):
        return True
    if _has_product_reference(normalized):
        return False
    phrases = (
        "o que tem disponivel",
        "o que voce tem",
        "o que vc tem",
        "o que voces tem",
        "o que a loja tem",
        "quais modelos",
        "quais aparelhos",
        "lista completa",
        "lista de modelos",
        "me manda a lista",
        "me passe a lista",
        "catalogo",
        "tabela de preco",
        "tabela de precos",
        "tabela de valor",
        "tabela de valores",
    )
    return any(phrase in normalized for phrase in phrases)

def _is_variant_catalog_list_request(text: str) -> bool:
    """Recognize a list request for a variant without a generation number."""
    normalized = _normalize(text)
    if not re.search(r"\bpro\s+max\b", normalized):
        return False
    if _requested_iphone_model_keys(text):
        return False
    if _requested_catalog_colors(text, []):
        return False
    return bool(
        re.search(
            r"\b(?:quais?|opcoes?|modelos?|lista|tem|teria|disponivel|"
            r"disponibilidade|estoque|valor|valores|preco|precos)\b",
            normalized,
        )
    )

def _is_all_iphone_17_line_request(text: str) -> bool:
    """Recognize an explicit request for every iPhone 17 line variant."""
    normalized = _normalize(text)
    if _requested_iphone_model_keys(normalized) != ((17, ""),):
        return False
    if _is_accessory_catalog_request(normalized):
        return False
    if not re.search(r"\biphones?\s+17\b", normalized):
        return False
    return bool(
        re.search(
            r"\btodos?\s+(?:eles|os\s+(?:modelos?|iphones?))\b|"
            r"\btodas?\s+(?:as\s+)?(?:opcoes|versoes|variantes)\b|"
            r"\b(?:linha|geracao|familia)\s+(?:completa|17)\b|"
            r"\blinha\s+completa\b",
            normalized,
        )
    )

def _is_generic_iphone_list_request(text: str) -> bool:
    normalized = _normalize(text)
    if not re.search(r"\biphones?\b", normalized):
        return False
    all_line_request = _is_all_iphone_17_line_request(text)
    if _requested_iphone_model_keys(text) and not all_line_request:
        return False
    if _is_accessory_catalog_request(normalized):
        return False
    if _requested_catalog_colors(text, []):
        return False
    if (
        re.search(r"\bate\b", normalized)
        or any(
            marker in normalized
            for marker in (
                "faixa de",
                "orcamento",
                "em torno de",
                "cerca de",
                "por volta de",
                "comprar",
                "retirar",
                "aparelho",
                "unidade",
                "quantos",
                "preciso de",
                "necessito de",
            )
        )
        or ("entrega" in normalized and not _has_pronta_entrega_reference(text))
    ):
        return False
    return all_line_request or bool(
        re.search(
            r"\b(?:modelo|modelos|preco|precos|valor|valores|lista|catalogo|"
            r"tem|vende|possui|disponivel|disponibilidade|ver|mostrar|mostre|"
            r"a\s+venda)\b",
            normalized,
        )
    )

def _is_sealed_catalog_list_request(text: str) -> bool:
    """Recognize a category-level request for all sealed catalog prices."""
    normalized = _normalize(text)
    if not normalized or _has_product_reference(normalized):
        return False
    if _has_seminovo_reference(normalized):
        return False
    new_model_list_request = _is_new_model_list_request(normalized)
    if not new_model_list_request and not re.search(r"\blacrados\b", normalized):
        return False
    return bool(
        re.search(
            r"\b(?:quanto|qual|quais|preco|precos|valor|valores|lista|tabela|"
            r"tem|vende|possui|disponiveis?|disponibilidade|estoque)\b",
            normalized,
        )
    )

def _is_new_model_list_request(text: str) -> bool:
    """Recognize broad requests for new models without an explicit model."""
    normalized = _normalize(text)
    return bool(
        normalized
        and not _has_product_reference(normalized)
        and not _has_seminovo_reference(normalized)
        and re.search(r"\b(?:modelos|aparelhos|celulares|iphones)\b", normalized)
        and re.search(r"\bnov[oa]s?\b", normalized)
    )

def _is_sealed_iphone_list_request(text: str) -> bool:
    """Recognize a sealed-cell-phone request that must exclude Apple accessories."""
    normalized = _normalize(text)
    if not normalized:
        return False
    if not re.search(r"\bcelulares?\b|\biphones?\b", normalized):
        return False
    if not re.search(r"\blacrados?\b", normalized):
        return False
    requested_models = _requested_iphone_model_keys(normalized)
    if requested_models and not _is_all_iphone_17_line_request(normalized):
        return False
    return bool(
        re.search(
            r"\b(?:quanto|qual|quais|preco|precos|valor|valores|lista|tabela|"
            r"tem|vende|possui|disponivel|disponibilidade|estoque)\b",
            normalized,
        )
    )

def _is_broad_airpods_request(text: str) -> bool:
    """Recognize a family-level AirPods query that should show every option."""
    normalized = _normalize(text)
    if not re.search(r"\bairpods?\b|\bair pods\b", normalized):
        return False
    return not re.search(r"\b(?:pro|anc|max|mini|plus|\d+)\b", normalized)

def _is_cheapest_catalog_request(text: str) -> bool:
    """Recognize a request for the lowest-priced available catalog item."""
    normalized = _normalize(text)
    if not normalized:
        return False
    if not re.search(
        r"\bmais\s+barat[oa]\b"
        r"|\bmais\s+em\s+conta\b"
        r"|\b(?:um|uma)\s+(?:mais\s+)?em\s+conta\b"
        r"|\bmenor\s+(?:preco|valor)\b",
        normalized,
    ):
        return False
    return not bool(
        re.search(
            r"\b(?:quais|todos|todas|iphones|modelos|opcoes|aparelhos|unidades)\b",
            normalized,
        )
    )

def _confirmed_catalog_price(item: Any) -> float | None:
    try:
        price = float(getattr(item, "price_brl", None))
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price >= 0 else None

def _is_ipad_model_alternative_price_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    if len(_requested_ipad_model_keys(text)) <= 1:
        return False
    request_context = _customer_product_request_context_query(text, history)
    return _is_explicit_catalog_price_request(request_context)

def _is_affirmative_first_iphone_statement(text: str) -> bool:
    normalized = _normalize(text)
    return bool(
        re.search(r"\bprimeir[oa]\s+iphones?\b", normalized)
        and not re.search(
            r"\b(?:nao|nunca)\s+(?:e\s+|foi\s+|sera\s+)?(?:o\s+)?"
            r"(?:meu|minha)\s+primeir[oa]\s+iphones?\b",
            normalized,
        )
    )

def _is_accessory_catalog_request(text: str) -> bool:
    normalized = _normalize(text)
    if not _is_accessory_catalog_query(normalized):
        return False
    if any(
        marker in normalized
        for marker in (
            "nao funciona",
            "quebrad",
            "defeit",
            "problema",
            "consert",
            "assistencia",
            "acompanha",
            "vem com",
            "vem na caixa",
            "inclus",
        )
    ):
        return False
    return normalized in {"fonte", "fontes", "carregador", "carregadores"} or bool(
        re.search(
            r"\b(?:tem|vende\w*|possui|disponivel|estoque|a venda|valor|valores|preco|precos|custa|original|tipo|usb|20w)\b",
            normalized,
        )
    )

def _is_accessory_inclusion_question(text: str) -> bool:
    normalized = _normalize(text)
    if not re.search(r"\b(?:fontes?|carregador(?:es)?|cabo|acessorio|acessorios)\b", normalized):
        return False
    asks_if_phone_is_sold_alone = bool(
        re.search(
            r"\bvem\s+(?:somente|so)\s+(?:(?:o|a)\s+)?(?:celular|aparelho|iphone)\b",
            normalized,
        )
    )
    asks_what_is_included = bool(
        re.search(
            r"(?:\bvem\s+(?:com|junto|inclus\w*|na\s+caixa)\b|\bacompanha\w*\b|\binclui\w*\b)",
            normalized,
        )
    )
    return asks_if_phone_is_sold_alone or asks_what_is_included

def _requested_model_attribute_scopes(
    model_segments: dict[_RequestedModelKey, tuple[str, ...]],
    items: list[Any],
) -> dict[_RequestedModelKey, tuple[_RequestedModelScope, ...]]:
    """Keep explicit capacities and colors attached to each model segment."""
    scopes: dict[_RequestedModelKey, list[_RequestedModelScope]] = {}
    for model, segments in model_segments.items():
        for segment in segments:
            capacities = _requested_capacity_keys(segment)
            colors = _requested_catalog_colors(segment, items)
            if capacities or colors:
                scopes.setdefault(model, []).append((segment, capacities, colors))
    return {model: tuple(requests) for model, requests in scopes.items()}

def _capacity_list_no_match_reply(text: str, *, sealed_only: bool = False) -> str | None:
    requested_capacity_keys = _requested_capacity_keys(text)
    if not requested_capacity_keys:
        return None

    normalized = _normalize(text)
    other_family_requested = bool(
        re.search(r"\b(?:ipads?|mac(?:books?)?|airpods?|apple\s+watch)\b", normalized)
    )
    is_iphone = bool(re.search(r"\biphones?\b", normalized))
    if is_iphone and not other_family_requested:
        subject = (
            "iPhones lacrados"
            if sealed_only or (_has_sealed_reference(normalized) and not _has_seminovo_reference(normalized))
            else "iPhones"
        )
    else:
        subject = "modelos novos/lacrados" if sealed_only else "modelos"

    capacity_labels = [
        re.sub(
            r"(\d+(?:[.,]\d+)?)(gb|tb)",
            lambda match: f"{match.group(1)} {match.group(2).upper()}",
            capacity,
            flags=re.IGNORECASE,
        )
        for capacity in requested_capacity_keys
    ]
    return (
        f"No momento, não encontrei {subject} com {' e '.join(capacity_labels)} "
        "disponíveis no catálogo. Posso verificar outras capacidades?"
    )

def _requested_capacity_key(text: str) -> str | None:
    keys = _requested_capacity_keys(text)
    return keys[0] if keys else None

def _trailing_model_capacity_scope(
    text: str,
    requested_models: tuple[tuple[int | str, str], ...],
) -> tuple[tuple[int | str, str], str] | None:
    """Scope a trailing capacity to the last model when a request lists alternatives."""
    if len(requested_models) <= 1:
        return None

    normalized = _normalize(text)
    capacity_match = re.search(
        r"(?P<capacity>\d+(?:[.,]\d+)?\s*(?:gb|tb|g|t))\s*$",
        normalized,
    )
    if capacity_match is None:
        return None
    capacities = _requested_capacity_keys(capacity_match.group("capacity"))
    if len(capacities) != 1:
        return None

    model_number, variant = requested_models[-1]
    model_label = " ".join(part for part in (str(model_number), variant) if part)
    before_capacity = normalized[: capacity_match.start()]
    if not re.search(
        rf"\b{re.escape(model_label)}\s*(?:(?:de|do|da|dos|das)\s*)?$",
        before_capacity,
    ):
        return None
    return requested_models[-1], capacities[0]

def _has_requested_catalog_color(text: str, items: list[Any]) -> bool:
    """Return whether the query names a color present in its candidates."""
    normalized = _normalize(text)
    if not normalized:
        return False

    for item in items:
        raw_colors = getattr(item, "color", None) or getattr(item, "colors", None)
        if isinstance(raw_colors, (list, tuple, set)):
            colors = raw_colors
        else:
            colors = re.split(r"\s*[|;/,]\s*", str(raw_colors or ""))
        for value in colors:
            color = _normalize(str(value or "")).strip()
            if color and re.search(rf"(?<!\w){re.escape(color)}(?!\w)", normalized):
                return True
    return False

def _catalog_color_map(items: list[Any]) -> dict[str, str]:
    colors: dict[str, str] = {}
    for item in items:
        raw_colors = getattr(item, "color", None) or getattr(item, "colors", None)
        if isinstance(raw_colors, (list, tuple, set)):
            values = raw_colors
        else:
            values = re.split(r"\s*[|;/,]\s*", str(raw_colors or ""))
        for value in values:
            color = str(value or "").strip()
            normalized_color = _normalize(color)
            if normalized_color:
                colors.setdefault(normalized_color, color)
    return colors

def _catalog_color_correction_scope(text: str) -> str:
    normalized = _normalize(text)
    correction_markers = list(
        re.finditer(
            r"\b(?:na verdade|quer dizer|quis dizer|queria dizer|corrigindo|"
            r"prefiro|prefira|quero|queria|apenas|so)\b",
            normalized,
        )
    )
    return normalized[correction_markers[-1].start() :] if correction_markers else normalized

def _latest_catalog_color_request(
    current_text: str,
    customer_request_context: str,
    items: list[Any],
) -> tuple[str, ...]:
    for candidate in [current_text, *reversed(customer_request_context.splitlines())]:
        colors = _requested_catalog_colors(candidate, items)
        if colors:
            return _requested_catalog_colors(
                _catalog_color_correction_scope(candidate),
                items,
            ) or colors
    return ()

def _is_catalog_color_either_or_request(
    current_text: str,
    image_description: str | None,
    customer_request_context: str,
    items: list[Any],
) -> bool:
    candidates = [
        current_text,
        image_description or "",
        *reversed(customer_request_context.splitlines()),
    ]
    for candidate in candidates:
        colors = _requested_catalog_colors(candidate, items)
        if not colors:
            continue
        if len(colors) < 2:
            return False
        normalized = _normalize(candidate)
        color_spans = []
        for color in colors:
            match = re.search(rf"(?<!\w){re.escape(color)}(?!\w)", normalized)
            if match:
                color_spans.append((match.start(), match.end()))
        color_spans.sort()
        return any(
            re.search(r"\b(?:ou|or)\b", normalized[left_end:right_start])
            for (_left_start, left_end), (right_start, _right_end)
            in zip(color_spans, color_spans[1:])
        )
    return False

def _catalog_color_key(value: Any) -> str:
    normalized = _normalize(str(value or ""))
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    aliases = {
        "amarela": "amarelo",
        "branca": "branco",
        "dourada": "dourado",
        "prata": "prateado",
        "prateada": "prateado",
        "preta": "preto",
        "roxa": "roxo",
        "vermelha": "vermelho",
    }
    return " ".join(aliases.get(word, word) for word in normalized.split())

def _item_matches_catalog_colors(item: Any, requested_colors: tuple[str, ...]) -> bool:
    raw_colors = getattr(item, "color", None) or getattr(item, "colors", None)
    if isinstance(raw_colors, (list, tuple, set)):
        values = raw_colors
    else:
        values = re.split(r"\s*[|;/,]\s*", str(raw_colors or ""))
    item_colors = [_catalog_color_key(value) for value in values]
    return any(
        color and requested_color
        and re.search(rf"(?<!\w){re.escape(requested_color)}(?!\w)", color)
        for color in item_colors
        for requested_color in map(_catalog_color_key, requested_colors)
    )

def _strip_catalog_color_references(query: str, items: list[Any]) -> str:
    normalized_query = _normalize(query)
    for color in sorted(_catalog_color_map(items), key=len, reverse=True):
        normalized_query = re.sub(
            rf"(?<!\w){re.escape(color)}(?!\w)",
            " ",
            normalized_query,
        )
    return re.sub(r"\s+", " ", normalized_query).strip()

def _current_catalog_color(text: str, items: list[Any]) -> str | None:
    """Return one color explicitly chosen in the current photo request."""
    normalized = _normalize(text)
    if not normalized:
        return None

    colors: set[str] = set()
    for item in items:
        raw_colors = getattr(item, "color", None) or getattr(item, "colors", None)
        if isinstance(raw_colors, (list, tuple, set)):
            values = raw_colors
        else:
            values = re.split(r"\s*[|;/,]\s*", str(raw_colors or ""))
        for value in values:
            color = _normalize(str(value or "")).strip()
            if color and re.search(rf"(?<!\w){re.escape(color)}(?!\w)", normalized):
                colors.add(color)
    return next(iter(colors)) if len(colors) == 1 else None

def _capacity_key(value: Any) -> str | None:
    normalized = _normalize(str(value or ""))
    match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(gb|tb|g)\b", normalized)
    if not match:
        return None
    number = match.group(1).replace(",", ".")
    unit = "tb" if match.group(2) == "tb" else "gb"
    if number.endswith(".0"):
        number = number[:-2]
    if unit == "gb" and number == "500":
        number = "512"
    return f"{number}{unit}"
