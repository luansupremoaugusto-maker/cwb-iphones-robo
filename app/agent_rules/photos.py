"""Photos agent rules."""

from __future__ import annotations

from app.agent import (
    Any,
    _catalog_families,
    _has_product_reference,
    _is_accessory_catalog_request,
    _normalize,
    _requested_capacity_keys,
    _requested_iphone_model_keys,
    re,
)

def _is_photo_retry_request(text: str) -> bool:
    normalized = _normalize(text)
    retry_phrases = (
        "nao foi enviado",
        "nao chegou",
        "nao recebi",
        "nao apareceu",
        "nao veio",
        "nao carregou",
        "manda de novo",
        "manda novamente",
        "envia de novo",
        "envia novamente",
        "enviar de novo",
        "reenviar",
        "reenvia",
    )
    return bool(normalized and any(phrase in normalized for phrase in retry_phrases))

def _is_photo_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if _is_photo_retry_request(text):
        return True
    # "doto" is a common WhatsApp typo for "foto"; keep the correction local
    # to photo intent so it cannot alter unrelated catalog queries.
    has_photo_word = any(
        word in normalized for word in ("foto", "fotos", "doto", "imagem", "imagens")
    )
    has_photo_abbreviation = bool(re.search(r"\bfts?\b", normalized))
    if not has_photo_word and not has_photo_abbreviation:
        return False
    if any(
        phrase in normalized
        for phrase in (
            "manda",
            "mande",
            "mandar",
            "enviar",
            "envia",
            "mostra",
            "ver",
            "tem",
            "teria",
            "teriam",
            "possui",
            "disponivel",
        )
    ):
        return True
    # Short WhatsApp requests such as "fotos do 16e preto?" often omit the
    # verb. A model/color reference or an object connector is enough to route
    # them through the deterministic photo lookup.
    return _has_product_reference(normalized) or bool(
        re.search(r"\b(?:do|da|desse|dessa|deste|desta|dele|dela)\b", normalized)
    )


def _is_explicit_photo_request(text: str) -> bool:
    """Distinguish a current request to send/view photos from a photo mention."""
    normalized = _normalize(text)
    if not _is_photo_request(text):
        return False
    if _is_photo_retry_request(text):
        return True
    has_action = bool(
        re.search(
            r"\b(?:manda\w*|mande|mandar|enviar|envia\w*|mostra\w*|mostre|"
            r"ver|tem|teria|possui|disponivel|quero|queria|gostaria|"
            r"consegue\w*|cade)\b",
            normalized,
        )
    )
    starts_with_photo = bool(
        re.match(
            r"^(?:(?:me|pode|poderia)\s+)?(?:(?:a|as|uma|umas)\s+)?"
            r"(?:foto|fotos|imagem|imagens)\b",
            normalized,
        )
    )
    return has_action or starts_with_photo

def _has_explicit_photo_device_reference(text: str) -> bool:
    normalized = _normalize(text)
    return bool(
        _catalog_families(normalized)
        or _has_product_reference(normalized)
        or _requested_capacity_keys(text)
        or re.search(
            r"\b(?:celular(?:es)?|aparelhos?|smartphones?|telefones?)\b",
            normalized,
        )
    )

def _strip_historical_accessory_terms_from_photo_query(
    query: str,
    current_query: str,
    request_text: str,
    image_description: str | None = None,
) -> str:
    """Use visual device context without replacing an accessory target."""
    explicitly_names_device = _has_explicit_photo_device_reference(request_text)
    if image_description and not explicitly_names_device:
        normalized_visual = _normalize(image_description)
        visual_subject = re.sub(
            r"^(?:descricao visual da imagem recebida\s*:\s*)",
            "",
            normalized_visual,
        )
        visual_subject = re.sub(
            r"^(?:foto|imagem)(?:\s+(?:mostra|mostrando|de|do|da))?\s*",
            "",
            visual_subject,
        )
        visual_subject = re.sub(r"^(?:um|uma|o|a)\s+", "", visual_subject)
        image_describes_accessory = bool(
            re.match(
                r"(?:capinhas?|capas?|cases?|peliculas?|fontes?|cabos?|"
                r"carregadores?|protetores?(?:\s+de\s+camera)?|suportes?)\b",
                visual_subject,
            )
        )
        explicitly_names_device = (
            not image_describes_accessory
            and _has_explicit_photo_device_reference(image_description)
        )
    if not explicitly_names_device:
        return query
    if not current_query or not query.endswith(current_query):
        return query

    history_context = query[: -len(current_query)]
    if not history_context.strip():
        return query

    history_context = re.sub(
        r"\b(?:pel[ií]culas?|capas?|capinhas?|cases?|fontes?|cabos?|"
        r"carregadores?|protetores?|suportes?)\b",
        " ",
        history_context,
        flags=re.IGNORECASE,
    )
    history_context = re.sub(r"[ \t]+", " ", history_context)
    history_context = re.sub(r" *\n *", "\n", history_context)
    history_context = re.sub(r"\n{2,}", "\n", history_context).strip()
    return "\n".join(part for part in (history_context, current_query) if part)

def _is_broad_promotion_photo_request(text: str) -> bool:
    """Recognize a category-level photo request for promotional iPhones."""
    normalized = _normalize(text)
    return bool(
        _is_photo_request(text)
        and re.search(r"\b(?:promoc\w*|ofert\w*|liquidac\w*)\b", normalized)
        and re.search(r"\biphones?\b", normalized)
        and not _requested_iphone_model_keys(normalized)
        and not _is_accessory_catalog_request(normalized)
    )

def _is_sealed_photo_request(text: str) -> bool:
    """Identify photo requests for sealed, made-to-order products."""
    if not _is_photo_request(text):
        return False
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "por encomenda",
            "encomenda",
        )
    )

def _is_sealed_item(item: Any) -> bool:
    """Identify a sealed item returned by the price sheet/catalog search."""
    source = _normalize(str(getattr(item, "source", "") or ""))
    condition = _normalize(str(getattr(item, "condition", "") or ""))
    return source == "google sheets" or "lacrado" in condition or "encomenda" in condition

def _is_made_to_order_sealed_item(item: Any) -> bool:
    """Identify sheet/order items that must never send catalog photos."""
    source = _normalize(str(getattr(item, "source", "") or "")).replace("_", " ")
    condition = _normalize(str(getattr(item, "condition", "") or ""))
    return source == "google sheets" or "encomenda" in condition
