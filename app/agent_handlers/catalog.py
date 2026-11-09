"""Catalog response handlers for AgentService."""

from __future__ import annotations

from app.agent import (
    AgentDecision,
    CASE_ACCESSORY_REPLY,
    _CATALOG_CONTEXT_ACKNOWLEDGEMENT_RE,
    _availability_catalog_query,
    _bare_model_selection_reply,
    _battery_detail_context_query,
    _capacity_key,
    _capacity_list_no_match_reply,
    _catalog_battery_unit_selection_query,
    _catalog_color_map,
    _catalog_families,
    _catalog_family,
    _catalog_score,
    _confirmed_catalog_price,
    _current_catalog_context,
    _customer_product_condition_context_query,
    _customer_product_request_context_query,
    _extract_bare_catalog_model_reference,
    _extract_catalog_product_id,
    _extract_entry_amount,
    _extract_installment_budget,
    _extract_latest_budget_limit,
    _format_available_products,
    _format_battery,
    _format_product_availability,
    _has_explicit_sealed_condition,
    _has_installment_product_context,
    _has_product_reference,
    _has_recent_catalog_product_context,
    _has_requested_catalog_color,
    _has_sealed_reference,
    _has_seminovo_reference,
    _is_accessory_catalog_request,
    _is_accessory_inclusion_question,
    _is_available_item,
    _is_available_list_request,
    _is_bare_model_availability_request,
    _is_bare_model_catalog_followup,
    _is_battery_detail_request,
    _is_broad_airpods_request,
    _is_capacity_availability_followup,
    _is_case_accessory_request,
    _is_catalog_availability_confirmation,
    _is_catalog_color_either_or_request,
    _is_catalog_color_list_request,
    _is_catalog_followup,
    _is_catalog_model_confirmation,
    _is_catalog_price_followup,
    _is_cheapest_catalog_request,
    _is_contextual_pronta_entrega_followup,
    _is_device_item,
    _is_all_iphone_17_line_request,
    _is_generic_iphone_list_request,
    _is_image_history_entry,
    _is_installment_budget_options_request,
    _is_ipad_model_alternative_price_followup,
    _is_new_model_list_request,
    _is_other_color_request,
    _is_photo_context_followup,
    _is_product_availability_request,
    _is_pronta_entrega_only_request,
    _is_sealed_accessory_item,
    _is_sealed_catalog_list_request,
    _is_sealed_iphone_list_request,
    _is_sealed_item,
    _is_variant_catalog_list_request,
    _item_matches_catalog_colors,
    _latest_catalog_color_request,
    _matches_requested_apple_watch_model,
    _matches_requested_model,
    _model_key,
    _normalize,
    _pending_iphone_variant_catalog_context,
    _pending_multi_model_product_request_context,
    _product_context_query,
    _product_interest_model_context,
    _requested_apple_watch_model_keys,
    _requested_battery_health,
    _requested_capacity_keys,
    _requested_catalog_colors,
    _requested_device_quantity,
    _requested_ipad_model_keys,
    _requested_iphone_model_floor,
    _requested_iphone_model_keys,
    _requested_model_attribute_scopes,
    _requested_model_segments,
    _seminovo_trade_in_catalog_target_query,
    _sort_product_availability_items,
    _strip_catalog_color_references,
    _trailing_model_capacity_scope,
    _unavailable_catalog_color_header,
    _visual_catalog_search_query,
    _visual_iphone_model_segments,
    catalog_price_recall_amount,
    format_brl,
    is_photo_offer_confirmation,
    re,
    simulate_installment,
    simulate_installment_with_entry,
)

async def _try_available_products(self, text: str) -> AgentDecision | None:
    variant_list_request = _is_variant_catalog_list_request(text)
    sealed_iphone_only = _is_sealed_iphone_list_request(text)
    sealed_only = _is_sealed_catalog_list_request(text) or sealed_iphone_only
    if not (_is_available_list_request(text) or sealed_only or sealed_iphone_only):
        return None
    method = getattr(self.cache, "list_available_products", None)
    if not callable(method):
        return None
    try:
        result = await method()
    except Exception:
        return None
    if not result.get("encontrado"):
        return None
    if sealed_only:
        lacrados_pronta_entrega = result.get("lacrados_pronta_entrega") or []
        lacrados = result.get("lacrados") or []
        if not lacrados_pronta_entrega and not lacrados:
            capacity_no_match = _capacity_list_no_match_reply(text, sealed_only=True)
            if capacity_no_match:
                return AgentDecision(reply=capacity_no_match, confidence="medium")
            if _is_new_model_list_request(text):
                return AgentDecision(
                    reply=(
                        "No momento, não localizei modelos novos/lacrados disponíveis no catálogo. "
                        "Se quiser, posso verificar um modelo específico ou outra capacidade."
                    ),
                    confidence="medium",
                )
            return None
        result = {
            "encontrado": True,
            "seminovos": [],
            "lacrados_pronta_entrega": lacrados_pronta_entrega,
            "lacrados": lacrados,
        }
    if _is_pronta_entrega_only_request(text):
        result = {**result, "lacrados": []}
    if variant_list_request:
        normalized = _normalize(text)

        def is_requested_variant(entry: dict[str, Any]) -> bool:
            model = _model_key(entry.get("nome", ""))
            return model is not None and model[1] == "pro max"

        result = {
            **result,
            "seminovos": [
                entry for entry in result.get("seminovos", []) if is_requested_variant(entry)
            ],
            "lacrados_pronta_entrega": [
                entry
                for entry in result.get("lacrados_pronta_entrega", [])
                if is_requested_variant(entry)
            ],
            "lacrados": [
                entry for entry in result.get("lacrados", []) if is_requested_variant(entry)
            ],
        }
        if _has_seminovo_reference(normalized) and not _has_sealed_reference(normalized):
            result["lacrados_pronta_entrega"] = []
            result["lacrados"] = []
        elif _has_sealed_reference(normalized) and not _has_seminovo_reference(normalized):
            result["seminovos"] = []
    if _is_generic_iphone_list_request(text) or sealed_iphone_only:
        normalized = _normalize(text)
        other_family_requested = bool(
            re.search(r"\b(?:ipads?|mac(?:books?)?|airpods?|apple\s+watch)\b", normalized)
        )
        if not other_family_requested:
            def is_iphone_entry(entry: dict[str, Any]) -> bool:
                entry_text = " ".join(
                    str(entry.get(field) or "")
                    for field in ("nome", "capacidade", "cor", "condicao")
                )
                return _catalog_family(entry_text) == "iphone"

            result = {
                **result,
                "seminovos": [
                    entry for entry in result.get("seminovos", []) if is_iphone_entry(entry)
                ],
                "lacrados_pronta_entrega": [
                    entry
                    for entry in result.get("lacrados_pronta_entrega", [])
                    if is_iphone_entry(entry)
                ],
                "lacrados": [
                    entry for entry in result.get("lacrados", []) if is_iphone_entry(entry)
                ],
            }

    if _is_all_iphone_17_line_request(text):
        def is_iphone_17_line_entry(entry: dict[str, Any]) -> bool:
            entry_text = " ".join(
                str(entry.get(field) or "")
                for field in ("nome", "capacidade", "cor", "condicao")
            )
            return any(
                generation == 17
                for generation, _variant in _requested_iphone_model_keys(entry_text)
                if isinstance(generation, int)
            )

        result = {
            **result,
            "seminovos": [
                entry for entry in result.get("seminovos", [])
                if is_iphone_17_line_entry(entry)
            ],
            "lacrados_pronta_entrega": [
                entry for entry in result.get("lacrados_pronta_entrega", [])
                if is_iphone_17_line_entry(entry)
            ],
            "lacrados": [
                entry for entry in result.get("lacrados", [])
                if is_iphone_17_line_entry(entry)
            ],
        }
    requested_capacity_keys = _requested_capacity_keys(text)
    requested_capacities = set(requested_capacity_keys)
    if requested_capacities:
        def has_requested_capacity(entry: dict[str, Any]) -> bool:
            capacities = _requested_capacity_keys(
                str(entry.get("capacidade") or entry.get("nome") or "")
            )
            return bool(requested_capacities.intersection(capacities))

        result = {
            **result,
            "seminovos": [
                entry for entry in result.get("seminovos", []) if has_requested_capacity(entry)
            ],
            "lacrados_pronta_entrega": [
                entry
                for entry in result.get("lacrados_pronta_entrega", [])
                if has_requested_capacity(entry)
            ],
            "lacrados": [
                entry for entry in result.get("lacrados", []) if has_requested_capacity(entry)
            ],
        }
        is_category_iphone_list = (
            _is_generic_iphone_list_request(text) or sealed_iphone_only
        )
        if is_category_iphone_list and not any(
            result.get(key)
            for key in ("seminovos", "lacrados_pronta_entrega", "lacrados")
        ):
            capacity_no_match = _capacity_list_no_match_reply(text, sealed_only=sealed_only)
            if capacity_no_match:
                return AgentDecision(reply=capacity_no_match, confidence="medium")
    return AgentDecision(reply=_format_available_products(result), confidence="high")

