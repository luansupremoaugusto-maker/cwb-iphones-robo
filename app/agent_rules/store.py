"""Store agent rules."""

from __future__ import annotations

import app.agent as _agent

from app.agent import (
    FAQStore,
    PAYMENT_METHODS_REPLY,
    STORE_TIMEZONE,
    WEEKDAY_LABELS,
    ZoneInfo,
    ZoneInfoNotFoundError,
    _has_pronta_entrega_reference,
    _has_sealed_reference,
    _is_available_list_request,
    _is_contextual_pronta_entrega_followup,
    _is_product_availability_request,
    _is_sealed_catalog_list_request,
    _normalize,
    datetime,
    re,
    timedelta,
    timezone,
)

def _store_now() -> datetime:
    """Honor the app.agent compatibility hook before using the real clock."""
    provider = getattr(_agent, "_store_now", None)
    if provider is not None and provider is not _store_now:
        return provider()
    return _default_store_now()


def _default_store_now() -> datetime:
    """Return the current time in the store's timezone."""
    try:
        store_timezone = ZoneInfo(STORE_TIMEZONE)
    except ZoneInfoNotFoundError:
        # Keep the bot usable on systems without the IANA timezone database.
        store_timezone = timezone(timedelta(hours=-3))
    return datetime.now(store_timezone)

def _today_label(now: datetime | None = None) -> str:
    current = now or _store_now()
    return f"{WEEKDAY_LABELS[current.weekday()]}, {current:%d/%m/%Y}"

def _is_business_weekday(now: datetime | None = None) -> bool:
    current = now or _store_now()
    return current.weekday() < 5

def _is_physical_store_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    phrases = (
        "loja fisica",
        "so entrega",
        "tem loja",
        "existe loja",
        "nao tem loja",
        "onde fica a loja",
        "qual o endereco",
        "endereco da loja",
        "endereco do escritorio",
        "de onde e a loja",
        "de onde fica a loja",
        "posso ir na loja",
        "posso visitar a loja",
    )
    return any(phrase in normalized for phrase in phrases) or bool(
        re.search(
            r"\b(?:de\s+onde\s+(?:e|fica)|a?onde\s+fica\s+(?:localizad\w*\s+)?(?:a\s+)?loja)\b",
            normalized,
        )
    )

def _has_catalog_condition_signal(text: str) -> bool:
    normalized = _normalize(text)
    return bool(
        re.search(
            r"\b(?:bateria|tela|display|vidro|pecas?|face\s+id|c[aâ]meras?|"
            r"alto\s+falante|microfone|carregamento|botoes?|bloqueio|icloud|"
            r"restric\w*|manuten\w*|trocad\w*|substituid\w*)\b",
            normalized,
        )
    )

def _is_delivery_or_pickup_request(
    text: str,
    history: list[dict[str, str]] | None = None,
) -> bool:
    normalized = _normalize(text)
    if not normalized or _has_sealed_reference(normalized):
        return False
    if _has_pronta_entrega_reference(text) and (
        _is_available_list_request(text)
        or _is_product_availability_request(text)
        or _is_contextual_pronta_entrega_followup(text, history)
    ):
        return False
    has_delivery = bool(re.search(r"\b(?:entrega|entregam|entregas)\b", normalized))
    has_pickup = _has_pickup_reference(normalized)
    return has_delivery or has_pickup or _is_delivery_followup_request(text, history)


def _has_pickup_reference(normalized: str) -> bool:
    return bool(
        re.search(r"\bretirad\w*\b", normalized)
        or any(
            phrase in normalized
            for phrase in (
                "retirar na loja",
                "buscar na loja",
                "pegar na loja",
            )
        )
        or re.search(
            r"\b(?:ir\s+(?:buscar|pegar)|vou\s+buscar)\b",
            normalized,
        )
    )


def _is_pickup_schedule_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or _has_sealed_reference(normalized):
        return False
    has_pickup = bool(
        re.search(r"\b(?:retirad\w*|retir\w*|busc\w*|peg\w*)\b", normalized)
    )
    return (
        has_pickup
        and _has_visit_date_reference(text)
        and _has_visit_time_reference(text)
    )

