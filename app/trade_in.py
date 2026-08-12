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
    r"(?:troca|trocar|troco|vender|venda|trade[- ]?in)\b",
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


def _has_device_reference(text: str) -> bool:
    return bool(_DEVICE_RE.search(text))


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
    )


def _has_device_offer(text: str) -> bool:
    """Detect an offer of a device, not a generic payment method."""
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
        r"parte do pagamento|como entrada|de entrada|para troca|na troca)\b",
        text,
        flags=re.IGNORECASE,
    ) and _has_personal_device_reference(text):
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
        r"\b(?:aceita|aceitam|pega|pegam|recebe|recebem|avalia|avaliam)\b"
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
    ):
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
        r"(?:compram|compra|pegam|aceitam|recebem|avaliam)\b",
        text,
        flags=re.IGNORECASE,
    )
    verb_first = re.search(
        r"\b(?:compram|compra|pegam|aceitam|recebem|avaliam)\b.{0,45}\b"
        r"(?:algum|alguma|produto|iphone|ipad|macbook|apple\s+watch|"
        r"airpods?|celular|aparelho|usado|seminovo)\b",
        text,
        flags=re.IGNORECASE,
    )
    return bool((store_subject or verb_first) and _has_device_reference(text)) or bool(
        re.search(r"\b(?:vocês|voces|vcs|loja)\b.{0,35}\baceitam\s+usado\b", text, re.IGNORECASE)
    )


def is_trade_in_request(text: str | None) -> bool:
    """Return True only for an Apple-device sale or trade-in request.

    This guard runs before the model. It intentionally distinguishes stock and
    purchase questions from offers of a customer's device, and distinguishes a
    cash/card entry from a device used as the entry.
    """
    normalized = _normalize(text)
    if not normalized or normalized.startswith("[foto:"):
        return False

    if re.search(r"\b(?:trocar|troca)\s+(?:a|o|uma|um)?\s*"
                 r"(?:pelicula|capa|case|tela|chip|numero|linha|cor)\b", normalized):
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


def is_purchase_without_trade_in_request(text: str | None) -> bool:
    """Recognize an explicit purchase intent that does not offer a device."""
    normalized = _normalize(text)
    if not normalized or not _PURCHASE_INTENT_RE.search(normalized):
        return False
    return not is_trade_in_request(normalized)


_FORM_EM_ANDAMENTO_MARKER = "favor preencher lista de avaliacao"


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