def _try_case_accessory_information(self, text: str) -> AgentDecision | None:
    if not _is_case_accessory_request(text):
        return None
    return AgentDecision(
        reply=self.faq.get("capinhas") or CASE_ACCESSORY_REPLY,
        confidence="high",
    )

def _try_accessory_inclusion_information(
    self,
    text: str,
    history: list[dict[str, str]] | None = None,
) -> AgentDecision | None:
    if not _is_accessory_inclusion_question(text):
        return None

    normalized = _normalize(text)
    if not _has_sealed_reference(normalized) and not _has_seminovo_reference(normalized):
        for entry in reversed(history or []):
            if entry.get("role") != "assistant":
                continue
            context = _normalize(entry.get("content", ""))
            if not _has_product_reference(context) or not re.search(r"\br\$\s*\d", context):
                continue
            has_sealed_condition = _has_sealed_reference(context)
            has_seminovo_condition = _has_seminovo_reference(context)
            if has_sealed_condition != has_seminovo_condition:
                normalized = context
                break

    if _has_sealed_reference(normalized) and not _has_seminovo_reference(normalized):
        reply = self.faq.get("lacrados")
    elif _has_seminovo_reference(normalized) and not _has_sealed_reference(normalized):
        reply = self.faq.get("seminovos")
    else:
        reply = self.faq.get("acessórios")
    return AgentDecision(
        reply=reply
        or (
            "Aparelhos seminovos acompanham cabo e fonte novos, homologados pela Anatel. "
            "Aparelhos novos lacrados acompanham apenas o cabo original que já vem dentro da caixa."
        ),
        confidence="high",
    )

async def _try_battery_detail(
    self,
    text: str,
    history: list[dict[str, str]] | None,
) -> AgentDecision | None:
    if not _is_battery_detail_request(text) or _is_product_availability_request(
        text,
        history=history,
    ):
        return None
    query = _battery_detail_context_query(text, history)
    if not query:
        return None
    try:
        candidates = await self.cache.search(query, limit=300)
    except Exception:
        return None

    candidates = [
        item
        for item in candidates
        if _is_device_item(item)
        and (getattr(item, "source", None) != "mercado_phone" or _is_available_item(item))
        and not _is_sealed_item(item)
    ]
    requested_health = _requested_battery_health(query)
    if requested_health is not None:
        candidates = [
            item
            for item in candidates
            if _requested_battery_health(
                f"bateria {getattr(item, 'battery_health', '')}%"
            )
            == requested_health
        ]
    if not candidates:
        alternative = await self._try_unavailable_seminew_alternative(
            query,
            requested_label=(
                _extract_bare_catalog_model_reference(text)
                or _extract_bare_catalog_model_reference(query)
            ),
        )
        if alternative is not None:
            return alternative
        requested_label = (
            _extract_bare_catalog_model_reference(text)
            or _extract_bare_catalog_model_reference(query)
            or "esse aparelho"
        )
        return AgentDecision(
            reply=(
                f"Não localizei o {requested_label} disponível no estoque no momento. "
                "Não consigo confirmar a saúde da bateria sem uma unidade cadastrada."
            ),
            confidence="medium",
        )

    if requested_health is not None:
        best_score = max(_catalog_score(query, item) for item in candidates)
        selected_candidates = [
            item for item in candidates if _catalog_score(query, item) == best_score
        ]
    else:
        selected = max(
            candidates,
            key=lambda item: (
                _catalog_score(query, item),
                _normalize(str(getattr(item, "name", "") or "")),
                _normalize(str(getattr(item, "capacity", "") or "")),
            ),
        )
        selected_candidates = [selected]
    selected = selected_candidates[0]
    label_parts = [str(getattr(selected, "name", "produto") or "produto")]
    for value in (
        getattr(selected, "capacity", None),
        getattr(selected, "color", None) or getattr(selected, "colors", None),
    ):
        if value and str(value).strip() not in label_parts:
            label_parts.append(str(value).strip())
    label = " ".join(label_parts)
    battery = getattr(selected, "battery_health", None)
    if battery is None:
        battery_reply = "A saúde da bateria não está informada no cadastro."
    else:
        battery_reply = f"A saúde cadastrada da bateria é {_format_battery(battery)}."
    if len(selected_candidates) > 1 and requested_health is not None:
        label = (
            f"as {len(selected_candidates)} opções de {label} com bateria "
            f"{_format_battery(requested_health)}"
        )
        originality_reply = (
            "O cadastro não informa se as baterias são originais ou se já foram "
            "trocadas, então não consigo confirmar essa parte por aqui."
        )
        battery_reply = ""
    else:
        originality_reply = (
            "O cadastro não informa se a bateria é original ou se já foi trocada, "
            "então não consigo confirmar essa parte por aqui."
        )
    return AgentDecision(
        reply=(
            f"Sobre {label}: "
            f"{(battery_reply + ' ') if battery_reply else ''}{originality_reply}"
        ),
        product_references=[
            str(getattr(item, "external_id", "")) for item in selected_candidates
        ],
        confidence="high",
    )

async def _try_catalog_product_reference(
    self,
    text: str,
    *,
    image_description: str | None = None,
) -> AgentDecision | None:
    product_id = _extract_catalog_product_id(_current_catalog_context(text, image_description))
    if product_id is None:
        return None

    method = getattr(self.cache, "get", None)
    if not callable(method):
        return AgentDecision(
            reply="Vou encaminhar esse link do catálogo para um atendente confirmar o aparelho.",
            handoff=True,
            handoff_reason="Consulta do produto do catálogo indisponível",
            confidence="low",
        )

    try:
        item = await method(product_id)
    except Exception:
        return AgentDecision(
            reply="Não consegui consultar esse aparelho agora. Vou encaminhar sua mensagem para um atendente confirmar.",
            handoff=True,
            handoff_reason="Falha ao consultar produto do catálogo",
            confidence="low",
        )

    if item is None:
        return AgentDecision(
            reply="Não localizei o aparelho indicado por esse link do catálogo. Pode confirmar o link ou o código do estoque?",
            confidence="medium",
        )

    return AgentDecision(
        reply=_format_product_availability([item]),
        product_references=[product_id],
        confidence="high",
    )

