"""Payments response handlers for AgentService."""

from __future__ import annotations

from app.agent import (
    AgentDecision,
    Any,
    BOLETO_INSTALLMENT_REPLY,
    InventoryItem,
    PAYMENT_LINK_REPLY,
    PAYMENT_METHODS_REPLY,
    PAYMENT_ONLY_CREDIT_REPLY,
    _cash_price_quotes_from_history,
    _catalog_battery_unit_selection_query,
    _catalog_families,
    _catalog_price_unit_selection_query,
    _extract_entry_amount,
    _format_ambiguous_installment_decision,
    _has_explicit_sealed_condition,
    _has_installment_product_context,
    _has_recent_card_installment_simulation,
    _has_recent_catalog_sale_context,
    _has_recent_specific_installment_selection,
    _has_seminovo_reference,
    _installment_context_query,
    _is_boleto_payment_request,
    _is_both_unit_installment_request,
    _is_cash_discount_question,
    _is_cash_price_question,
    _is_credit_only_installment_question,
    _is_explicit_catalog_price_request,
    _is_full_installment_request,
    _is_generic_installment_count_question,
    _is_independent_payment_methods_request,
    _is_installment_fee_inclusion_question,
    _is_installment_model_followup,
    _is_installment_offer_confirmation,
    _is_installment_rate_for_referenced_price,
    _is_installment_rate_question,
    _is_installment_selection_followup,
    _is_no_interest_installment_question,
    _is_payment_link_followup,
    _is_payment_link_request,
    _is_payment_methods_question,
    _is_price_validity_question,
    _is_product_availability_request,
    _is_rate_model_followup,
    _is_specific_installment_request,
    _last_requested_installments,
    _multi_model_installment_queries,
    _normalize,
    _recent_installment_entry_amount,
    _requested_battery_health,
    _requested_capacity_keys,
    _requested_installment_product_keys,
    _requested_installments,
    _requested_iphone_model_keys,
    format_brl,
    format_installment_rates,
    format_installment_table,
    math,
    re,
    simulate_installment_table_with_entry,
)

async def _try_installment_rate_question(
    self,
    text: str,
    history: list[dict[str, str]] | None = None,
) -> AgentDecision | None:
    if not _is_installment_rate_question(text):
        return None
    if (
        _is_installment_fee_inclusion_question(text)
        and _has_recent_card_installment_simulation(history)
    ):
        return AgentDecision(
            reply=(
                "Sim. Os valores parcelados da simulação já incluem a taxa de juros da "
                "máquina. O preço à vista aparece separado, e o total varia conforme "
                "a quantidade de parcelas."
            ),
            confidence="high",
        )
    if (
        _is_installment_rate_for_referenced_price(text)
        and _has_recent_catalog_sale_context(history)
    ):
        method = getattr(self.cache, "simulate_all_installments", None)
        if callable(method):
            query = _installment_context_query(text, history)
            try:
                result = await method(query)
            except Exception:
                result = {}
            if result.get("encontrado"):
                return AgentDecision(
                    reply=format_installment_table(result),
                    confidence="high",
                )
    return AgentDecision(
        reply=format_installment_rates(
            ask_for_model=not _is_no_interest_installment_question(text)
        ),
        confidence="high",
    )

async def _try_generic_installment_count_question(
    self,
    text: str,
    history: list[dict[str, str]] | None,
    *,
    image_description: str | None = None,
) -> AgentDecision | None:
    if not _is_generic_installment_count_question(text):
        return None

    if _is_payment_link_followup(text, history):
        return await self._try_payment_link(text, history)

    product_context = "\n".join(
        part for part in (text, image_description) if part and part.strip()
    )
    contextual_query = _installment_context_query(product_context, history)
    requested_families = _catalog_families(contextual_query)
    single_product_key = _requested_installment_product_keys(contextual_query)
    family_has_model_key_without_capacity = requested_families in (
        ("macbook",),
        ("airpods",),
        ("apple_watch",),
    )
    has_single_product = (
        len(single_product_key) == 1
        and (
            len(_requested_capacity_keys(contextual_query)) == 1
            or (
                requested_families == ("iphone",)
                and _has_recent_specific_installment_selection(history)
            )
            or family_has_model_key_without_capacity
        )
    )
    if has_single_product:
        installment = await self._try_full_installment_table(
            text,
            history,
            image_description=image_description,
        )
        if installment is not None:
            return installment

    return AgentDecision(reply=PAYMENT_ONLY_CREDIT_REPLY, confidence="high")