def _is_delivery_fee_request(
    text: str,
    history: list[dict[str, str]] | None = None,
) -> bool:
    normalized = _normalize(text)
    if not normalized or not re.search(r"\b(?:taxas?|tarifas?|fretes?)\b", normalized):
        return False
    if re.search(
        r"\b(?:taxas?\s+de\s+entrega|taxas?\s+do\s+frete|fretes?|motoboy|sedex|entrega)\b",
        normalized,
    ):
        return True
    has_address_marker = bool(
        re.search(
            r"\b(?:rua|avenida|av\.?|alameda|travessa|praca|bairro|endereco|cep)\b",
            normalized,
        )
    )
    has_address_number = bool(re.search(r"\b\d{1,5}\b", normalized))
    return has_address_marker and has_address_number and _has_delivery_context(history)

def _has_delivery_context(history: list[dict[str, str]] | None) -> bool:
    delivery_markers = (
        "enviamos para curitiba",
        "por motoboy",
        "por sedex",
        "pagamento deve ser antecipado antes do despacho",
    )
    return any(
        entry.get("role") == "assistant"
        and any(marker in _normalize(entry.get("content", "")) for marker in delivery_markers)
        for entry in (history or [])
    )


def _has_delivery_choice_context(history: list[dict[str, str]] | None) -> bool:
    latest_assistant_reply = next(
        (
            _normalize(entry.get("content", ""))
            for entry in reversed(history or [])
            if entry.get("role") == "assistant" and entry.get("content")
        ),
        "",
    )
    return bool(
        re.search(r"\bprefer\w*\b", latest_assistant_reply)
        and re.search(r"\b(?:retir\w*|busc\w*|peg\w*)\b", latest_assistant_reply)
        and re.search(r"\b(?:receb\w*|entreg\w*|envio|envi\w*)\b", latest_assistant_reply)
    )