async def _try_unavailable_lacrado_alternative(
    self,
    query: str,
    *,
    requested_budget: float | None = None,
    condition_query: str | None = None,
) -> AgentDecision | None:
    normalized_query = _normalize(condition_query if condition_query is not None else query)
    if not _has_explicit_sealed_condition(normalized_query):
        return None

    method = getattr(self.cache, "list_available_products", None)
    if not callable(method):
        return None
    try:
        result = await method()
    except Exception:
        return None

    seminovos = result.get("seminovos") or []
    lacrados_pronta_entrega = result.get("lacrados_pronta_entrega") or []
    lacrados = result.get("lacrados") or []
    requested_family = _catalog_family(query)
    requested_models = _requested_iphone_model_keys(query)

    def entry_text(entry: dict[str, Any]) -> str:
        return " ".join(
            str(entry.get(field) or "")
            for field in ("nome", "capacidade", "cor", "condicao")
        )

    def same_family(entry: dict[str, Any]) -> bool:
        return requested_family is None or _catalog_family(entry_text(entry)) == requested_family

    def same_requested_model(entry: dict[str, Any]) -> bool:
        if not requested_models or not same_family(entry):
            return False
        return _catalog_family(entry_text(entry)) == "iphone" and _model_key(
            entry.get("nome", "")
        ) in requested_models

    def within_budget(entry: dict[str, Any]) -> bool:
        if requested_budget is None:
            return True
        prices = entry.get("precos_brl") or []
        if not isinstance(prices, (list, tuple)):
            prices = [prices]
        for price in prices:
            try:
                if float(price) <= requested_budget:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    available_seminovos = [entry for entry in seminovos if same_family(entry) and within_budget(entry)]
    matching_seminovos = [entry for entry in available_seminovos if same_requested_model(entry)]
    other_seminovos = [entry for entry in available_seminovos if not same_requested_model(entry)]
    available_lacrados_pronta_entrega = [
        entry
        for entry in lacrados_pronta_entrega
        if same_family(entry) and within_budget(entry)
    ]
    matching_lacrados_pronta_entrega = [
        entry
        for entry in available_lacrados_pronta_entrega
        if same_requested_model(entry)
    ]
    other_lacrados_pronta_entrega = [
        entry
        for entry in available_lacrados_pronta_entrega
        if not same_requested_model(entry)
    ]
    available_lacrados = [entry for entry in lacrados if same_family(entry) and within_budget(entry)]
    matching_lacrados = [entry for entry in available_lacrados if same_requested_model(entry)]
    other_lacrados = [
        entry for entry in available_lacrados if not same_requested_model(entry)
    ]
    alternatives = {
        "seminovos": [*matching_seminovos, *other_seminovos],
        "lacrados_pronta_entrega": [
            *matching_lacrados_pronta_entrega,
            *other_lacrados_pronta_entrega,
        ],
        "lacrados": [*matching_lacrados, *other_lacrados],
    }
    if not any(alternatives.values()):
        return AgentDecision(
            reply=(
                "N\u00e3o localizei esse modelo novo/lacrado nem na lista de lacrados por encomenda "
                "nem entre os lacrados dispon\u00edveis no estoque. "
                "Tamb\u00e9m n\u00e3o encontrei outra op\u00e7\u00e3o cadastrada para sugerir agora. "
                "Se quiser, me diga outro modelo ou capacidade."
            ),
            confidence="medium",
        )

    return AgentDecision(
        reply=(
            "N\u00e3o localizei esse modelo novo/lacrado nem na lista de lacrados por encomenda "
            "nem entre os lacrados dispon\u00edveis no estoque. "
            "Para voc\u00ea escolher outra op\u00e7\u00e3o, seguem alternativas cadastradas:\n\n"
            + _format_available_products(alternatives)
        ),
        confidence="high",
    )

async def _try_unavailable_seminew_alternative(
    self,
    query: str,
    *,
    requested_label: str | None = None,
) -> AgentDecision | None:
    if not _has_installment_product_context(query):
        return None

    if _has_explicit_sealed_condition(_normalize(query)):
        return None

    method = getattr(self.cache, "list_available_products", None)
    if not callable(method):
        return None
    try:
        result = await method()
    except Exception:
        return None

    requested_families = _catalog_families(query)
    if any(family in {"ipad", "macbook"} for family in requested_families):
        family_labels = {
            "iphone": ("iPhone", "iPhones"),
            "ipad": ("iPad", "iPads"),
            "macbook": ("MacBook", "MacBooks"),
            "airpods": ("AirPods", "AirPods"),
            "apple_watch": ("Apple Watch", "Apple Watches"),
        }
        single_family = len(requested_families) == 1
        family_name, family_plural = (
            family_labels[requested_families[0]] if single_family else (None, None)
        )

        def is_requested_family_entry(entry: dict[str, Any]) -> bool:
            return _catalog_family(str(entry.get("nome") or "")) in requested_families

        family_options = {
            "seminovos": [
                entry
                for entry in result.get("seminovos") or []
                if is_requested_family_entry(entry)
            ],
            "lacrados_pronta_entrega": [
                entry
                for entry in result.get("lacrados_pronta_entrega") or []
                if is_requested_family_entry(entry)
            ],
            "lacrados": [
                entry
                for entry in result.get("lacrados") or []
                if is_requested_family_entry(entry)
            ],
        }
        new_family_available = any(
            family_options[section]
            for section in ("lacrados_pronta_entrega", "lacrados")
        )
        if not any(family_options.values()):
            if single_family:
                reply = (
                    f"No momento, não localizei opções de {family_name} seminovo disponíveis no sistema. "
                    f"Também não encontrei {family_plural} novos disponíveis no catálogo. "
                    "Se quiser, posso verificar outro modelo ou capacidade."
                )
            else:
                reply = (
                    "No momento, não localizei opções seminovas das famílias solicitadas no sistema. "
                    "Também não encontrei produtos novos dessas famílias no catálogo. "
                    "Se quiser, posso verificar outro modelo ou capacidade."
                )
            return AgentDecision(
                reply=reply,
                confidence="medium",
            )

        formatted_family = _format_available_products(family_options).removeprefix(
            "📋 Lista completa de produtos disponíveis:\n"
        )
        if family_options["seminovos"]:
            intro = (
                f"Não localizei a opção de {family_name} seminovo solicitada. "
                f"Encontrei estes {family_plural} seminovos"
                if single_family
                else "Não localizei todas as opções seminovas solicitadas. "
                "Encontrei estes produtos seminovos"
            )
            if new_family_available:
                intro += " e novos disponíveis para você escolher:\n\n"
            else:
                intro += " disponíveis para você escolher:\n\n"
        else:
            intro = (
                f"No momento, não localizei opções de {family_name} seminovo disponíveis no sistema. "
                f"Para você escolher, seguem as opções de {family_plural} novos disponíveis:\n\n"
                if single_family
                else "No momento, não localizei opções seminovas das famílias solicitadas. "
                "Para você escolher, seguem as opções novas disponíveis:\n\n"
            )
        return AgentDecision(
            reply=intro + formatted_family,
            confidence="medium",
        )

    seminovos = result.get("seminovos") or []
    if not seminovos:
        return None
    unavailable_reply = (
        f"No momento, n\u00e3o localizei o {requested_label} dispon\u00edvel no estoque."
        if requested_label
        else "No momento, n\u00e3o localizei esse produto seminovo dispon\u00edvel no sistema."
    )
    return AgentDecision(
        reply=(
            unavailable_reply + " "
            "Algum outro modelo tamb\u00e9m interessaria? Para facilitar, segue a lista "
            "dos seminovos dispon\u00edveis para voc\u00ea escolher:\n\n"
            + _format_available_products({"seminovos": seminovos, "lacrados": []})
        ),
        confidence="medium",
    )

async def _try_catalog_price_recall(self, text: str) -> AgentDecision | None:
    remembered_price = catalog_price_recall_amount(text)
    if remembered_price is None:
        return None

    try:
        candidates = await self.cache.search("iphone", limit=300)
    except Exception:
        return None

    priced_candidates: list[tuple[float, Any]] = []
    for item in candidates:
        if not _is_device_item(item):
            continue
        if getattr(item, "source", None) == "mercado_phone" and not _is_available_item(item):
            continue
        try:
            price = float(getattr(item, "price_brl", None))
        except (TypeError, ValueError):
            continue
        priced_candidates.append((price, item))

    if not priced_candidates:
        return None

    distance, selected = min(
        (
            (abs(price - remembered_price), item)
            for price, item in priced_candidates
        ),
        key=lambda pair: (
            pair[0],
            -_catalog_score("iphone", pair[1]),
            _normalize(str(getattr(pair[1], "name", "") or "")),
        ),
    )
    if distance > max(100.0, remembered_price * 0.10):
        return None

    reference = str(getattr(selected, "external_id", "") or "")
    return AgentDecision(
        reply=_format_product_availability([selected]),
        product_references=[reference] if reference else [],
        confidence="high",
    )