async def _try_rate_model_followup(
    self,
    text: str,
    history: list[dict[str, str]] | None,
) -> AgentDecision | None:
    if not _is_rate_model_followup(text, history):
        return None
    specific = await self._try_specific_installment(text, history)
    if specific is not None:
        return specific
    return await self._try_full_installment_table(text, history)

async def _try_model_installment_followup(
    self,
    text: str,
    history: list[dict[str, str]] | None,
) -> AgentDecision | None:
    if not _is_installment_model_followup(text, history):
        return None
    return await self._try_full_installment_table(text, history)

async def _try_installment_offer_confirmation(
    self,
    text: str,
    history: list[dict[str, str]] | None,
) -> AgentDecision | None:
    if not _is_installment_offer_confirmation(text, history):
        return None

    entry_amount = _recent_installment_entry_amount(history)
    if entry_amount is not None:
        synthetic_request = (
            f"Simular as parcelas com entrada de {format_brl(entry_amount)}"
        )
        return await self._try_entry_installment(synthetic_request, history)

    return await self._try_full_installment_table("simular parcelamento", history)

async def _try_installment_unit_selection(
    self,
    text: str,
    history: list[dict[str, str]] | None,
) -> AgentDecision | None:
    if not history or history[-1].get("role") != "assistant":
        return None

    offer = _normalize(history[-1].get("content", ""))
    asks_which_unit = bool(
        re.search(r"\b(?:qual\s+delas|qual\s+das\s+duas|qual\s+unidade)\b", offer)
    )
    asks_to_simulate = bool(re.search(r"\b(?:simul\w*|parcel\w*)\b", offer))
    if not asks_which_unit or not asks_to_simulate:
        return None

    query = _catalog_battery_unit_selection_query(text, history)
    if query is None:
        query = _catalog_price_unit_selection_query(text, history)
    if not query:
        return None

    method = getattr(self.cache, "simulate_all_installments", None)
    if not callable(method):
        return None
    try:
        result = await method(query)
    except Exception:
        return None
    if result.get("encontrado"):
        return AgentDecision(
            reply=format_installment_table(result),
            confidence="high",
        )
    if result.get("ambiguo"):
        return _format_ambiguous_installment_decision(result)

    models = _requested_iphone_model_keys(query)
    if models:
        number, variant = models[0]
        model = f"iPhone {number}" + (f" {variant.title()}" if variant else "")
    else:
        model = "essa unidade"
    battery = _requested_battery_health(query)
    battery_label = f" com bateria {battery:g}%" if battery is not None else ""
    return AgentDecision(
        reply=(
            f"Não encontrei preço confirmado para simular o {model}{battery_label} "
            "no momento."
        ),
        confidence="medium",
    )

async def _try_payment_link(
    self,
    text: str,
    history: list[dict[str, str]] | None,
) -> AgentDecision | None:
    if not (_is_payment_link_request(text) or _is_payment_link_followup(text, history)):
        return None
    reply = self.faq.get("link_pagamento") or PAYMENT_LINK_REPLY
    return AgentDecision(
        reply=self._append_delivery_or_pickup_info(reply, text),
        confidence="high",
    )

