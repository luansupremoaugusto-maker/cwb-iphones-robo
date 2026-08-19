from __future__ import annotations

import re
import unicodedata


TRADE_IN_REASON = "Avaliação de aparelho usado para parte do pagamento"
TRADE_IN_NEGOTIATION_REPLY = (
    "Recebi sua mensagem. Vou encaminhar o atendimento para um atendente "
    "concluir a avaliação do aparelho."
)

TRADE_IN_FORM = """✅ Sim, compramos produtos da marca Apple, mediante avaliação. Favor preencher lista de avaliação (copie e cole com as respostas)

Qual modelo de iPhone?
R:
Gb:
Cor:

Se não for iPhone, informe qual produto Apple:
R:

Comprou ele novo ou semi novo?
R:

Acessórios que acompanham?
R:

Marcas de uso?
R:

Riscos na tela?
R:

Tem algo quebrado/defeituoso?
(Mesmo que simples informar qualquer anormalidade)
R:

Possui algo que já foi trocado ou feito algum reparo?
R:

É desbloqueado chip todas operadoras?
R:

Qual a % da Saúde da bateria? (Veja em Ajustes > Bateria > Saúde da bateria)
R:

Valor que pretende no seu usado? (Lembrando que precisamos de margem para revenda)
R:

Ainda tem garantia Apple? Se sim, quanto?
R:

Se puder mandar fotos agradecemos.
Com essas informações já podemos fazer uma avaliação prévia!!

Só avaliamos produtos da marca Apple. Após preencher, vou encaminhar o atendimento para um atendente concluir a avaliação."""


PURCHASE_WITHOUT_TRADE_IN_REPLY = (
    "Entendi! Ent\u00e3o voc\u00ea quer comprar um aparelho novo. "
    "Qual modelo ou capacidade voc\u00ea procura? Posso verificar as op\u00e7\u00f5es dispon\u00edveis."
)
PARTS_BUYBACK_REPLY = (
    "N\u00e3o compramos pe\u00e7as avulsas. Compramos somente produtos completos da Apple, "
    "mediante avalia\u00e7\u00e3o."
)
NON_APPLE_TRADE_IN_REPLY = (
    "No momento, avaliamos para troca somente produtos da Apple. "
    "N\u00e3o aceitamos aparelhos de outras marcas na troca."
)