def _is_higher_battery_option_request(text: str) -> bool:
    normalized = _normalize(text)
    return bool(
        re.search(r"\b(?:bateria|saude)\b", normalized)
        and re.search(r"\b(?:mais\s+(?:alta|elevada|boa)|maior|melhor)\b", normalized)
    )

async def _try_product_availability(
    self,
    text: str,
    history: list[dict[str, str]] | None = None,
    *,
    image_description: str | None = None,
    allow_buyer_interest_with_image: bool = False,
) -> AgentDecision | None:
    battery_unit_selection_query = _catalog_battery_unit_selection_query(
        text,
        history,
    )
    pending_variant_catalog_context = _pending_iphone_variant_catalog_context(
        text,
        history,
    )
    ipad_model_alternative_price_followup = _is_ipad_model_alternative_price_followup(
        text,
        history,
    )
    # A short clarification after a photo request is still a photo
    # selection, even when it contains availability wording such as
    # "tem um 11 verde". Let the photo resolver preserve that context.
    capacity_availability_followup = _is_capacity_availability_followup(text, history)
    if (
        (
            _is_photo_context_followup(text, history)
            or is_photo_offer_confirmation(text, history)
        )
        and not (
            _is_catalog_price_followup(text)
            and _has_recent_catalog_product_context(history)
        )
        and not capacity_availability_followup
        and battery_unit_selection_query is None
    ):
        return None
    seminovo_trade_in_query = _seminovo_trade_in_catalog_target_query(text)
    current_query = (
        seminovo_trade_in_query
        or pending_variant_catalog_context
        or _current_catalog_context(text, image_description)
    )
    if battery_unit_selection_query is not None:
        current_query = battery_unit_selection_query
    installment_budget_context = (
        _customer_product_request_context_query(text, history)
        if _is_installment_budget_options_request(text, history)
        else None
    )
    product_interest_context = _product_interest_model_context(text, history)
    selected_model = product_interest_context[0] if product_interest_context else None
    default_condition = product_interest_context[1] if product_interest_context else None
    customer_request_context = installment_budget_context or _customer_product_request_context_query(
        text,
        history,
    )
    current_request_budget = _extract_latest_budget_limit(text)
    is_current_cheapest_request = _is_cheapest_catalog_request(text)
    latest_customer_message = next(
        (
            entry.get("content", "").strip()
            for entry in reversed(history or [])
            if entry.get("role") == "user" and entry.get("content", "").strip()
        ),
        "",
    )
    history_entries = history or []
    latest_customer_anchor_index = max(
        (
            index
            for index, entry in enumerate(history_entries)
            if entry.get("role") == "user"
            and (
                _has_product_reference(_normalize(entry.get("content", "")))
                or _extract_bare_catalog_model_reference(entry.get("content", ""))
            )
        ),
        default=-1,
    )
    newer_generic_catalog_list_index = max(
        (
            index
            for index, entry in enumerate(history_entries)
            if index > latest_customer_anchor_index
            and entry.get("role") == "user"
            and (
                _is_available_list_request(entry.get("content", ""))
                or _is_sealed_catalog_list_request(entry.get("content", ""))
            )
            and _has_recent_catalog_product_context(
                history_entries[index + 1 :],
                skip_acknowledgements=True,
            )
        ),
        default=None,
    )
    newer_generic_catalog_list = newer_generic_catalog_list_index is not None
    current_request_has_catalog_anchor = bool(
        _has_product_reference(_normalize(text))
        or _extract_bare_catalog_model_reference(text)
    )
    customer_affordability_request = is_current_cheapest_request or (
        current_request_budget is not None
        and not _has_product_reference(_normalize(text))
        and not _extract_bare_catalog_model_reference(text)
        and _is_cheapest_catalog_request(latest_customer_message)
    )
    customer_request_has_catalog_anchor = bool(
        _catalog_families(customer_request_context)
        or _has_product_reference(_normalize(customer_request_context))
        or _extract_bare_catalog_model_reference(customer_request_context)
    ) and (not newer_generic_catalog_list or current_request_has_catalog_anchor)
    selection_context_used = battery_unit_selection_query is not None or bool(
        selected_model
        and not _has_product_reference(_normalize(current_query))
        and not is_current_cheapest_request
    )
    if selection_context_used and battery_unit_selection_query is None:
        normalized_text = _normalize(text)
        selected_conditions: list[str] = []
        if _has_seminovo_reference(normalized_text):
            selected_conditions.append("seminovo")
        if _has_explicit_sealed_condition(normalized_text):
            selected_conditions.append("lacrado")
        if not selected_conditions and default_condition:
            selected_conditions.append(default_condition)
        selection_query = " ".join([selected_model, *selected_conditions])
        current_query = (
            selection_query
            if _bare_model_selection_reply(text)
            else "\n".join((selection_query, current_query)).strip()
        )
    if (
        (
            _is_bare_model_availability_request(current_query)
            or _is_bare_model_catalog_followup(current_query, history)
        )
        and len(_requested_iphone_model_keys(current_query)) <= 1
    ):
        # Expand shorthand only for a single model. In a batched request
        # such as "um 13 ou 14", prefixing the first bare number would
        # make the explicit-model parser discard the other alternative.
        bare_model = _extract_bare_catalog_model_reference(current_query)
        if bare_model:
            current_query = f"{bare_model} {current_query}".strip()
    query = current_query
    if installment_budget_context:
        query = installment_budget_context
    elif (
        history
        and not _has_product_reference(_normalize(current_query))
        and not _is_accessory_catalog_request(current_query)
        and not _is_case_accessory_request(current_query)
        and (
            _is_catalog_followup(current_query)
            or _is_catalog_availability_confirmation(current_query, history)
            or _is_catalog_model_confirmation(current_query, history)
            or capacity_availability_followup
            or battery_unit_selection_query is not None
            or _is_contextual_pronta_entrega_followup(current_query, history)
            or customer_affordability_request
        )
    ):
        if newer_generic_catalog_list_index is not None:
            product_context_history = history_entries[newer_generic_catalog_list_index:]
            while product_context_history and _CATALOG_CONTEXT_ACKNOWLEDGEMENT_RE.fullmatch(
                _normalize(product_context_history[-1].get("content", ""))
            ):
                product_context_history = product_context_history[:-1]
        else:
            product_context_history = history
        query = (
            customer_request_context
            if customer_affordability_request
            and customer_request_has_catalog_anchor
            else _product_context_query(
                current_query,
                product_context_history,
                strip_assistant_constraints=not (
                    customer_affordability_request
                    and not customer_request_has_catalog_anchor
                ),
            )
        )
    if ipad_model_alternative_price_followup:
        query = _customer_product_request_context_query(text, history)
    if capacity_availability_followup:
        # Photo metadata from the previous turn must not veto the current
        # stock question or remain an active intent marker.
        query = re.sub(r"\b(?:foto|fotos|imagem|imagens)\b", " ", query)
        query = re.sub(r"\s+", " ", query).strip()
    if _is_other_color_request(text) or _is_catalog_color_list_request(text):
        query = _strip_catalog_color_references(
            query,
            list(getattr(self.cache, "items", []) or []),
        )
    if not allow_buyer_interest_with_image and not installment_budget_context:
        pending_multi_model_context = _pending_multi_model_product_request_context(
            text,
            history,
        )
        if pending_multi_model_context:
            query = pending_multi_model_context
    visual_search_query = None
    if allow_buyer_interest_with_image:
        visual_catalog_context = image_description
        requested_text_models = _requested_iphone_model_keys(text)
        if len(requested_text_models) == 1:
            selected_model_segments = _visual_iphone_model_segments(
                image_description or ""
            ).get(requested_text_models[0], ())
            visual_catalog_context = (
                "\n".join(selected_model_segments)
                if selected_model_segments
                else text
            )
        visual_search_query = _visual_catalog_search_query(
            visual_catalog_context,
            list(getattr(self.cache, "items", []) or []),
        )
    if allow_buyer_interest_with_image and visual_search_query is None:
        return None
    catalog_search_query = visual_search_query or query
    if (
        not visual_search_query
        and _is_catalog_price_followup(text)
        and _has_recent_catalog_product_context(history)
    ):
        contextual_models = _requested_iphone_model_keys(query)
        latest_customer_model: tuple[int | str, str] | None = None
        for entry in reversed(history or []):
            if entry.get("role") != "user":
                continue
            customer_models = _requested_iphone_model_keys(entry.get("content", ""))
            if len(customer_models) == 1:
                latest_customer_model = customer_models[0]
                break
        if latest_customer_model is not None:
            contextual_models = (latest_customer_model,)
        if len(contextual_models) == 1 and _catalog_families(query) == ("iphone",):
            number, variant = contextual_models[0]
            model = "iPhone " + str(number).upper()
            if variant:
                model += " " + variant.title()
            search_parts = [model]

            current_or_contextual_capacities = (
                _requested_capacity_keys(text) or _requested_capacity_keys(query)
            )
            for capacity in current_or_contextual_capacities:
                capacity_match = re.fullmatch(
                    r"(?P<number>\d+(?:\.\d+)?)(?P<unit>gb|tb)",
                    capacity,
                )
                if capacity_match:
                    search_parts.append(
                        f"{capacity_match.group('number')} {capacity_match.group('unit').upper()}"
                    )

            customer_context = _customer_product_request_context_query(text, history)
            customer_normalized = _normalize(customer_context)
            if _has_seminovo_reference(customer_normalized):
                search_parts.append("seminovo")
            elif _has_explicit_sealed_condition(customer_normalized):
                search_parts.append("lacrado")
            customer_battery = _requested_battery_health(customer_context)
            if customer_battery is not None:
                search_parts.extend(("bateria", f"{customer_battery:g}%"))
            customer_colors = _requested_catalog_colors(
                customer_context,
                list(getattr(self.cache, "items", []) or []),
            )
            if len(customer_colors) == 1:
                search_parts.append(customer_colors[0])
            search_parts.append("valor")
            catalog_search_query = " ".join(search_parts)
            query = catalog_search_query
    catalog_filter_query = (
        _current_catalog_context(text, visual_search_query)
        if visual_search_query
        else query
    )
    if not (
        _is_product_availability_request(
            query,
            history=history,
            allow_buyer_interest_with_image=allow_buyer_interest_with_image,
            buyer_interest_text=text,
        )
        or _is_contextual_pronta_entrega_followup(text, history)
        or ipad_model_alternative_price_followup
        or battery_unit_selection_query is not None
        or (
            is_current_cheapest_request
            and _has_recent_catalog_product_context(
                history,
                skip_acknowledgements=True,
            )
        )
    ):
        return None

    requested_budget = None
    if not installment_budget_context:
        requested_budget = current_request_budget
        if requested_budget is None and not allow_buyer_interest_with_image:
            requested_budget = _extract_latest_budget_limit(customer_request_context)
    requested_quantity = _requested_device_quantity(
        text if allow_buyer_interest_with_image else query
    )
    customer_condition_context = (
        seminovo_trade_in_query
        or _customer_product_condition_context_query(text, history)
    )
    pronta_entrega_only = _is_pronta_entrega_only_request(
        text
    ) or _is_pronta_entrega_only_request(customer_request_context)
    if selection_context_used:
        condition_query = current_query
    else:
        condition_query = customer_condition_context
    try:
        candidates = await self.cache.search(
            _availability_catalog_query(catalog_search_query),
            limit=300,
        )
    except Exception:
        alternative = await self._try_unavailable_lacrado_alternative(
            catalog_filter_query,
            requested_budget=requested_budget,
            condition_query=condition_query,
        )
        if alternative is not None:
            return alternative
        return None

    public_candidates = [
        item
        for item in candidates
        if (
            _is_sealed_accessory_item(item)
            if _is_accessory_catalog_request(catalog_filter_query)
            else _is_device_item(item)
        )
        and (getattr(item, "source", None) != "mercado_phone" or _is_available_item(item))
    ]
    public_candidates = [
        item
        for item in public_candidates
        if _matches_requested_model(catalog_filter_query, item)
    ]
    if pronta_entrega_only:
        public_candidates = [
            item
            for item in public_candidates
            if getattr(item, "source", None) == "mercado_phone"
        ]
    if requested_budget is not None:
        within_budget: list[Any] = []
        for item in public_candidates:
            price = getattr(item, "price_brl", None)
            try:
                if price is not None and float(price) <= requested_budget:
                    within_budget.append(item)
            except (TypeError, ValueError):
                continue
        public_candidates = within_budget

    requested_capacities = _requested_capacity_keys(text) or _requested_capacity_keys(
        catalog_filter_query
    )
    requested_families = _catalog_families(catalog_filter_query)
    requested_apple_watch_models = (
        _requested_apple_watch_model_keys(text, allow_bare=True)
        if "apple_watch" in requested_families
        else ()
    )
    if requested_apple_watch_models:
        public_candidates = [
            item
            for item in public_candidates
            if _matches_requested_apple_watch_model(
                requested_apple_watch_models,
                item,
            )
        ]
    requested_ipad_models = _requested_ipad_model_keys(catalog_filter_query)
    requested_models = _requested_iphone_model_keys(catalog_filter_query)
    requested_model_segments = _requested_model_segments(customer_request_context)
    model_attribute_items = [
        *public_candidates,
        *list(getattr(self.cache, "items", []) or []),
        *list(getattr(getattr(self.cache, "sealed_cache", None), "items", []) or []),
    ]
    requested_model_scopes = {}
    if len(requested_models) > 1:
        requested_model_scopes = _requested_model_attribute_scopes(
            requested_model_segments,
            model_attribute_items,
        )

    visual_context = image_description or ""
    image_history_index: int | None = None
    if not visual_context:
        for index in range(len(history or []) - 1, -1, -1):
            entry = (history or [])[index]
            content = entry.get("content", "").strip()
            if entry.get("role") == "user" and _is_image_history_entry(content):
                visual_context = content
                image_history_index = index
                break
    visual_model_segments = (
        _visual_iphone_model_segments(visual_context)
        if visual_context
        else {}
    )
    visual_model_scope_applied = False
    visual_model_scoped_colors: tuple[str, ...] = ()
    has_later_user_colors = False
    if (
        len(requested_models) == 1
        and not _is_other_color_request(text)
        and not _is_catalog_color_list_request(text)
    ):
        selected_model = requested_models[0]
        selected_visual_segments = visual_model_segments.get(selected_model, ())
        later_user_messages = [
            entry.get("content", "").strip()
            for index, entry in enumerate(history or [])
            if image_history_index is not None
            and index > image_history_index
            and entry.get("role") == "user"
            and entry.get("content", "").strip()
            and not _is_image_history_entry(entry.get("content", ""))
        ]
        has_later_user_colors = any(
            _requested_catalog_colors(message, model_attribute_items)
            for message in later_user_messages
        )
        has_later_user_capacities = any(
            _requested_capacity_keys(message)
            for message in later_user_messages
        )
        if selected_visual_segments:
            visual_scopes = _requested_model_attribute_scopes(
                {selected_model: selected_visual_segments},
                model_attribute_items,
            ).get(selected_model, ())
            explicit_text_colors = _requested_catalog_colors(text, model_attribute_items)
            explicit_text_capacities = _requested_capacity_keys(text)
            scoped_visual_attributes = []
            scoped_colors: list[str] = []
            for segment, capacities, colors in visual_scopes:
                if explicit_text_capacities or has_later_user_capacities:
                    capacities = ()
                if (
                    explicit_text_colors
                    or has_later_user_colors
                    or explicit_text_capacities
                    or has_later_user_capacities
                ):
                    colors = ()
                if capacities or colors:
                    scoped_visual_attributes.append((segment, capacities, colors))
                for color in colors:
                    if color not in scoped_colors:
                        scoped_colors.append(color)
            requested_model_scopes = (
                {selected_model: tuple(scoped_visual_attributes)}
                if scoped_visual_attributes
                else {}
            )
            visual_model_scope_applied = True
            visual_model_scoped_colors = tuple(scoped_colors)

    def matches_model_attributes(
        item: Any,
        capacities: tuple[str, ...],
        colors: tuple[str, ...],
    ) -> bool:
        capacity = _capacity_key(
            getattr(item, "capacity", None) or getattr(item, "name", "")
        )
        return (
            (not capacities or capacity in capacities)
            and (not colors or _item_matches_catalog_colors(item, colors))
        )

    def matches_requested_model_scope(item: Any) -> bool:
        model = _model_key(getattr(item, "name", ""))
        model_scopes = requested_model_scopes.get(model, ())
        if not model_scopes:
            return True
        return any(
            matches_model_attributes(item, capacities, colors)
            for _segment, capacities, colors in model_scopes
        )

    if requested_model_scopes:
        public_candidates = [
            item for item in public_candidates if matches_requested_model_scope(item)
        ]
    requested_pro_max_without_generation = bool(
        not requested_models
        and re.search(r"\bpro\s+max\b", _normalize(catalog_filter_query))
    )
    if requested_pro_max_without_generation:
        public_candidates = [
            item
            for item in public_candidates
            if re.search(
                r"\bpro\s+max\b",
                _normalize(str(getattr(item, "name", "") or "")),
            )
        ]
    scoped_capacity = _trailing_model_capacity_scope(
        text,
        _requested_iphone_model_keys(text),
    )
    if scoped_capacity is None and not _requested_capacity_keys(text):
        scoped_capacity = _trailing_model_capacity_scope(
            catalog_filter_query,
            requested_models,
        )
    if scoped_capacity is not None:
        scoped_model, scoped_capacity_key = scoped_capacity
        public_candidates = [
            item
            for item in public_candidates
            if _model_key(getattr(item, "name", "")) != scoped_model
            or _capacity_key(getattr(item, "capacity", None) or getattr(item, "name", ""))
            == scoped_capacity_key
        ]
    elif requested_capacities and not requested_model_scopes:
        public_candidates = [
            item
            for item in public_candidates
            if _capacity_key(getattr(item, "capacity", None) or getattr(item, "name", ""))
            in requested_capacities
        ]

    normalized_query = _normalize(condition_query)
    requested_conditions: set[str] = set()
    has_explicit_sealed_condition = _has_explicit_sealed_condition(normalized_query)
    if has_explicit_sealed_condition:
        requested_conditions.add("lacrado")
    if _has_seminovo_reference(normalized_query):
        requested_conditions.add("seminovo")
    condition_was_requested = bool(requested_conditions)
    single_condition_requested = len(requested_conditions) == 1
    if condition_was_requested:
        public_candidates = [
            item
            for item in public_candidates
            if ("lacrado" if _is_sealed_item(item) else "seminovo") in requested_conditions
        ]

    installment_budget = (
        _extract_installment_budget(customer_request_context)
        if installment_budget_context
        else None
    )
    entry_amount = _extract_entry_amount(customer_request_context) if installment_budget else None
    installment_quotes: dict[int, dict[str, Any]] = {}
    if installment_budget is not None:
        installment_count, monthly_limit = installment_budget
        matching_installments: list[Any] = []
        for item in public_candidates:
            try:
                result = (
                    simulate_installment_with_entry(item, entry_amount, installment_count)
                    if entry_amount is not None
                    else simulate_installment(item, installment_count)
                )
                monthly_payment = float(result.get("valor_parcela_brl"))
            except (TypeError, ValueError, OverflowError):
                continue
            if result.get("encontrado") and monthly_payment <= monthly_limit:
                matching_installments.append(item)
                installment_quotes[id(item)] = result
        public_candidates = matching_installments

    color_catalog_items = [
        *public_candidates,
        *list(getattr(self.cache, "items", []) or []),
        *list(getattr(getattr(self.cache, "sealed_cache", None), "items", []) or []),
    ]
    exploring_colors = _is_other_color_request(text) or _is_catalog_color_list_request(text)
    requested_catalog_colors: tuple[str, ...] = ()
    if not exploring_colors:
        requested_catalog_colors = _latest_catalog_color_request(
            text,
            "",
            color_catalog_items,
        )
        if not requested_catalog_colors and image_description:
            requested_catalog_colors = _latest_catalog_color_request(
                image_description,
                "",
                color_catalog_items,
            )
        if not requested_catalog_colors:
            requested_catalog_colors = _latest_catalog_color_request(
                "",
                customer_request_context,
                color_catalog_items,
            )
    if (
        visual_model_scope_applied
        and not _requested_catalog_colors(text, color_catalog_items)
        and not has_later_user_colors
    ):
        requested_catalog_colors = visual_model_scoped_colors
    color_either_or_request = (
        not exploring_colors
        and _is_catalog_color_either_or_request(
            text,
            image_description,
            customer_request_context,
            color_catalog_items,
        )
    )
    single_catalog_model_request = (
        len(requested_models) <= 1
        and len(requested_ipad_models) <= 1
        and len(requested_families) <= 1
    )
    requested_color_unavailable = False
    unavailable_requested_colors: tuple[str, ...] = ()
    has_available_requested_color = False
    if single_catalog_model_request and requested_catalog_colors:
        matching_color_candidates = [
            item
            for item in public_candidates
            if _item_matches_catalog_colors(item, requested_catalog_colors)
        ]
        available_requested_colors = tuple(
            color
            for color in requested_catalog_colors
            if any(
                _item_matches_catalog_colors(item, (color,))
                for item in public_candidates
            )
        )
        unavailable_requested_colors = tuple(
            color
            for color in requested_catalog_colors
            if color not in available_requested_colors
        )
        has_available_requested_color = bool(available_requested_colors)
        requested_color_unavailable = bool(
            public_candidates
            and unavailable_requested_colors
            and not (color_either_or_request and has_available_requested_color)
        )
        if matching_color_candidates:
            public_candidates = matching_color_candidates

    if not public_candidates:
        if allow_buyer_interest_with_image:
            selected_text_models = _requested_iphone_model_keys(text)
            if len(selected_text_models) == 1:
                # If the pictured unit is gone, check for another current
                # unit of the explicitly selected model before answering.
                current_model_decision = await self._try_product_availability(
                    text,
                    history=history,
                )
                if current_model_decision is not None:
                    if current_model_decision.product_references:
                        return current_model_decision
                    number, variant = selected_text_models[0]
                    number_label = str(number).upper() if isinstance(number, str) else str(number)
                    model_label = f"iPhone {number_label}"
                    if variant:
                        model_label += f" {variant.title()}"
                    return AgentDecision(
                        reply=(
                            f"No momento, não temos o {model_label} disponível no estoque. "
                            "Posso procurar outras opções disponíveis?"
                        ),
                        confidence="medium",
                    )
        if installment_budget is not None:
            installment_count, monthly_limit = installment_budget
            entry_text = (
                f" e entrada de {format_brl(entry_amount)}"
                if entry_amount is not None
                else " sem entrada"
            )
            return AgentDecision(
                reply=(
                    f"Não encontrei aparelhos com preço confirmado que caibam em "
                    f"{installment_count}x de até {format_brl(monthly_limit)}{entry_text}. "
                    "Posso procurar opções com uma entrada ou com uma parcela mensal maior?"
                ),
                confidence="medium",
            )
        if _is_bare_model_catalog_followup(text, history):
            requested_label = _extract_bare_catalog_model_reference(text)
            if requested_label:
                return AgentDecision(
                    reply=(
                        f"No momento, não localizei o {requested_label} disponível no estoque. "
                        "Posso verificar outro modelo ou capacidade?"
                    ),
                    confidence="medium",
                )
        if requested_apple_watch_models:
            watch_labels = []
            for family, number, size in requested_apple_watch_models:
                family_label = {
                    "series": "Series",
                    "se": "SE",
                    "ultra": "Ultra",
                }[family]
                label = f"Apple Watch {family_label} {number}"
                if size:
                    label += f" {size} mm"
                watch_labels.append(label)
            requested_watch_label = " e ".join(watch_labels)
            if pronta_entrega_only:
                reply = (
                    f"Não localizei {requested_watch_label} a pronta entrega no estoque "
                    "no momento. Se quiser, posso consultar opções por encomenda ou outros modelos."
                )
            else:
                reply = (
                    f"No momento, não localizei {requested_watch_label} disponível no catálogo. "
                    "Posso verificar outra cor ou tamanho?"
                )
            return AgentDecision(reply=reply, confidence="medium")
        if not pronta_entrega_only:
            alternative = await self._try_unavailable_lacrado_alternative(
                catalog_filter_query,
                requested_budget=requested_budget,
                condition_query=condition_query,
            )
            if alternative is not None:
                return alternative
            if requested_budget is None:
                alternative = await self._try_unavailable_seminew_alternative(
                    catalog_filter_query
                )
                if alternative is not None:
                    return alternative
        capacity_text = (
            f" {', '.join(value.upper() for value in requested_capacities)}"
            if requested_capacities
            else ""
        )
        if pronta_entrega_only:
            return AgentDecision(
                reply=(
                    "Não localizei esse modelo a pronta entrega no estoque no momento. "
                    "Se quiser, posso consultar opções por encomenda ou outros modelos."
                ),
                confidence="medium",
            )
        if requested_budget is not None:
            return AgentDecision(
                reply=(
                    f"Não localizei aparelhos disponíveis até {format_brl(requested_budget)}{capacity_text}. "
                    "Posso procurar em uma faixa maior ou em outro modelo?"
                ),
                confidence="medium",
            )
        return AgentDecision(
            reply=(
                f"No momento não localizei uma opção cadastrada{capacity_text} para esse produto. "
                "Pode me informar outro modelo ou capacidade?"
            ),
            confidence="medium",
        )

    cheapest_request = is_current_cheapest_request
    has_explicit_catalog_attributes = (
        _requested_battery_health(catalog_filter_query) is not None
        or bool(requested_catalog_colors)
        or _has_requested_catalog_color(catalog_filter_query, public_candidates)
    )
    broad_request = (
        requested_budget is not None
        or (
            installment_budget is not None
            and not has_explicit_catalog_attributes
        )
        or requested_quantity is not None
        or _is_broad_airpods_request(catalog_filter_query)
        or len(requested_families) > 1
        or len(requested_ipad_models) > 1
        or _requested_iphone_model_floor(catalog_filter_query) is not None
    )
    def price_sort_key(item: Any) -> tuple[float, str, str, str]:
        numeric_price = _confirmed_catalog_price(item)
        if numeric_price is None:
            numeric_price = float("inf")
        return (
            numeric_price,
            _normalize(str(getattr(item, "name", "") or "")),
            _normalize(str(getattr(item, "capacity", "") or "")),
            _normalize(str(getattr(item, "color", None) or getattr(item, "colors", "") or "")),
        )

    if requested_color_unavailable:
        selected = sorted(public_candidates, key=price_sort_key)
    elif cheapest_request:
        priced_candidates = [
            item for item in public_candidates if _confirmed_catalog_price(item) is not None
        ]
        if not priced_candidates:
            return AgentDecision(
                reply=(
                    "No momento não localizei nenhum aparelho com preço confirmado para comparar. "
                    "Posso verificar outra opção?"
                ),
                confidence="medium",
            )
        if len(requested_models) > 1 and len(_requested_model_segments(text)) > 1:
            selected = []
            selected_ids: set[str] = set()
            for requested_model in requested_models:
                model_candidates = [
                    item
                    for item in priced_candidates
                    if _model_key(getattr(item, "name", "")) == requested_model
                ]
                model_scopes = requested_model_scopes.get(requested_model, ())
                selection_scopes = model_scopes or (("", (), ()),)
                for _segment, capacities, colors in selection_scopes:
                    scoped_candidates = [
                        item
                        for item in model_candidates
                        if matches_model_attributes(item, capacities, colors)
                    ]
                    if not scoped_candidates:
                        continue
                    selected_item = min(scoped_candidates, key=price_sort_key)
                    external_id = str(getattr(selected_item, "external_id", "") or "")
                    if external_id not in selected_ids:
                        selected.append(selected_item)
                        selected_ids.add(external_id)
        else:
            selected = sorted(priced_candidates, key=price_sort_key)[:1]
    elif broad_request:
        selected = sorted(public_candidates, key=price_sort_key)
    else:
        scored = [
            (_catalog_score(catalog_search_query, item), item)
            for item in public_candidates
        ]
        best_score = max(score for score, _item in scored)
        if best_score <= 0:
            if not pronta_entrega_only:
                alternative = await self._try_unavailable_lacrado_alternative(
                    catalog_filter_query,
                    requested_budget=requested_budget,
                    condition_query=condition_query,
                )
                if alternative is not None:
                    return alternative
                if requested_budget is None:
                    alternative = await self._try_unavailable_seminew_alternative(
                        catalog_filter_query
                    )
                    if alternative is not None:
                        return alternative
            if pronta_entrega_only:
                return AgentDecision(
                    reply=(
                        "Não localizei esse modelo a pronta entrega no estoque no momento. "
                        "Se quiser, posso consultar opções por encomenda ou outros modelos."
                    ),
                    confidence="medium",
                )
            return AgentDecision(
                reply="No momento não localizei esse produto no catálogo. Pode me informar o modelo ou capacidade?",
                confidence="medium",
            )

        multiple_model_request_without_capacity = (
            len(requested_models) > 1
            and not requested_capacities
        )
        return_all_matching_units = (
            (
                installment_budget is not None
                or (
                    (
                        len(requested_models) == 1
                        and (bool(requested_conditions) or len(requested_capacities) <= 1)
                    )
                    or multiple_model_request_without_capacity
                    or scoped_capacity is not None
                    or (
                        len(requested_models) == 1
                        and not requested_capacities
                        and "possibilidade de comprar" in _normalize(text)
                    )
                )
            )
            and requested_quantity is None
            and not has_explicit_catalog_attributes
        )

        def condition_matches(item: Any) -> bool:
            if not requested_conditions:
                return True
            condition = "lacrado" if _is_sealed_item(item) else "seminovo"
            return condition in requested_conditions

        def best_matches(scored_items: list[tuple[int, Any]]) -> list[Any]:
            positive = [(score, item) for score, item in scored_items if score > 0]
            if not positive:
                return []
            best = max(score for score, _item in positive)
            return [item for score, item in positive if score == best]

        def include_ready_sealed_units(
            selected_items: list[Any],
            scored_items: list[tuple[int, Any]],
        ) -> list[Any]:
            """Keep physical sealed stock beside equally matching sheet prices."""
            if (
                (
                    "seminovo" in requested_conditions
                    and "lacrado" not in requested_conditions
                )
                or requested_quantity is not None
                or _requested_battery_health(catalog_filter_query) is not None
                or _has_requested_catalog_color(catalog_filter_query, public_candidates)
            ):
                return selected_items

            selected_ids = {str(getattr(item, "external_id", "")) for item in selected_items}
            ready_items = [
                item
                for score, item in scored_items
                if score > 0
                and getattr(item, "source", "") == "mercado_phone"
                and _is_sealed_item(item)
                and str(getattr(item, "external_id", "")) not in selected_ids
            ]
            return [*ready_items, *selected_items]

        def select_best_matches(scored_items: list[tuple[int, Any]]) -> list[Any]:
            if single_condition_requested:
                return include_ready_sealed_units(best_matches(scored_items), scored_items)

            grouped: dict[str, list[tuple[int, Any]]] = {
                "seminovo": [],
                "lacrado": [],
            }
            for score, item in scored_items:
                condition = "lacrado" if _is_sealed_item(item) else "seminovo"
                grouped[condition].append((score, item))

            selected_by_condition: list[Any] = []
            for condition in ("seminovo", "lacrado"):
                selected = best_matches(grouped[condition])
                if condition == "lacrado":
                    selected = include_ready_sealed_units(selected, grouped[condition])
                selected_by_condition.extend(selected)
            return selected_by_condition or best_matches(scored_items)

        def select_requested_model_matches(scored_items: list[tuple[int, Any]]) -> list[Any]:
            if len(requested_models) <= 1:
                return select_best_matches(scored_items)

            selected_by_model: list[Any] = []
            selected_by_model_ids: set[str] = set()
            for requested_model in requested_models:
                model_matches = [
                    (score, item)
                    for score, item in scored_items
                    if _model_key(getattr(item, "name", "")) == requested_model
                ]
                model_scopes = requested_model_scopes.get(requested_model, ())
                if model_scopes:
                    for segment, capacities, colors in model_scopes:
                        scoped_matches = [
                            (_catalog_score(segment, item), item)
                            for _score, item in model_matches
                            if matches_model_attributes(item, capacities, colors)
                        ]
                        for item in select_best_matches(scoped_matches):
                            external_id = str(getattr(item, "external_id", "") or "")
                            if external_id not in selected_by_model_ids:
                                selected_by_model.append(item)
                                selected_by_model_ids.add(external_id)
                else:
                    model_segments = requested_model_segments.get(requested_model, ())
                    if model_segments:
                        model_matches = [
                            (
                                max(_catalog_score(segment, item) for segment in model_segments),
                                item,
                            )
                            for _score, item in model_matches
                        ]
                    for item in select_best_matches(model_matches):
                        external_id = str(getattr(item, "external_id", "") or "")
                        if external_id not in selected_by_model_ids:
                            selected_by_model.append(item)
                            selected_by_model_ids.add(external_id)
            return selected_by_model

        if return_all_matching_units:
            selected = [
                item
                for score, item in scored
                if score > 0 and condition_matches(item)
            ]
        elif len(requested_capacities) > 1 and not requested_model_scopes:
            selected = []
            for capacity in requested_capacities:
                capacity_matches = [
                    (score, item)
                    for score, item in scored
                    if _capacity_key(getattr(item, "capacity", None) or getattr(item, "name", ""))
                    == capacity
                ]
                if not capacity_matches:
                    continue
                selected.extend(select_requested_model_matches(capacity_matches))
        else:
            selected = select_requested_model_matches(scored)

        if (
            single_catalog_model_request
            and len(requested_catalog_colors) > 1
            and not color_either_or_request
            and not unavailable_requested_colors
        ):
            selected_ids = {
                str(getattr(item, "external_id", ""))
                for item in selected
            }
            for color in requested_catalog_colors:
                color_matches = [
                    (score, item)
                    for score, item in scored
                    if _item_matches_catalog_colors(item, (color,))
                    and condition_matches(item)
                ]
                color_selected = select_best_matches(color_matches)
                if not color_selected and color_matches:
                    color_selected = [
                        min(
                            color_matches,
                            key=lambda match: (-match[0], price_sort_key(match[1])),
                        )[1]
                    ]
                for item in color_selected:
                    item_id = str(getattr(item, "external_id", ""))
                    if item_id not in selected_ids:
                        selected.append(item)
                        selected_ids.add(item_id)

    selected = _sort_product_availability_items(selected)
    availability_header = None
    if requested_color_unavailable and selected:
        availability_header = _unavailable_catalog_color_header(
            selected=selected,
            requested_models=requested_models,
            requested_ipad_models=requested_ipad_models,
            requested_families=requested_families,
            requested_catalog_colors=(
                unavailable_requested_colors or requested_catalog_colors
            ),
            requested_capacities=requested_capacities,
            has_available_requested_color=has_available_requested_color,
            requested_iphone_variant=(
                "pro max" if requested_pro_max_without_generation else None
            ),
            requested_apple_watch_models=requested_apple_watch_models,
        )
    higher_battery_option_request = _is_higher_battery_option_request(text)
    if higher_battery_option_request and selected and availability_header is None:
        model_name = str(getattr(selected[0], "name", None) or "esse modelo").strip()
        if len(selected) == 1:
            availability_header = (
                f"No momento, só encontrei esta opção de {model_name} disponível no catálogo:"
            )
        else:
            availability_header = (
                f"No momento, encontrei estas opções de {model_name} disponíveis no catálogo:"
            )

    reply = _format_product_availability(
        selected,
        header=availability_header,
        installment_budget=installment_budget,
        installment_quotes=installment_quotes,
        entry_amount=entry_amount,
    )
    if higher_battery_option_request and selected:
        battery_values = []
        for item in selected:
            if _is_sealed_item(item):
                continue
            try:
                battery = float(getattr(item, "battery_health", None))
            except (TypeError, ValueError):
                continue
            if 0 <= battery <= 100:
                battery_values.append(battery)
        if len(selected) == 1 and battery_values:
            reply += "\nNão localizei outra opção com saúde da bateria mais alta no momento."
        elif battery_values:
            highest_battery = max(battery_values)
            reply += (
                "\nA maior saúde da bateria cadastrada entre essas opções é "
                f"{_format_battery(highest_battery)}."
            )
        elif len(selected) == 1:
            reply += (
                "\nA saúde da bateria desta unidade não está informada no catálogo, "
                "então não consigo confirmar uma opção com saúde maior."
            )
    if selected and _is_other_color_request(text):
        available_colors = _catalog_color_map(selected)
        if len(available_colors) == 1:
            available_color = next(iter(available_colors.values()))
            model_name = str(getattr(selected[0], "name", None) or "esse modelo").strip()
            reply = (
                f"No momento, só encontrei o {model_name} na cor {available_color} 😊. "
                "Não aparece outra cor disponível no catálogo."
            )
    bare_model_price_request = (
        not re.search(r"\biphones?\b", _normalize(catalog_filter_query))
        and bool(
            re.search(
                r"\b(?:orcamento|valor(?:es)?|preco(?:s)?)\b",
                _normalize(catalog_filter_query),
            )
        )
    )
    if (
        len(requested_models) > 1
        and selected
        and not requested_capacities
        and not condition_was_requested
        and bare_model_price_request
    ):
        selected_models = {
            _model_key(getattr(item, "name", "")) for item in selected
        }
        unavailable_models = [
            model for model in requested_models if model not in selected_models
        ]
        if unavailable_models:
            unavailable_labels = []
            for number, variant in unavailable_models:
                if not variant:
                    unavailable_labels.append(f"iPhone {number} (modelo base)")
                elif variant == "e":
                    unavailable_labels.append(f"iPhone {number}e")
                else:
                    unavailable_labels.append(f"iPhone {number} {variant.title()}")
            reply += (
                "\n\nNão localizei opção disponível para "
                + " e ".join(unavailable_labels)
                + "."
            )
    if len(requested_ipad_models) > 1 and selected:
        selected_ipad_models = {
            model
            for item in selected
            for model in _requested_ipad_model_keys(getattr(item, "name", ""))
            if model in requested_ipad_models
        }
        unavailable_ipad_models = [
            model for model in requested_ipad_models if model not in selected_ipad_models
        ]
        if unavailable_ipad_models:
            unavailable_labels = []
            for model in unavailable_ipad_models:
                model_label = model.title() if model in {"air", "pro", "mini"} else model
                unavailable_labels.append(f"iPad {model_label}")
            condition_label = (
                " seminova"
                if requested_conditions == {"seminovo"}
                else " nova/lacrada"
                if requested_conditions == {"lacrado"}
                else ""
            )
            reply += (
                "\n\nNão localizei opção"
                + condition_label
                + " disponível para "
                + " e ".join(unavailable_labels)
                + "."
            )
    if requested_quantity is not None:
        reply += (
            f"\n\nComo você precisa de {requested_quantity} aparelhos, "
            f"escolha os {requested_quantity} que prefere e depois me informe o horário desejado para retirada."
        )
    references = [str(getattr(item, "external_id", "")) for item in selected]
    return AgentDecision(
        reply=reply,
        product_references=[reference for reference in references if reference],
        confidence="high",
    )