def _try_boleto_installment(self, text: str) -> AgentDecision | None:
    if not _is_boleto_payment_request(text):
        return None
    normalized = _normalize(text)
    has_installment_question = _requested_installments(text) is not None or bool(
        re.search(r"\bparcel\w*\b", normalized)
    )
    reply = BOLETO_INSTALLMENT_REPLY
    if not has_installment_question:
        reply = self.faq.get("pagamento") or BOLETO_INSTALLMENT_REPLY
    return AgentDecision(
        reply=self._append_delivery_or_pickup_info(reply, text),
        confidence="high",
    )

def _try_payment_methods(self, text: str) -> AgentDecision | None:
    if not _is_payment_methods_question(text):
        return None

    if _is_credit_only_installment_question(text):
        reply = PAYMENT_ONLY_CREDIT_REPLY
    else:
        reply = self.faq.get("pagamento") or PAYMENT_METHODS_REPLY
    normalized = _normalize(text)
    if re.search(
        r"\b(?:dois|duas|2)\s+cartoes?(?:\s+de\s+credito)?\b",
        normalized,
    ) or "mais de um cartao" in normalized:
        reply = (
            "Sim 😊 Você pode usar dois cartões de crédito na mesma compra e completar o valor "
            f"com PIX, dinheiro ou cartão de débito. {reply}"
        )
    return AgentDecision(
        reply=self._append_delivery_or_pickup_info(reply, text),
        confidence="high",
    )

async def _try_combined_catalog_payment_methods(
    self,
    text: str,
    history: list[dict[str, str]] | None,
) -> AgentDecision | None:
    if (
        not _is_explicit_catalog_price_request(text)
        or not _is_independent_payment_methods_request(text)
        or not _is_payment_methods_question(text)
    ):
        return None

    catalog_decision = await self._try_product_availability(text, history=history)
    payment_decision = self._try_payment_methods(text)
    if catalog_decision is None or payment_decision is None:
        return None
    if catalog_decision.handoff:
        return catalog_decision

    return AgentDecision(
        reply=f"{catalog_decision.reply.rstrip()}\n\n{payment_decision.reply.strip()}",
        product_references=catalog_decision.product_references,
        image_urls=catalog_decision.image_urls,
        confidence="high",
    )

async def _try_combined_warranty_payment(
    self,
    text: str,
    history: list[dict[str, str]] | None,
) -> AgentDecision | None:
    payment_decision = self._try_payment_methods(text)
    if payment_decision is None:
        payment_decision = self._try_boleto_installment(text)
    if payment_decision is None:
        return None
    warranty_decision = await self._try_warranty(text, history)
    if warranty_decision is None:
        return None
    return AgentDecision(
        reply=f"{warranty_decision.reply.rstrip()} {payment_decision.reply.lstrip()}",
        confidence="high",
    )

def _try_price_policy(self, text: str) -> AgentDecision | None:
    if _is_cash_discount_question(text):
        reply = self.faq.get("desconto_pix") or (
            "Não há desconto no PIX; PIX, dinheiro e cartão de débito têm "
            "pagamento integral à vista, sem taxas."
        )
    elif _is_price_validity_question(text):
        reply = self.faq.get("preco") or (
            "Os preços podem ser alterados sem aviso prévio. A confirmação "
            "deve ser feita no momento do atendimento."
        )
    else:
        return None
    return AgentDecision(
        reply=self._append_delivery_or_pickup_info(reply, text),
        confidence="high",
    )

def _try_cash_price_followup(
    self,
    text: str,
    history: list[dict[str, str]] | None,
) -> AgentDecision | None:
    if not _is_cash_price_question(text) or not _has_recent_catalog_sale_context(history):
        return None

    prices = _cash_price_quotes_from_history(history)
    policy = self.faq.get("desconto_pix") or (
        "Não há desconto no PIX; PIX, dinheiro e cartão de débito têm "
        "pagamento integral à vista, sem taxas."
    )
    if len(prices) == 1:
        reply = f"À vista fica {prices[0]}. {policy}"
    elif prices:
        reply = (
            "À vista, os valores são os mesmos informados para cada opção: "
            f"{', '.join(prices)}. {policy}"
        )
    else:
        reply = (
            "À vista fica o mesmo valor informado para a opção do catálogo. "
            f"{policy}"
        )
    return AgentDecision(reply=reply, confidence="high")