def _normalize(value: str | None) -> str:
    plain = "".join(
        char for char in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", plain).strip().lower()


_APPLE_PRODUCT_RE = re.compile(
    r"\b(?:iphone|ipad|macbook|airpods?|apple\s+watch|apple)\b",
    re.IGNORECASE,
)
_DEVICE_RE = re.compile(
    r"\b(?:iphone|ipad|macbook|airpods?|apple\s+watch|celular|aparelho|"
    r"smartphone|telefone|usado|usada|seminovo|seminova|apple|produto)\b",
    re.IGNORECASE,
)
_NON_APPLE_RE = re.compile(
    r"\b(?:samsung|galaxy|xiaomi|redmi|motorola|moto\s+g|android|"
    r"google\s+pixel|poco|realme|huawei|nokia)\b",
    re.IGNORECASE,
)

_NEGATION_RE = re.compile(
    r"\b(?:nao|nunca|jamais|sem|nenhum|nenhuma)\b.{0,35}\b"
    r"(?:(?:troca|trocar|troco)(?!\s+(?:de\s+)?(?:pecas?|tela|bateria|"
    r"display|vidro|conector|camera|carcaca|microfone|alto\s+falante|"
    r"chip|flex|placa|componentes?))|vender|venda|trade[- ]?in)\b",
    re.IGNORECASE,
)
_PAYMENT_METHOD_RE = re.compile(
    r"\b(?:dinheiro|pix|cartao|credito|debito|parcelado|parcelar|"
    r"a vista|avista|sinal)\b",
    re.IGNORECASE,
)
_STOCK_QUERY_RE = re.compile(
    r"\b(?:tem|possui|disponivel|estoque|vende)\b.{0,35}\b"
    r"(?:iphone|ipad|macbook|apple\s+watch|airpods?|celular|aparelho|"
    r"usado|seminovo)\b",
    re.IGNORECASE,
)
_PARTS_RE = re.compile(
    r"\b(?:pecas?|tela|bateria|display|vidro|conector|camera|"
    r"carca\u00e7a|carcaca|microfone|alto\s+falante|chip|flex|placa|componente[s]?)\b",
    re.IGNORECASE,
)
_BUYBACK_VERB_RE = re.compile(
    r"\b(?:compr(?:a|am|amos)|peg(?:a|am|amos)|pegm|aceit(?:a|am|amos)|"
    r"receb(?:e|em|emos)|avali(?:a|am|amos))\b",
    re.IGNORECASE,
)
_NON_APPLE_EXCHANGE_RE = re.compile(
    r"\b(?:na\s+troca|para\s+troca|parte\s+do\s+pagamento|"
    r"como\s+entrada|de\s+entrada|retoma\w*|retomar)\b",
    re.IGNORECASE,
)
_COMPLETE_DEVICE_DETAIL_RE = re.compile(
    r"\b(?:caixa|caixinha|mes(?:es)?\s+de\s+uso|uso|impecavel|perfeito|"
    r"estado|saude\s+(?:da\s+)?bateria)\b"
    r"|\b\d{1,3}\s*%\s*(?:de\s*)?bateria\b"
    r"|\b\d{1,3}\s+(?:de\s+)?bateria\b"
    r"|\bbateria\s*(?:de|em|com)?\s*(?:\d{1,3}\s*%|boa|ruim)\b",
    re.IGNORECASE,
)
_BARE_IPHONE_MODEL_RE = re.compile(
    r"\b(?:iphone\s*)?\d{1,2}\s+(?:pro(?:\s+max)?|max|plus|mini|e|se)\b",
    re.IGNORECASE,
)
_BARE_MODEL_EXCHANGE_RE = re.compile(
    r"\b(?:na|para)\s+troca\b.{0,20}\b(?:um|uma)\s+\d{1,2}\b",
    re.IGNORECASE,
)
_CATALOG_PRICE_RECALL_CONTEXT_RE = re.compile(
    r"\b(?:estava|tava)\s+vendo\b.{0,50}\b(?:celular|aparelho|iphone)\b"
    r".{0,50}\b(?:contigo|com\s+(?:voces|vcs)|na\s+loja)\b",
    re.IGNORECASE,
)
_CATALOG_PRICE_RECALL_AMOUNT_RE = re.compile(
    r"\b(?:era|foi|custava|custou|estava\s+por|por)\s+(?:r\$\s*)?"
    r"(?P<amount>(?:\d{1,3}(?:[.,]\d{3})+|\d{3,5})(?:[.,]\d{1,2})?)\b",
    re.IGNORECASE,
)


def _parse_catalog_price_amount(value: str) -> float | None:
    normalized = value.strip()
    if "," in normalized and "." in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    elif "," in normalized:
        integer, fraction = normalized.rsplit(",", 1)
        normalized = normalized.replace(",", "") if len(fraction) == 3 else f"{integer}.{fraction}"
    elif "." in normalized:
        integer, fraction = normalized.rsplit(".", 1)
        normalized = normalized.replace(".", "") if len(fraction) == 3 else f"{integer}.{fraction}"
    try:
        amount = float(normalized)
    except ValueError:
        return None
    return amount if amount > 0 else None


def catalog_price_recall_amount(text: str | None) -> float | None:
    """Extract a remembered catalog price from an informal product reference."""
    normalized = _normalize(text)
    if not normalized or not _CATALOG_PRICE_RECALL_CONTEXT_RE.search(normalized):
        return None
    match = _CATALOG_PRICE_RECALL_AMOUNT_RE.search(normalized)
    return _parse_catalog_price_amount(match.group("amount")) if match else None


def is_catalog_price_recall_request(text: str | None) -> bool:
    """Recognize a remembered store product, not an offer of the customer's device."""
    return catalog_price_recall_amount(text) is not None


def _has_device_reference(text: str) -> bool:
    return bool(_DEVICE_RE.search(text))


def is_non_apple_trade_in_request(text: str | None) -> bool:
    """Recognize a non-Apple buyback question before the LLM can offer a form."""
    normalized = _normalize(text)
    if not normalized or _APPLE_PRODUCT_RE.search(normalized):
        return False
    return bool(
        _NON_APPLE_RE.search(normalized)
        and (_BUYBACK_VERB_RE.search(normalized) or _NON_APPLE_EXCHANGE_RE.search(normalized))
    )


def _has_personal_device_reference(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:meu|minha|meus|minhas|ele|ela|isso|esse|esta)\b.{0,25}"
            r"\b(?:iphone|ipad|macbook|apple\s+watch|airpods?|celular|"
            r"aparelho|smartphone|telefone|usado|seminovo)\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"\b(?:iphone|ipad|macbook|apple\s+watch|airpods?|celular|"
            r"aparelho|smartphone|telefone)\b.{0,25}"
            r"\b(?:meu|minha|meus|minhas|ele|ela)\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"\b(?:tenho|possuo|estou\s+com|to\s+com)\b\s+(?:um|uma)?\s*"
            r"(?:iphone|ipad|macbook|apple\s+watch|airpods?|celular|"
            r"aparelho|smartphone|telefone)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _has_device_offer(text: str) -> bool:
    """Detect an offer of a device, not a generic payment method."""
    # In informal Portuguese, "tem interesse em comprar um iPhone" commonly
    # omits "vocês" and means that the customer is asking whether the store
    # wants to buy the described device. Keep the first-person buyer phrasing
    # ("tenho interesse em comprar") in the catalog flow.
    if re.search(
        r"\b(?:tem|teria|possui)\s+interesse\s+em\s+compr\w*\b",
        text,
        flags=re.IGNORECASE,
    ) and _APPLE_PRODUCT_RE.search(text):
        return True

    # Customers commonly state ownership before the sale intent: "tenho um
    # iPhone 11 Pro Max para vender". Keep this ahead of the stock guard below
    # so the model number is not routed as a catalog lookup.
    if re.search(
        r"\b(?:tenho|possuo|estou|estou com|to com)\b"
        r".{0,60}\b(?:para|pra)?\s*vend(?:er|endo|o|a)\b",
        text,
        flags=re.IGNORECASE,
    ) and _has_device_reference(text):
        return True

    if re.search(
        r"\b(?:trade[- ]?in|retoma|retomar|troca de celular|"
        r"parte do pagamento|como entrada|de entrada|para troca|na troca|na jogada)\b",
        text,
        flags=re.IGNORECASE,
    ) and (
        _has_personal_device_reference(text)
        or (
            (
                _BARE_IPHONE_MODEL_RE.search(text)
                or _BARE_MODEL_EXCHANGE_RE.search(text)
                or _has_device_reference(text)
            )
            and _COMPLETE_DEVICE_DETAIL_RE.search(text)
            and not _NON_APPLE_RE.search(text)
        )
    ):
        return True

    if re.search(
        r"\b(?:dar|dou|us[ao]|usar|oferecer|entregar|passar|ficar)\b"
        r".{0,35}\b(?:meu|minha|celular|iphone|aparelho|usado|ele|ela)\b"
        r".{0,35}\b(?:entrada|pagamento|troca)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return True

    if re.search(
        r"\b(?:quero|vou|posso|gostaria de|pretendo)\s+"
        r"(?:trocar|vender|avaliar|oferecer|dar|usar)\b",
        text,
        flags=re.IGNORECASE,
    ) and _has_device_reference(text):
        return True

    if re.search(
        r"\b(?:aceita|aceitam|pega|pegam|pegm|recebe|recebem|avalia|avaliam)\b"
        r".{0,40}\b(?:meu|minha|meus|minhas|celular|iphone|aparelho|"
        r"usado|seminovo|ele|ela)\b",
        text,
        flags=re.IGNORECASE,
    ):
        return True

    if re.search(
        r"\b(?:vender|vendo|venda|avaliar|avaliacao|quanto vale|"
        r"quanto voces dao|quanto vcs dao)\b.{0,35}\b(?:meu|minha|"
        r"celular|iphone|aparelho|usado|ele|ela)\b",
        text,
        flags=re.IGNORECASE,
    ) and _has_device_reference(text):
        return True

    # Pronoun-only phrasing from voice messages: "pega ele como pagamento?"
    return bool(
        re.search(
            r"\b(?:pega\w*|aceita\w*|dar|da|usar|uso)\b"
            r".{0,25}\b(?:ele|ela|isso|esse)\b.{0,30}\b"
            r"(?:entrada|pagamento|troca)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _is_store_buyback_question(text: str) -> bool:
    """Recognize questions about the shop buying an Apple device."""
    if re.search(r"\b(?:compram|compra)\s+da\s+apple\b", text):
        return False
    if _PAYMENT_METHOD_RE.search(text) and not _has_device_reference(text):
        return False

    store_subject = re.search(
        r"\b(?:voces|vcs|loja|a loja|cwb\.iphones)\b.{0,40}\b"
        r"(?:compram|compra|pegam|pegm|aceitam|recebem|avaliam)\b",
        text,
        flags=re.IGNORECASE,
    )
    verb_first = re.search(
        r"\b(?:compram|compra|pegam|pegm|aceitam|recebem|avaliam)\b.{0,45}\b"
        r"(?:algum|alguma|produto|iphone|ipad|macbook|apple\s+watch|"
        r"airpods?|celular|aparelho|usado|seminovo)\b",
        text,
        flags=re.IGNORECASE,
    )
    return bool((store_subject or verb_first) and _has_device_reference(text)) or bool(
        re.search(r"\b(?:vocês|voces|vcs|loja)\b.{0,35}\baceitam\s+usado\b", text, re.IGNORECASE)
    )


def _has_complete_device_buyback_context(text: str) -> bool:
    """Return True when a part term describes a complete device offer."""
    if _BARE_MODEL_EXCHANGE_RE.search(text) and _COMPLETE_DEVICE_DETAIL_RE.search(text):
        return True
    if _has_complete_device_reference(text) and _BUYBACK_VERB_RE.search(text):
        return True

    # A customer may identify the complete device directly after the buyback
    # verb ("vocês pegam iPhone 17") and send its condition in later
    # fragments. In that case, terms such as "bateria" are specifications,
    # not a request to sell a loose battery.
    for verb_match in _BUYBACK_VERB_RE.finditer(text):
        window = text[verb_match.end() : verb_match.end() + 55]
        device_match = _APPLE_PRODUCT_RE.search(window)
        if not device_match:
            continue
        if _PARTS_RE.search(window[: device_match.start()]):
            continue
        if _COMPLETE_DEVICE_DETAIL_RE.search(text[verb_match.end() :]):
            return True

    for verb_match in _BUYBACK_VERB_RE.finditer(text):
        window = text[verb_match.end() : verb_match.end() + 60]
        part_match = _PARTS_RE.search(window)
        if (
            part_match
            and part_match.start() <= 45
            and _DEVICE_RE.search(window[: part_match.start()])
        ):
            return True

    for part_match in _PARTS_RE.finditer(text):
        prefix = text[max(0, part_match.start() - 45) : part_match.start()]
        suffix = text[part_match.end() : part_match.end() + 60]
        verb_match = _BUYBACK_VERB_RE.search(suffix)
        if (
            _DEVICE_RE.search(prefix)
            and verb_match
            and verb_match.start() <= 45
        ):
            return True

    return False


def _has_complete_device_listing(text: str) -> bool:
    """Recognize a customer's list of complete devices offered for resale."""
    has_sale_language = bool(re.search(r"\b(?:vend(?:endo|er|o|a)|revenda)\b", text))
    has_model_capacity = bool(
        re.search(r"\b\d{1,2}\s+\d+(?:[.,]\d+)?\s*(?:gb|tb)\b", text)
    )
    return has_sale_language and has_model_capacity and _has_device_reference(text)


_COMPLETE_DEVICE_REFERENCE_RE = re.compile(
    r"\b(?:"
    r"(?:tenho|possuo|estou com|to com)\s+(?:um|uma)?\s*"
    r"|(?:meu|minha|meus|minhas)\s+"
    r")(?:iphone|ipad|macbook|airpods?|apple\s+watch|celular|"
    r"aparelho|smartphone|telefone)\b",
    re.IGNORECASE,
)


def _has_complete_device_reference(text: str) -> bool:
    """Recognize an owned complete device, not a part followed by its model."""
    for match in _COMPLETE_DEVICE_REFERENCE_RE.finditer(text):
        prefix = text[max(0, match.start() - 35) : match.start()]
        if re.search(_PARTS_RE.pattern + r"\s+(?:de|do|da)\s*$", prefix):
            continue
        return True
    return False


def is_parts_buyback_request(text: str | None) -> bool:
    """Recognize a question about the shop buying repair parts."""
    normalized = _normalize(text)
    if not normalized or not _PARTS_RE.search(normalized):
        return False
    if _has_complete_device_buyback_context(normalized) or _has_complete_device_listing(normalized):
        return False
    store_subject = re.search(
        r"\b(?:voces|vcs|loja|a loja|cwb\.iphones)\b.{0,40}"
        + _BUYBACK_VERB_RE.pattern,
        normalized,
    )
    verb_first = re.search(
        _BUYBACK_VERB_RE.pattern + r".{0,45}" + _PARTS_RE.pattern,
        normalized,
    )
    parts_first = re.search(
        _PARTS_RE.pattern + r".{0,45}" + _BUYBACK_VERB_RE.pattern,
        normalized,
    )
    return bool(store_subject or verb_first or parts_first)


def is_trade_in_request(text: str | None) -> bool:
    """Return True only for an Apple-device sale or trade-in request.

    This guard runs before the model. It intentionally distinguishes stock and
    purchase questions from offers of a customer's device, and distinguishes a
    cash/card entry from a device used as the entry.
    """
    normalized = _normalize(text)
    if not normalized or normalized.startswith("[foto:"):
        return False
    if is_catalog_price_recall_request(normalized):
        return False

    if re.search(
        r"\b(?:troca\w*|substitu\w*|consert\w*|repar\w*|manuten\w*|arrum\w*)\s+"
        r"(?:a|o|uma|um|de|do|da)?\s*"
        r"(?:pelicula|capa|case|tela|bateria|display|vidro|conector|camera|"
        r"carcaca|microfone|alto\s+falante|chip|numero|linha|cor)\b",
        normalized,
    ):
        return False
    if _NEGATION_RE.search(normalized):
        return False
    if re.search(r"\b(?:nao quero|so quero|s[oó] quero)\s+comprar\b", normalized):
        return False
    if re.search(r"\bcompr(?:ar|o|ei)\b.{0,20}\bda\s+apple\b", normalized):
        return False

    has_offer = _has_device_offer(normalized)
    if has_offer:
        return True

    # Stock and ordinary purchase questions must stay in the catalog flow.
    if _STOCK_QUERY_RE.search(normalized):
        return False
    if re.search(
        r"\b(?:quero|vou|preciso|pretendo|gostaria de)\s+compr\w*\b",
        normalized,
    ):
        return False
    if re.search(r"\b(?:parcela|parcelar|parcelado|cartao|pix|dinheiro)\b", normalized):
        return False

    # A non-Apple brand must not activate the Apple evaluation form.
    if _NON_APPLE_RE.search(normalized) and not _APPLE_PRODUCT_RE.search(normalized):
        return False

    if _is_store_buyback_question(normalized):
        # "da Apple" means buying from Apple, not buying the customer's device.
        return not bool(re.search(r"\bda\s+apple\b", normalized))

    return bool(
        re.search(r"\b(?:avaliacao|avaliar)\s+(?:de\s+)?(?:celular|iphone|"
                  r"aparelho|produto)\b", normalized)
        or re.search(r"\b(?:aceitam|compram|pegam)\s+(?:usado|seminovo)\b", normalized)
    )


_PURCHASE_INTENT_RE = re.compile(
    r"\b(?:quero|vou|preciso|pretendo|gostaria de|decidi|optei por)\s+compr\w*\b"
    r"|\bcomprar\s+(?:um|uma|o|a)?\s*(?:aparelho|celular|iphone|ipad|macbook|novo|outro)\b",
    re.IGNORECASE,
)


_PURCHASE_PICKUP_PAYMENT_RE = re.compile(
    r"\b(?:pix|dinheiro|cartao|credito|debito|a vista|avista)\b"
    r".{0,45}\b(?:buscar|retirar|retirada|levar)\b"
    r"|\b(?:buscar|retirar|retirada|levar)\b"
    r".{0,45}\b(?:pix|dinheiro|cartao|credito|debito|a vista|avista)\b",
    re.IGNORECASE,
)
_MONEY_AMOUNT_RE = re.compile(
    r"(?:\br\$\s*\d[\d.,]*|\b\d[\d.,]*\s*(?:mil|k|reais)\b)",
    re.IGNORECASE,
)


def is_purchase_without_trade_in_request(text: str | None) -> bool:
    """Recognize a purchase or catalog payment request without a device offer."""
    normalized = _normalize(text)
    if (
        normalized
        and _MONEY_AMOUNT_RE.search(normalized)
        and _PURCHASE_PICKUP_PAYMENT_RE.search(normalized)
        and _has_device_reference(normalized)
    ):
        return not is_trade_in_request(normalized)

    if not normalized or not _PURCHASE_INTENT_RE.search(normalized):
        return False
    return not is_trade_in_request(normalized)


_FORM_EM_ANDAMENTO_MARKER = "favor preencher lista de avaliacao"


_SHORT_CONFIRMATION_REPLIES = frozenset(("sim", "sim por favor", "sim pfv", "pode", "pode sim", "claro", "ok", "okay", "beleza"))


def _is_photo_offer_message(content: str) -> bool:
    normalized = _normalize(content)
    return bool(
        _FORM_EM_ANDAMENTO_MARKER not in normalized
        and re.search(r"\b(?:foto|fotos|imagem|imagens)\b", normalized)
        and re.search(r"\b(?:posso|pode|vou|vamos|enviar|envia|mande|mandar|mostrar|mostra)\b", normalized)
        and not re.search(
            r"\b(?:parte do pagamento|como entrada|para troca|trade[- ]?in|"
            r"vender|compramos|aceitam meu|pegam meu|dar meu|usar meu)\b",
            normalized,
        )
    )


def is_photo_offer_confirmation(
    text: str | None,
    history: list[dict[str, str]] | None,
) -> bool:
    normalized = _normalize(text)
    if normalized not in _SHORT_CONFIRMATION_REPLIES or not history:
        return False
    if any(
        entry.get("role") == "user"
        and is_trade_in_request(entry.get("content"))
        for entry in history[-8:]
    ):
        return False
    for entry in reversed(history[-8:]):
        if entry.get("role") != "assistant" or not entry.get("content"):
            continue
        return _is_photo_offer_message(entry.get("content", ""))
    return False


def trade_in_em_andamento(historico: list[dict[str, str]] | None) -> bool:
    """Return True when the bot already sent the evaluation form."""
    for message in historico or []:
        if message.get("role") != "assistant":
            continue
        content = message.get("content", "")
        if isinstance(content, str) and _FORM_EM_ANDAMENTO_MARKER in _normalize(content):
            return True
    return False


def is_trade_in_negotiation(text: str | None) -> bool:
    """Detect closing/negotiation language after a trade-in form was sent."""
    normalized = _normalize(text)
    if not normalized:
        return False
    closing = re.search(
        r"\b(?:vamos fechar|pode fechar|fechado|fechamos|topo|"
        r"aceito (?:a oferta|esse valor)|negociar|negociacao|proposta)\b",
        normalized,
    )
    money = bool(re.search(r"\br\$\s*\d|\b\d{2,5}\s*(?:reais|conto|mil)\b", normalized))
    context = bool(re.search(r"\b(?:valor|oferta|usado|iphone|celular|aparelho|troca)\b", normalized))
    return bool(closing and (money or context))


def is_trade_in_context_request(
    text: str | None,
    history: list[dict[str, str]] | None,
) -> bool:
    """Recognize abbreviated trade-in follow-ups using the recent conversation."""
    if is_trade_in_request(text):
        return True

    normalized = _normalize(text)
    if not normalized or not history:
        return False

    if is_photo_offer_confirmation(text, history):
        return False
    recent_assistant_messages = [
        _normalize(entry.get("content", ""))
        for entry in reversed(history[-8:])
        if entry.get("role") == "assistant" and entry.get("content")
    ]
    trade_in_offer_pending = any(
        "favor preencher lista de avaliacao" not in content
        and (
            (
                re.search(r"\b(?:parte do pagamento|como entrada|para troca)\b", content)
                and _DEVICE_RE.search(content)
            )
            or (
                re.search(r"\b(?:avaliacao|avaliar)\b", content)
                and _DEVICE_RE.search(content)
            )
        )
        for content in recent_assistant_messages
    )

    if normalized in {
        "sim",
        "sim por favor",
        "sim pfv",
        "pode",
        "pode sim",
        "claro",
        "ok",
        "okay",
        "beleza",
    }:
        return trade_in_offer_pending

    # Some customers omit "iPhone" in a short follow-up such as
    # "Tenho 14, quanto ficaria dai?" after a product price was discussed.
    owns_numbered_device = re.search(
        r"\b(?:tenho|possuo|estou com|to com)\s+(?:um\s+)?"
        r"(?:iphone\s*)?\d{1,2}\b",
        normalized,
    )
    asks_for_trade_in_value = re.search(
        r"\b(?:quanto|ficaria|diferenca|troco|valor|pagamento|entrada)\b",
        normalized,
    )
    if not owns_numbered_device or not asks_for_trade_in_value:
        return False
    if re.search(r"\b\d{1,2}\s+anos?\b", normalized):
        return False

    recent_context = " ".join(
        _normalize(entry.get("content", ""))
        for entry in history[-8:]
        if entry.get("content")
    )
    return bool(_APPLE_PRODUCT_RE.search(recent_context))


CONDITION_HANDOFF_REPLY = (
    "N\u00e3o consigo confirmar esse detalhe f\u00edsico somente pelas fotos. "
    "Vou encaminhar sua pergunta para um atendente verificar o estado do aparelho."
)
CONDITION_HANDOFF_REASON = "D\u00favida sobre marcas de uso ou estado f\u00edsico do aparelho"