def _is_delivery_followup_request(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Keep a short shipping confirmation in the delivery flow after the FAQ answer."""
    normalized = _normalize(text)
    if not normalized:
        return False
    if _is_available_list_request(text):
        return False
    if re.search(
        r"\b(?:foto|fotos|imagem|imagens|valor|preco|precos|link|dados|"
        r"informacao|informacoes|detalhes)\b",
        normalized,
    ):
        return False
    if _has_delivery_choice_context(history) and re.search(
        r"\b(?:receb\w*|entreg\w*|envio|envi\w*)\b",
        normalized,
    ):
        return True
    if not _has_delivery_context(history):
        return False
    return bool(
        re.search(
            r"\b(?:conseguem?|podem?|poderiam?)\s+(?:me\s+)?(?:mandar|enviar)\b",
            normalized,
        )
    )

def _is_delivery_payment_question(
    text: str,
) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    has_delivery_context = bool(
        re.search(r"\b(?:entreg\w*|motoboy|sedex|frete|envio|envi\w*)\b", normalized)
    )
    has_payment_context = bool(
        re.search(
            r"\b(?:pagamento|pagar|pago|parcelad\w*|parcelamento|pix|dinheiro|"
            r"debito|credito|cartao)\b",
            normalized,
        )
    )
    return has_delivery_context and has_payment_context

def _is_delivery_quote_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    normalized = _normalize(text)
    if not normalized or not history:
        return False

    latest_assistant_reply = next(
        (
            _normalize(entry.get("content", ""))
            for entry in reversed(history)
            if entry.get("role") == "assistant" and entry.get("content")
        ),
        "",
    )
    if not (
        "qual e seu bairro ou cep" in latest_assistant_reply
        and "taxa" in latest_assistant_reply
        and "prazo" in latest_assistant_reply
        and "pagamento" in latest_assistant_reply
        and re.search(r"\b(?:entrega|motoboy|sedex)\b", latest_assistant_reply)
    ):
        return False

    has_postal_code = bool(re.search(r"\b\d{5}-?\d{3}\b", text))
    has_location_statement = bool(
        re.search(
            r"\b(?:(?:moro|resido)\s+(?:em|no|na)|sou\s+de|"
            r"meu\s+(?:bairro|endereco)\s+e)\s+[a-z0-9]+|"
            r"\bbairro\s+(?!(?:qual|e|pode|ser\w*|voce|fica|melhor|atende|"
            r"de|do|dos|da|das|em|no|na|para|pra)\b)[a-z0-9]+|"
            r"\bfica\s+(?:em|no|na)\s+[a-z0-9]+",
            normalized,
        )
    )
    if "?" in text and re.search(r"\bfica\s+(?:em|no|na)\b", normalized):
        has_location_statement = False
    has_street_address = bool(
        re.search(
            r"\b(?:rua|avenida|av|alameda|travessa|praca|endereco)\b"
            r".{0,60}\b\d{1,5}\b",
            normalized,
        )
    )
    if has_postal_code or has_location_statement or has_street_address:
        return True

    candidate = re.sub(
        r"^(?:(?:sim|ok|certo|beleza|obrigad\w*|ola|oi|bom dia|boa tarde|boa noite)"
        r"\b[\s,]*)+",
        "",
        normalized,
    ).strip()
    candidate = re.sub(r"^[,;:\s]+|[,;:!?]+\s*$", "", candidate).strip()
    if not candidate:
        return False
    words = re.findall(r"[a-z0-9]+", candidate)
    if not words or len(words) > 6:
        return False
    unrelated_markers = (
        r"\b(?:sim|nao|ok|certo|beleza|obrigad\w*|ola|oi|pode|ser|tudo|bem|claro|"
        r"perfeito|combinado|confirmo|iphone|ipad|airpods|watch|samsung|motorola|xiaomi|redmi|"
        r"modelo|preco|quanto|qual|como|"
        r"pagamento|pagar|pago|pix|dinheiro|debito|credito|cartao|parcela\w*|vezes|reais?|"
        r"voces|entrega|entreg\w*|motoboy|sedex|frete|taxa|prazo|fica|em|no|na|de|para|pra|"
        r"hoje|amanha|visita|marcar|agendar|remarcar|vou|quero|gostaria|"
        r"consigo|posso|tempo|ir|loja|atendente)\b"
    )
    if re.search(unrelated_markers, candidate):
        return False

    location_only = bool(re.fullmatch(r"[a-z]+(?:[ ,]+[a-z]+){0,3}", candidate))
    if location_only and re.search(r"\b(?:curitiba|pinhais|colombo|araucaria)\b", candidate):
        return True
    if "?" in text or re.fullmatch(r"\d+", normalized):
        return False
    return location_only and len(words) <= 4

def _is_explicit_human_request(text: str) -> bool:
    normalized = _normalize(text)
    return bool(
        re.search(r"\b(?:atendente|humano|pessoa|reclamacao)\b", normalized)
        or "quero falar com" in normalized
        or "falar com um atendente" in normalized
    )

def _delivery_or_pickup_reply(
    faq: FAQStore,
    text: str,
    *,
    force_delivery: bool = False,
) -> str:
    normalized = _normalize(text)
    replies: list[str] = []
    if force_delivery or re.search(r"\b(?:entrega|entregam|entregas)\b", normalized):
        replies.append(
            faq.get("entrega")
            or "Enviamos para Curitiba e região por motoboy. Para fora de Curitiba, enviamos por Sedex."
        )
    if _has_pickup_reference(normalized):
        replies.append(
            faq.get("retirada")
            or "Fazemos retirada na loja com horário marcado. O pagamento é feito na hora da retirada."
        )
    return "\n\n".join(reply for reply in replies if reply)

def _is_visit_unavailability_message(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    cannot_visit = bool(
        re.search(
            r"\bnao\s+(?:consigo|posso)\s+(?:ir|vir|visitar|passar|comparecer)\b"
            r".{0,45}\b(?:loja|estabelecimento)\b"
            r"|\bnao\s+da\s+tempo\b"
            r"|\bsem\s+tempo\s+(?:de|para)\s+(?:ir|vir|visitar|passar|comparecer)\b",
            normalized,
        )
    )
    if not cannot_visit:
        return False
    has_positive_visit_plan = bool(
        re.search(
            r"(?<!nao\s)\b(?:mas\s+)?(?:posso|consigo|vou|gostaria\s+de)\s+"
            r"(?:ir|vir|visitar|passar|comparecer|marcar|agendar)\b"
            r"|\b(?:vou|quero|pretendo)\s+deixar\s+(?:para|pra)\s+"
            r"(?:ir|vir|visitar|passar|comparecer)\b",
            normalized,
        )
    )
    return not has_positive_visit_plan

def _delivery_payment_reply(
    faq: FAQStore,
    *,
    acknowledge_cannot_visit: bool = False,
) -> str:
    delivery = faq.get("entrega") or (
        "Enviamos para Curitiba e região por motoboy. Para fora de Curitiba, enviamos por Sedex."
    )
    delivery = re.sub(
        r"\s*O pagamento deve ser antecipado antes do despacho\.?",
        "",
        delivery,
        flags=re.IGNORECASE,
    )
    delivery = re.sub(
        r"\s*Taxa e prazo devem ser cotados com um atendente\.?",
        "",
        delivery,
        flags=re.IGNORECASE,
    ).strip()
    payment = faq.get("pagamento") or PAYMENT_METHODS_REPLY
    acknowledgement = (
        "Entendi que você não consegue vir até a loja. " if acknowledge_cannot_visit else ""
    )
    return (
        f"{acknowledgement}{delivery} "
        "Para envio, o pagamento deve ser antecipado antes do despacho; não é possível pagar na entrega. "
        f"{payment} Não aceitamos pagamento por cartão de crédito online ou link. "
        "Um atendente precisa confirmar a taxa e o prazo pelo endereço e orientar "
        "como concluir o pagamento antes do envio. Qual é seu bairro ou CEP?"
    )

def _extract_time_reference(text: str) -> tuple[int, str] | None:
    normalized = _normalize(text)
    patterns = (
        r"\b(?P<hour>[01]?\d|2[0-3])\s*:\s*(?P<minute>[0-5]\d)\b",
        r"\b(?P<hour>[01]?\d|2[0-3])\s*h\s*(?P<minute>[0-5]\d)?\b",
        r"\b(?P<hour>[01]?\d|2[0-3])\s+horas?\b",
        r"\bas\s*(?P<hour>[01]?\d|2[0-3])\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        hour = int(match.group("hour"))
        minute = int(match.groupdict().get("minute") or 0)
        return hour * 60 + minute, f"{hour:02d}:{minute:02d}"
    return None

def _pickup_schedule_reply(faq: FAQStore, text: str) -> str:
    normalized = _normalize(text)
    day_names = {
        "segunda": ("segunda-feira", "Na"),
        "terca": ("terça-feira", "Na"),
        "quarta": ("quarta-feira", "Na"),
        "quinta": ("quinta-feira", "Na"),
        "sexta": ("sexta-feira", "Na"),
        "sabado": ("sábado", "No"),
        "domingo": ("domingo", "No"),
    }
    day_match = re.search(
        r"\b(segunda(?:-feira)?|terca(?:-feira)?|quarta(?:-feira)?|"
        r"quinta(?:-feira)?|sexta(?:-feira)?|sabado|domingo)\b",
        normalized,
    )
    day_key = day_match.group(1).split("-")[0] if day_match else None
    day_label, day_preposition = day_names.get(day_key, ("dia informado", "No"))
    pickup = "Fazemos retirada na loja com horário marcado."
    hours = faq.get("hours") or (
        "Atendemos de segunda a sexta, das 09:00 às 18:00. "
        "Aos sábados, domingos e feriados, a loja fica fechada."
    )

    if day_key in {"sabado", "domingo"}:
        detail = f"{day_preposition} {day_label}, a loja fica fechada para atendimento e retirada."
    else:
        time_reference = _extract_time_reference(text)
        if time_reference is None:
            detail = (
                "A retirada precisa ocorrer dentro do expediente e ter o horário confirmado "
                "por um atendente."
            )
        else:
            minutes, time_label = time_reference
            after_time = bool(re.search(r"\b(?:depois|apos)\b.{0,16}\b\d", normalized))
            if minutes < 9 * 60:
                detail = (
                    f"{day_preposition} {day_label}, {time_label} é antes da abertura às 09:00; "
                    "um atendente pode confirmar outro horário."
                )
            elif minutes >= 18 * 60:
                detail = (
                    f"{day_preposition} {day_label}, o expediente termina às 18:00, então "
                    f"{time_label} fica no limite ou após o fechamento e precisa ser confirmado "
                    "por um atendente."
                )
            elif after_time:
                detail = (
                    f"{day_preposition} {day_label}, depois das {time_label} a retirada só pode "
                    "ser solicitada até o fechamento, às 18:00, e depende da confirmação de "
                    "um atendente."
                )
            else:
                detail = (
                    f"{day_preposition} {day_label}, às {time_label} fica dentro do expediente, "
                    "mas a retirada precisa de confirmação de um atendente."
                )

    return f"{pickup} {hours} {detail}"

def _is_current_date_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    phrases = (
        "que dia e hoje",
        "qual dia e hoje",
        "que data e hoje",
        "qual a data de hoje",
        "hoje e que dia",
    )
    return any(phrase in normalized for phrase in phrases)

def _is_today_store_status_request(text: str) -> bool:
    normalized = re.sub(r"\bhj\b", "hoje", _normalize(text))
    has_status_marker = bool(
        re.search(r"\b(?:abert\w*|fechad\w*|atend\w*)\b", normalized)
        or re.search(
            r"\b(?:voce|voces|vcs|a loja|loja)\s+"
            r"(?:(?:esta|estao)\s+)?(?:trabalh|funcion|atend)\w*\b",
            normalized,
        )
        or re.search(
            r"\b(?:trabalha|trabalham|funciona|funcionam|atende|atendem)\s+hoje\b",
            normalized,
        )
    ) or any(
        marker in normalized
        for marker in (
            "funcionamento",
            "horario",
            "horas",
            "abre",
            "fecha",
        )
    )
    if not has_status_marker:
        return False
    # A weekday-specific hours question remains the user's intent even when
    # "hoje" appears in a separate phrase such as "hoje não consigo".
    if _is_store_day_hours_request(text):
        return False
    if "hoje" in normalized:
        return True
    # In WhatsApp, "a loja está aberta?" commonly omits "hoje" but still
    # asks for the current-day status. Keep hour questions on the FAQ path.
    if _is_store_hours_request(text):
        return False
    implicit_open_status = re.fullmatch(
        r"(?:(?:voces|vcs)\s+)?(?:estao|estamos)\s+abert\w*",
        normalized.strip(" ?!.,"),
    )
    return bool(
        (
            "loja" in normalized
            and re.search(r"\b(?:abert\w*|fechad\w*)\b", normalized)
        )
        or implicit_open_status
    )

def _is_store_day_hours_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    has_day = bool(
        re.search(
            r"\b(?:segunda(?:-feira)?|terca(?:-feira)?|quarta(?:-feira)?|"
            r"quinta(?:-feira)?|sexta(?:-feira)?|sabado|domingo)\b",
            normalized,
        )
    )
    has_hours_marker = bool(
        re.search(r"\b(?:abr\w*|fechad\w*|atend\w*|funcion\w*|trabalh\w*)\b", normalized)
    ) or any(marker in normalized for marker in ("horario", "horas"))
    return has_day and has_hours_marker

def _is_store_hours_request(text: str) -> bool:
    normalized = _normalize(text)
    normalized = re.sub(r"\bq\b", "que", normalized)
    normalized = re.sub(r"\bhrs?\b", "horas", normalized)
    if not normalized:
        return False
    if _is_store_day_hours_request(text):
        return True
    if "hoje" in normalized:
        return False
    phrases = (
        "ate que horas",
        "ate que horario",
        "qual o horario",
        "qual horario",
        "horario de atendimento",
        "horario de funcionamento",
        "que horas voces atendem",
        "que horario voces atendem",
        "quando voces atendem",
    )
    return any(phrase in normalized for phrase in phrases) or (
        "horario" in normalized and "atendimento" in normalized
    )

def _is_visit_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or _is_visit_unavailability_message(text):
        return False
    phrases = (
        "marcar um horario",
        "marcar horario",
        "marcar uma visita",
        "marcar visita",
        "agendar",
        "agendamento",
        "agenda pra mim",
        "agenda para mim",
        "marque pra mim",
        "marque para mim",
        "reservar um horario",
        "deixar um horario marcado",
        "visitar a loja",
        "visitar o escritorio",
        "ir na loja",
        "ir ate a loja",
        "ir ao escritorio",
        "passar na loja",
        "comparecer",
        "que dia posso ir",
        "ir hoje",
        "visitar hoje",
        "passar hoje",
        "comparecer hoje",
    )
    return any(phrase in normalized for phrase in phrases) or bool(
        re.search(
            r"\b(?:posso|consigo|da para|da pra|gostaria de)\s+"
            r"(?:ir|visitar|comparecer)\b",
            normalized,
        )
        or re.search(
            r"\b(?:posso|consigo|da para|da pra|gostaria de)\s+passar\s+"
            r"(?:(?:na|pela)\s+loja(?:\s+fisica)?|ai|aqui|amanha|hoje)\b",
            normalized,
        )
        or re.search(
            r"\b(?:posso|consigo)\s+(?:te\s+)?entreg\w*\b.{0,60}\bhoje\b",
            normalized,
        )
        or (
            re.search(r"\bmarcar\b", normalized)
            and (
                _has_visit_date_reference(text)
                or _has_visit_time_reference(text)
            )
        )
    )

def _is_reservation_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    phrases = (
        "reserva",
        "reservar",
        "segurar",
        "separar",
        "deixar separado",
        "guardar o aparelho",
        "guardar ele",
    )
    return any(phrase in normalized for phrase in phrases)

def _has_visit_date_reference(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    date_markers = (
        "segunda",
        "terca",
        "quarta",
        "quinta",
        "sexta",
        "sabado",
        "domingo",
        "amanha",
        "hoje",
        "proxima semana",
        "esta semana",
    )
    return (
        any(marker in normalized for marker in date_markers)
        or bool(re.search(r"\bhj\b", normalized))
        or bool(re.search(r"\bdia\s+\d{1,2}\b", normalized))
        or bool(re.search(r"\b(?:0?[1-9]|[12]\d|3[01])/(?:0?[1-9]|1[0-2])(?:/\d{4})?\b", normalized))
    )

def _has_visit_time_reference(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    return bool(
        re.search(
            r"\b(?:as)\s*(?:[01]?\d|2[0-3])(?:\s*(?::|h)\s*[0-5]\d)?\s*(?:h|horas)?\b",
            normalized,
        )
        or re.search(r"\b(?:[01]?\d|2[0-3])\s*(?::\s*[0-5]\d|h(?:\s*[0-5]\d)?|horas)\b", normalized)
    )

def _visit_time_for_request(text: str) -> str | None:
    if not _has_visit_time_reference(text):
        return None
    normalized = _normalize(text)
    patterns = (
        r"\b(?P<hour>[01]?\d|2[0-3])\s*:\s*(?P<minute>[0-5]\d)\b",
        r"\b(?P<hour>[01]?\d|2[0-3])\s*h\s*(?P<minute>[0-5]\d)?\b",
        r"\b(?P<hour>[01]?\d|2[0-3])\s*horas?\b",
        r"\bas\s*(?P<hour>[01]?\d|2[0-3])\b",
    )
    for pattern in patterns:
        matches = list(re.finditer(pattern, normalized))
        if matches:
            match = matches[-1]
            hour = int(match.group("hour"))
            minute = match.groupdict().get("minute") or "00"
            return f"{hour:02d}:{minute}"
    return None

def _has_appointment_prompt(history: list[dict[str, str]] | None) -> bool:
    return any(
        entry.get("role") == "assistant"
        and any(
            marker in _normalize(entry.get("content", ""))
            for marker in ("qual dia", "que dia", "qual horario", "que horario")
        )
        for entry in (history or [])
    )

def _has_today_visit_offer(history: list[dict[str, str]] | None) -> bool:
    return any(
        entry.get("role") == "assistant"
        and "visita para hoje" in _normalize(entry.get("content", ""))
        for entry in (history or [])
    )

def _latest_appointment_date_offer(
    history: list[dict[str, str]] | None,
) -> str | None:
    last_assistant = next(
        (
            entry
            for entry in reversed(history or [])
            if entry.get("role") == "assistant"
        ),
        None,
    )
    if last_assistant is None:
        return None

    content = last_assistant.get("content", "")
    offer = re.search(
        r"\b(?:marcar|solicitar)\s+(?:sua\s+|uma\s+)?visita\s+para\s+([^,\r\n.!?]+)",
        content,
        re.IGNORECASE,
    )
    if (
        not _has_appointment_prompt([last_assistant])
        or offer is None
        or not _has_visit_date_reference(offer.group(1))
    ):
        return None
    return offer.group(1)

def _has_unanswered_reservation_request(history: list[dict[str, str]] | None) -> bool:
    entries = history or []
    last_user_index = next(
        (index for index in range(len(entries) - 1, -1, -1) if entries[index].get("role") == "user"),
        None,
    )
    if last_user_index is None or not _is_reservation_request(entries[last_user_index].get("content", "")):
        return False

    last_assistant = next(
        (
            entry
            for entry in reversed(entries[last_user_index + 1 :])
            if entry.get("role") == "assistant"
        ),
        None,
    )
    if last_assistant is None:
        return False

    answer = _normalize(last_assistant.get("content", ""))
    if "nao trabalhamos com reserva" in answer:
        return False
    return _has_appointment_prompt([last_assistant]) or "visita para hoje" in answer

def _is_appointment_followup(text: str, history: list[dict[str, str]] | None) -> bool:
    """Use appointment history only when the current message looks like a reply to it."""
    if not _has_appointment_prompt(history):
        return False

    normalized = _normalize(text)
    if not normalized or _is_visit_unavailability_message(text):
        return False

    # A new request must win over an old appointment prompt.
    if (
        _is_current_date_request(text)
        or _is_today_store_status_request(text)
        or _is_available_list_request(text)
        or _is_sealed_catalog_list_request(text)
        or _is_product_availability_request(text)
        or _is_store_hours_request(text)
        or _is_physical_store_request(text)
    ):
        return False

    if _has_visit_date_reference(text) or _has_visit_time_reference(text):
        return True

    short_reply = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    short_reply = re.sub(r"\s+", " ", short_reply).strip()
    is_now_availability_reply = bool(
        re.fullmatch(
            r"(?:estou|estamos) dispon(?:ivel|iveis) (?:agora|agr)",
            short_reply,
        )
    )
    return is_now_availability_reply or short_reply in {
        "sim",
        "pode",
        "pode ser",
        "ok",
        "okay",
        "beleza",
        "esse horario",
        "esse horario serve",
        "esse horario esta bom",
        "agora tem como",
    }

def _is_handoff_confirmation(text: str, history: list[dict[str, str]] | None) -> bool:
    """Treat a short affirmative as human handoff only after an explicit offer."""
    normalized = re.sub(r"[^\w\s]", " ", _normalize(text), flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized not in {
        "sim",
        "sim pfv",
        "sim por favor",
        "pode",
        "pode sim",
        "pode ser",
        "claro",
        "ok",
        "okay",
        "beleza",
        "blz",
        "blz pode ser",
        "beleza pode ser",
    }:
        return False

    offer_markers = (
        "posso encaminhar",
        "vou encaminhar",
        "encaminhar seu pedido",
        "falar com um atendente",
        "atendente finalizar",
        "atendente confirmar",
    )
    latest_assistant_reply = next(
        (
            entry.get("content", "")
            for entry in reversed(history or [])
            if entry.get("role") == "assistant" and entry.get("content", "").strip()
        ),
        "",
    )
    normalized_latest_reply = _normalize(latest_assistant_reply)
    return any(marker in normalized_latest_reply for marker in offer_markers)

def _appointment_context(text: str, history: list[dict[str, str]] | None) -> str:
    previous_user_text = [
        entry.get("content", "").strip()
        for entry in (history or [])
        if entry.get("role") == "user" and entry.get("content", "").strip()
        and not _is_today_store_status_request(entry.get("content", ""))
    ]
    context_parts = [*previous_user_text[-4:]]
    latest_offer = _latest_appointment_date_offer(history)
    if latest_offer:
        context_parts.insert(0, latest_offer)
    return "\n".join([*context_parts, text.strip()]).strip()

def _store_address(faq: FAQStore) -> str:
    return faq.get("address") or (
        "Avenida Nossa Senhora da Luz, 1341 - Jardim Social, Curitiba - PR, 82520-060"
    )

def _store_hours(faq: FAQStore) -> str:
    return faq.get("hours") or faq.get("horario") or (
        "Atendemos de segunda a sexta, das 09:00 às 18:00. "
        "Aos sábados, domingos e feriados, a loja fica fechada."
    )

def _store_hours_reply(faq: FAQStore) -> str:
    hours = _store_hours(faq)
    if "marcad" in _normalize(hours):
        return hours
    return f"{hours} O atendimento \u00e9 feito com hor\u00e1rio marcado."

def _today_store_reply(faq: FAQStore, *, include_physical_store: bool = True) -> str:
    current = _store_now()
    if _is_business_weekday(current) and current.hour >= 18:
        return _tomorrow_store_reply(faq, include_physical_store=include_physical_store)
    today = _today_label(current)
    address = _store_address(faq)
    if _is_business_weekday(current):
        intro = "Sim, temos loja física. " if include_physical_store else ""
        return (
            f"{intro}Hoje é {today}. Atendemos hoje das 09:00 às 18:00, com horário marcado. "
            f"Endereço: {address}. Posso marcar uma visita para hoje? "
            "Qual horário fica melhor para você?"
        )
    intro = "Sim, temos loja física. " if include_physical_store else ""
    return (
        f"{intro}Hoje é {today}. Hoje a loja está fechada. {_store_hours(faq)} "
        f"Endereço: {address}. O atendimento é feito com horário marcado. "
        "Podemos marcar uma visita em um dia de atendimento. Qual dia e horário ficam "
        "melhores para você?"
    )

def _tomorrow_store_reply(faq: FAQStore, *, include_physical_store: bool = True) -> str:
    current = _store_now()
    tomorrow = current + timedelta(days=1)
    tomorrow_label = _today_label(tomorrow)
    address = _store_address(faq)
    intro = "Sim, temos loja física. " if include_physical_store else ""
    if _is_business_weekday(tomorrow):
        return (
            f"{intro}Perfeito 😊 Podemos marcar sua visita para amanhã, {tomorrow_label}, "
            "das 09:00 às 18:00, com horário marcado. "
            f"Endereço: {address}. Qual horário fica melhor para você?"
        )
    return (
        f"{intro}Amanhã será {tomorrow_label} e a loja estará fechada. {_store_hours(faq)} "
        f"Endereço: {address}. O atendimento é feito com horário marcado. "
        "Podemos marcar uma visita em um dia de atendimento. Qual dia e horário ficam "
        "melhores para você?"
    )

def _visit_date_for_request(
    text: str,
    now: datetime | None = None,
) -> datetime | None:
    normalized = _normalize(text)
    current = now or _store_now()
    if re.search(r"\b(?:hoje|hj)\b", normalized):
        return current
    if "amanha" in normalized:
        return current + timedelta(days=1)

    numeric_date = re.search(
        r"\b(?P<day>0?[1-9]|[12]\d|3[01])/(?P<month>0?[1-9]|1[0-2])"
        r"(?:/(?P<year>\d{4}))?\b",
        normalized,
    )
    if numeric_date is not None:
        day = int(numeric_date.group("day"))
        month = int(numeric_date.group("month"))
        explicit_year = numeric_date.group("year")
        years = [int(explicit_year)] if explicit_year else range(current.year, current.year + 5)
        for year in years:
            try:
                visit_day = current.replace(
                    year=year,
                    month=month,
                    day=day,
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
            except ValueError:
                continue
            if explicit_year or visit_day.date() >= current.date():
                return visit_day
        return None

    match = re.search(
        r"\b(segunda(?:[- ]feira)?|terca(?:[- ]feira)?|quarta(?:[- ]feira)?|"
        r"quinta(?:[- ]feira)?|sexta(?:[- ]feira)?|sabado|domingo)\b",
        normalized,
    )
    if not match:
        return None

    weekday = match.group(1).split("-")[0].split(" ")[0]
    weekday_numbers = {
        "segunda": 0,
        "terca": 1,
        "quarta": 2,
        "quinta": 3,
        "sexta": 4,
        "sabado": 5,
        "domingo": 6,
    }
    if "proxima semana" in normalized or "semana que vem" in normalized:
        days_ahead = 7 - current.weekday() + weekday_numbers[weekday]
    else:
        days_ahead = (weekday_numbers[weekday] - current.weekday()) % 7
    return current + timedelta(days=days_ahead)

def _visit_day_reply(visit_day: datetime) -> str:
    day_label = _today_label(visit_day)
    if _is_business_weekday(visit_day):
        return (
            f"Entendi 😊 Podemos marcar sua visita para {day_label}, das 09:00 às 18:00, "
            "com horário marcado. Qual horário fica melhor para você?"
        )
    return (
        f"A loja fica fechada no {day_label}. Podemos solicitar sua visita em um dia de "
        "atendimento, com horário marcado. Qual dia e horário ficam melhores para você?"
    )

def _reservation_policy(faq: FAQStore) -> str:
    return faq.get("reserva") or (
        "Não trabalhamos com reserva de aparelhos porque alguns clientes reservam "
        "e depois cancelam, e nesse período deixamos de vender o aparelho para outras pessoas."
    )

def _reservation_reply(faq: FAQStore, visit_day: datetime | None = None) -> str:
    reason = _reservation_policy(faq)
    if visit_day is not None:
        return (
            f"{reason} Não consigo garantir que o aparelho continue disponível até a visita. "
            f"{_visit_day_reply(visit_day)}"
        )

    current = _store_now()
    if _is_business_weekday(current):
        if current.hour >= 18:
            return (
                f"{reason} Hoje é {_today_label(current)} e a loja já encerrou o atendimento. "
                f"{_tomorrow_store_reply(faq, include_physical_store=False)}"
            )
        return (
            f"{reason} Hoje é {_today_label(current)}. Podemos marcar sua visita para hoje, "
            f"com horário marcado, no endereço {_store_address(faq)}. Qual horário fica melhor para você?"
        )
    return (
        f"{reason} Hoje é {_today_label(current)} e a loja está fechada. "
        "Podemos marcar uma visita em um dia de atendimento, com horário marcado. "
        "Qual dia e horário ficam melhores para você?"
    )