async def _try_combined_catalog_installment(
    self,
    text: str,
    history: list[dict[str, str]] | None,
    *,
    image_description: str | None = None,
) -> AgentDecision | None:
    if _extract_entry_amount(text) is not None:
        return None
    if _is_payment_link_request(text) or _is_boleto_payment_request(text):
        return None
    if not _is_product_availability_request(text, history=history):
        return None
    if not (_is_full_installment_request(text) or _is_specific_installment_request(text)):
        return None

    availability = await self._try_product_availability(
        text,
        history=history,
        image_description=image_description,
    )
    if availability is None:
        return None

    installment = await self._try_specific_installment(text, history)
    if installment is None:
        installment = await self._try_full_installment_table(
            text,
            history,
            image_description=image_description,
        )
    if installment is None:
        return availability

    sections = [availability.reply]
    if re.search(r"\bpix\b", _normalize(text)):
        sections.append("Sim 😊 No PIX, o valor é o mesmo preço à vista informado acima.")
    sections.append(installment.reply)
    return AgentDecision(
        reply="\n\n".join(sections),
        product_references=availability.product_references,
        confidence="high",
    )

async def _try_multi_model_installment_tables(
    self,
    text: str,
    history: list[dict[str, str]] | None,
) -> AgentDecision | None:
    queries = _multi_model_installment_queries(text, history)
    if not queries:
        return None
    method = getattr(self.cache, "simulate_all_installments", None)
    if not callable(method):
        return None

    sections: list[str] = []
    references: list[str] = []
    all_found = True
    for query in queries:
        try:
            result = await method(query)
        except Exception:
            return None
        if result.get("encontrado"):
            sections.append(format_installment_table(result))
            continue
        if result.get("ambiguo"):
            ambiguous = _format_ambiguous_installment_decision(result)
            if ambiguous is not None:
                sections.append(ambiguous.reply)
                references.extend(ambiguous.product_references)
                all_found = False
                continue

        requested_model = _requested_iphone_model_keys(query)
        if requested_model:
            number, variant = requested_model[0]
            label = f"iPhone {number}" + (f" {variant.title()}" if variant else "")
        else:
            label = "esse modelo"
        sections.append(
            f"Não encontrei preço confirmado para simular o {label} no momento."
        )
        all_found = False

    return AgentDecision(
        reply="\n\n".join(sections),
        product_references=list(dict.fromkeys(references)),
        confidence="high" if all_found else "medium",
    )

async def _try_both_unit_installment_tables(
    self,
    text: str,
    history: list[dict[str, str]] | None,
    result: dict[str, Any],
) -> AgentDecision | None:
    if not _is_both_unit_installment_request(text, history):
        return None
    candidates = result.get("candidatos") or []
    if len(candidates) != 2 or any(not isinstance(item, dict) for item in candidates):
        return None

    references = [str(item.get("referencia") or "") for item in candidates]
    if not all(references) or len(set(references)) != 2:
        return None
    method = getattr(self.cache, "simulate_all_installments", None)
    if not callable(method):
        return None

    sections: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        name = str(candidate.get("nome") or "").strip()
        capacity = str(candidate.get("capacidade") or "").strip()
        condition = str(candidate.get("condicao") or "").strip()
        color = str(candidate.get("cor") or "").strip()
        battery = candidate.get("saude_bateria")
        price = candidate.get("preco_brl")
        if not name or not capacity or price is None:
            return None

        query_parts = [name, capacity, condition, color]
        if battery is not None:
            try:
                battery_value = float(battery)
            except (TypeError, ValueError):
                return None
            query_parts.append(f"bateria {battery_value:g}%")
        try:
            table = await method(" ".join(part for part in query_parts if part))
        except Exception:
            return None
        if not table.get("encontrado"):
            return None
        try:
            candidate_price = float(price)
            simulated_price = float(table.get("preco_avista_brl"))
        except (TypeError, ValueError):
            return None
        if not math.isclose(candidate_price, simulated_price, rel_tol=0.0, abs_tol=0.01):
            return None

        details = [name, capacity]
        if condition:
            details.append(condition.lower())
        if battery is not None:
            details.append(f"bateria {battery_value:g}%")
        details.append(format_brl(candidate_price))
        sections.append(
            f"Opção {index} — {' — '.join(details)}\n{format_installment_table(table)}"
        )

    return AgentDecision(
        reply="\n\n".join(sections),
        product_references=references,
        confidence="high",
    )

async def _try_specific_installment(
    self,
    text: str,
    history: list[dict[str, str]] | None,
) -> AgentDecision | None:
    installments = _requested_installments(text)
    if installments is None:
        if not _is_installment_selection_followup(text, history):
            return None
        installments = _last_requested_installments(history)
    if installments is None:
        return None

    query = _installment_context_query(text, history)
    if not _has_installment_product_context(query):
        return None
    multiple_models = await self._try_multi_model_installment_tables(text, history)
    if multiple_models is not None:
        return multiple_models
    method = getattr(self.cache, "simulate_all_installments", None)
    if not callable(method):
        return None
    try:
        result = await method(query)
    except Exception:
        return None
    if result.get("encontrado"):
        return AgentDecision(
            reply=format_installment_table(result),
            confidence="high",
        )
    if result.get("ambiguo"):
        both_units = await self._try_both_unit_installment_tables(
            text,
            history,
            result,
        )
        if both_units is not None:
            return both_units
        condition_tables = await self._try_ambiguous_condition_installment_tables(
            query,
            result,
        )
        if condition_tables is not None:
            return condition_tables
        return _format_ambiguous_installment_decision(result)
    if not result.get("ambiguo"):
        alternative = await self._try_unavailable_seminew_alternative(query)
        if alternative is not None:
            return alternative
        return None

async def _try_ambiguous_condition_installment_tables(
    self,
    query: str,
    result: dict[str, Any],
) -> AgentDecision | None:
    """Simulate both confirmed conditions when only condition is ambiguous."""
    candidates = result.get("candidatos") or []
    if len(candidates) != 2:
        return None

    normalized_query = _normalize(query)
    if _has_explicit_sealed_condition(normalized_query) or _has_seminovo_reference(
        normalized_query
    ):
        return None

    by_condition: dict[str, dict[str, Any]] = {}
    product_keys: set[tuple[str, str]] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return None
        condition = _normalize(str(candidate.get("condicao") or ""))
        if _has_explicit_sealed_condition(condition):
            key = "lacrado"
        elif _has_seminovo_reference(condition):
            key = "seminovo"
        else:
            return None
        if key in by_condition:
            return None
        by_condition[key] = candidate
        name = _normalize(str(candidate.get("nome") or ""))
        capacity = _normalize(str(candidate.get("capacidade") or ""))
        if not name or not capacity:
            return None
        product_keys.add((name, capacity))

    if set(by_condition) != {"seminovo", "lacrado"} or len(product_keys) != 1:
        return None

    method = getattr(self.cache, "simulate_all_installments", None)
    if not callable(method):
        return None

    sections: list[str] = []
    for condition, label in (
        ("seminovo", "Opção seminova"),
        ("lacrado", "Opção novo lacrado por encomenda"),
    ):
        try:
            table = await method(f"{query}\n{condition}")
        except Exception:
            return None
        if not table.get("encontrado"):
            return None
        if condition == "seminovo":
            battery = by_condition[condition].get("saude_bateria")
            if battery is not None:
                label += f" (bateria {float(battery):g}%)"
        sections.append(f"{label}:\n{format_installment_table(table)}")

    return AgentDecision(
        reply="\n\n".join(sections),
        confidence="high",
    )

async def _try_full_installment_table(
    self,
    text: str,
    history: list[dict[str, str]] | None,
    *,
    image_description: str | None = None,
) -> AgentDecision | None:
    if not (
        _is_full_installment_request(text)
        or _is_rate_model_followup(text, history)
        or _is_installment_model_followup(text, history)
    ):
        return None
    multiple_models = await self._try_multi_model_installment_tables(text, history)
    if multiple_models is not None:
        return multiple_models
    method = getattr(self.cache, "simulate_all_installments", None)
    if not callable(method):
        return None
    product_context = "\n".join(
        part for part in (text, image_description) if part and part.strip()
    )
    query = _installment_context_query(product_context, history)
    try:
        result = await method(query)
    except Exception:
        return None
    if not result.get("encontrado"):
        if result.get("ambiguo"):
            both_units = await self._try_both_unit_installment_tables(
                text,
                history,
                result,
            )
            if both_units is not None:
                return both_units
            condition_tables = await self._try_ambiguous_condition_installment_tables(
                query,
                result,
            )
            if condition_tables is not None:
                return condition_tables
            return _format_ambiguous_installment_decision(result)
        return await self._try_unavailable_seminew_alternative(
            query
        )
    return AgentDecision(
        reply=format_installment_table(result),
        confidence="high",
    )

async def _try_entry_installment(
    self,
    text: str,
    history: list[dict[str, str]] | None,
) -> AgentDecision | None:
    entry_amount = _extract_entry_amount(text)
    if entry_amount is None:
        return None
    normalized = _normalize(text)
    has_installment_intent = (
        _requested_installments(text) is not None
        or _is_full_installment_request(text)
        or bool(
            re.search(
                r"\b(?:parcel\w*|quantas\s+vezes|fica\s+quanto|quanto\s+fica)\b",
                normalized,
            )
        )
    )
    if not has_installment_intent:
        return None

    query = _installment_context_query(text, history)
    try:
        method = getattr(self.cache, "simulate_all_installments_with_entry", None)
        if not callable(method):
            return None
        result = await method(query, entry_amount)
        if result.get("encontrado"):
            return AgentDecision(
                reply=format_installment_table(result),
                confidence="high",
            )
        if result.get("ambiguo"):
            candidates = result.get("candidatos") or []
            if candidates and all(isinstance(candidate, dict) for candidate in candidates):
                candidate_keys: set[tuple[str, str, str, float]] = set()
                for candidate in candidates:
                    try:
                        candidate_keys.add(
                            (
                                _normalize(str(candidate.get("nome") or "")),
                                _normalize(str(candidate.get("capacidade") or "")),
                                _normalize(str(candidate.get("condicao") or "")),
                                round(float(candidate["preco_brl"]), 2),
                            )
                        )
                    except (KeyError, TypeError, ValueError):
                        candidate_keys.clear()
                        break
                if len(candidate_keys) == 1:
                    candidate = candidates[0]
                    item = InventoryItem(
                        external_id=str(candidate.get("referencia") or "entry-installment"),
                        name=str(candidate["nome"]),
                        capacity=str(candidate["capacidade"]),
                        condition=str(candidate["condicao"]),
                        price_brl=float(candidate["preco_brl"]),
                    )
                    equivalent_result = simulate_installment_table_with_entry(
                        item,
                        entry_amount,
                    )
                    if equivalent_result.get("encontrado"):
                        return AgentDecision(
                            reply=format_installment_table(equivalent_result),
                            confidence="high",
                        )
            ambiguous_decision = _format_ambiguous_installment_decision(result)
            if ambiguous_decision is not None:
                return ambiguous_decision
    except Exception:
        return None
    return None
