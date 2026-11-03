"""Photos response handlers for AgentService."""

from __future__ import annotations

from app.agent import (
    AgentDecision,
    MAX_PRODUCT_PHOTOS,
    PRODUCT_PHOTO_HANDOFF_REASON,
    SEALED_PHOTO_REPLY,
    _active_photo_price_request,
    _capacity_key,
    _catalog_family,
    _catalog_score,
    _current_catalog_color,
    _current_catalog_context,
    _extract_bare_catalog_model_reference,
    _has_photo_request_in_history,
    _has_product_reference,
    _has_requested_catalog_color,
    _is_available_item,
    _is_broad_promotion_photo_request,
    _is_device_item,
    _is_made_to_order_sealed_item,
    _is_photo_context_followup,
    _is_photo_request,
    _is_photo_retry_request,
    _is_sealed_item,
    _is_sealed_photo_request,
    _is_standalone_photo_followup,
    _matches_requested_model,
    _model_key,
    _normalize,
    _photo_price_selectors,
    _product_context_query,
    _requested_battery_health,
    _requested_capacity_keys,
    _requested_iphone_model_floor,
    _requested_iphone_model_keys,
    _requested_photo_condition,
    _strip_historical_accessory_terms_from_photo_query,
)

async def _try_product_photos(
    self,
    text: str,
    history: list[dict[str, str]] | None,
    *,
    image_description: str | None = None,
) -> AgentDecision | None:
    if (
        not _is_photo_request(text)
        and not _is_standalone_photo_followup(text, history)
        and not _is_photo_context_followup(text, history)
    ):
        return None
    if _is_photo_retry_request(text) and not _has_photo_request_in_history(
        history, current_text=text
    ):
        return None
    if _is_sealed_photo_request(text):
        return AgentDecision(reply=SEALED_PHOTO_REPLY, confidence="high")
    if _is_broad_promotion_photo_request(text):
        try:
            promotional_items = await self.cache.search("iPhone seminovo", limit=300)
        except Exception:
            promotional_items = []

        photo_items: list[Any] = []
        approved_urls: list[str] = []
        for item in promotional_items:
            item_text = (
                f"{getattr(item, 'name', '')} {getattr(item, 'description', '')} "
                f"{getattr(item, 'search_text', '')}"
            )
            if (
                _catalog_family(item_text) != "iphone"
                or _is_sealed_item(item)
                or (
                    getattr(item, "source", None) == "mercado_phone"
                    and not _is_available_item(item)
                )
            ):
                continue
            urls = [
                url
                for url in (getattr(item, "photo_urls", []) or [])
                if isinstance(url, str) and url.lower().startswith("https://")
            ]
            if not urls:
                continue
            photo_items.append(item)
            approved_urls.extend(urls)

        approved_urls = list(dict.fromkeys(approved_urls))[:MAX_PRODUCT_PHOTOS]
        if approved_urls:
            references = [
                str(getattr(item, "external_id", ""))
                for item in photo_items
                if getattr(item, "external_id", "")
            ]
            return AgentDecision(
                reply="Claro! Seguem as fotos dos iPhones seminovos disponíveis na promoção.",
                image_urls=approved_urls,
                product_references=references,
                confidence="high",
            )
        return AgentDecision(
            reply=(
                "No momento não encontrei fotos cadastradas dos iPhones seminovos "
                "disponíveis na promoção."
            ),
            confidence="medium",
        )
    current_query = _current_catalog_context(text, image_description)
    if not _has_product_reference(_normalize(current_query)):
        # A current photo request such as "fotos do 13" names a model even
        # when the customer omits the word "iPhone". Promote that shorthand
        # before inheriting a previous assistant list, which may mention
        # neighboring Pro and Pro Max variants.
        bare_model = _extract_bare_catalog_model_reference(current_query)
        if bare_model and _is_photo_request(current_query):
            current_query = f"{bare_model} {current_query}".strip()
    if _is_photo_context_followup(text, history) and not _has_product_reference(
        _normalize(current_query)
    ):
        followup_model = _extract_bare_catalog_model_reference(current_query)
        if followup_model:
            current_query = f"{followup_model} {current_query}".strip()
    query = _product_context_query(current_query, history)
    query = _strip_historical_accessory_terms_from_photo_query(
        query,
        current_query,
        request_text=text,
        image_description=image_description,
    )
    # A previous assistant answer can list several capacities. When the
    # current turn contains one explicit choice, make that choice the
    # first capacity seen by the photo resolver instead of letting the
    # historical list select its first entry.
    explicit_capacity_keys = _requested_capacity_keys(text)
    if len(explicit_capacity_keys) != 1:
        explicit_capacity_keys = _requested_capacity_keys(image_description or "")
    if len(explicit_capacity_keys) == 1:
        query = f"capacidade solicitada: {explicit_capacity_keys[0]}\n{query}".strip()
    current_color = _current_catalog_color(
        text,
        list(getattr(self.cache, "items", []) or []),
    )
    if current_color is None and image_description:
        current_color = _current_catalog_color(
            image_description,
            list(getattr(self.cache, "items", []) or []),
        )
    if current_color:
        query = f"foto_cor_atual: {current_color}\n{query}".strip()
    requested_models = _requested_iphone_model_keys(query)
    requested_capacities = _requested_capacity_keys(query)
    requested_photo_prices = _photo_price_selectors(
        _active_photo_price_request(text, history)
    )
    requested_condition = _requested_photo_condition(query)
    photo_model_batch_request = (
        len(requested_photo_prices) > 1
        or len(requested_models) > 1
        or _requested_iphone_model_floor(query) is not None
    )
    return_all_matching_photo_units = (
        len(requested_photo_prices) > 1
        or (
            photo_model_batch_request
            and (
                _requested_iphone_model_floor(query) is not None
                or not requested_capacities
            )
            and _requested_battery_health(query) is None
            and not _has_requested_catalog_color(
                query,
                list(getattr(self.cache, "items", []) or []),
            )
        )
    )
    finder = getattr(self.cache, "find_product_photos", None)

    def condition_matches(item: Any) -> bool:
        if requested_condition is None:
            return True
        is_sealed = _is_sealed_item(item)
        return is_sealed if requested_condition == "sealed" else not is_sealed

    def photo_lookup_failed(item: Any) -> bool:
        lookup_failed = getattr(self.cache, "product_photo_lookup_failed", None)
        product_id = str(getattr(item, "external_id", "") or "")
        return bool(
            product_id
            and callable(lookup_failed)
            and lookup_failed(product_id)
        )

    def photo_item_label(item: Any) -> str:
        product_parts = [
            str(getattr(item, "name", "") or "esse aparelho").strip()
        ]
        color = getattr(item, "color", None) or getattr(item, "colors", None)
        if isinstance(color, (list, tuple, set)):
            color = ", ".join(str(value).strip() for value in color if value)
        for detail in (getattr(item, "capacity", None), color):
            detail = str(detail or "").strip()
            if detail and _normalize(detail) not in _normalize(" ".join(product_parts)):
                product_parts.append(detail)
        return " ".join(part for part in product_parts if part)

    def photo_item_label_list(items: list[Any]) -> str:
        labels = [photo_item_label(item) for item in items]
        if len(labels) > 1:
            return f"{', '.join(labels[:-1])} e {labels[-1]}"
        return labels[0] if labels else "os aparelhos indicados"

    def exact_candidates(items: list[Any]) -> list[Any]:
        candidates: list[Any] = []
        seen: set[str] = set()
        for item in items:
            external_id = str(getattr(item, "external_id", "") or "")
            if external_id in seen:
                continue
            if not _is_device_item(item):
                continue
            if (
                getattr(item, "source", None) == "mercado_phone"
                and not _is_available_item(item)
            ):
                continue
            if not condition_matches(item) or not _matches_requested_model(query, item):
                continue
            if requested_capacities and _capacity_key(
                getattr(item, "capacity", None) or getattr(item, "name", "")
            ) not in requested_capacities:
                continue
            seen.add(external_id)
            candidates.append(item)

        scored = [
            (_catalog_score(query, item, match_price=True), item)
            for item in candidates
        ]
        positive = [(score, item) for score, item in scored if score > 0]
        if not positive:
            return []
        best_score = max(score for score, _item in positive)
        return [item for score, item in positive if score == best_score]

    def multiple_photo_matches(
        items: list[Any],
        *,
        include_all_matching_units: bool = False,
    ) -> list[Any]:
        """Select requested photo units without losing valid alternatives."""
        scored: list[tuple[int, Any]] = []
        seen: set[str] = set()
        for item in items:
            external_id = str(getattr(item, "external_id", "") or "")
            if external_id in seen:
                continue
            if not _is_device_item(item):
                continue
            if (
                getattr(item, "source", None) == "mercado_phone"
                and not _is_available_item(item)
            ):
                continue
            if not condition_matches(item) or not _matches_requested_model(query, item):
                continue
            if requested_capacities and _capacity_key(
                getattr(item, "capacity", None) or getattr(item, "name", "")
            ) not in requested_capacities:
                continue
            if len(requested_photo_prices) > 1:
                try:
                    item_price_cents = round(
                        float(getattr(item, "price_brl", None)) * 100
                    )
                except (TypeError, ValueError):
                    item_price_cents = None
                if item_price_cents not in requested_photo_prices:
                    continue
            score = _catalog_score(query, item, match_price=True)
            if score <= 0:
                continue
            seen.add(external_id)
            scored.append((score, item))

        if include_all_matching_units:
            selected = [item for _score, item in scored]
        else:
            selected = []
            for requested_model in requested_models:
                model_matches = [
                    (score, item)
                    for score, item in scored
                    if _model_key(getattr(item, "name", "")) == requested_model
                ]
                capacities = requested_capacities or (None,)
                for capacity in capacities:
                    capacity_matches = [
                        (score, item)
                        for score, item in model_matches
                        if capacity is None
                        or _capacity_key(
                            getattr(item, "capacity", None) or getattr(item, "name", "")
                        )
                        == capacity
                    ]
                    if not capacity_matches:
                        continue
                    best_score = max(score for score, _item in capacity_matches)
                    selected.extend(
                        item for score, item in capacity_matches if score == best_score
                    )

        selected_by_id: dict[str, Any] = {}
        for item in selected:
            external_id = str(getattr(item, "external_id", "") or "")
            if external_id:
                selected_by_id[external_id] = item
        return list(selected_by_id.values())

    def ambiguous_reply(items: list[Any]) -> AgentDecision:
        first = items[0]
        model = str(getattr(first, "name", "esse aparelho") or "esse aparelho").strip()
        condition = "novo lacrado" if _is_sealed_item(first) else "seminovo"
        lines: list[str] = []
        for item in items:
            capacity = str(getattr(item, "capacity", None) or "capacidade não informada").strip()
            color = str(
                getattr(item, "color", None)
                or getattr(item, "colors", None)
                or "cor não informada"
            ).strip()
            photo_status = (
                "fotos cadastradas"
                if getattr(item, "photo_urls", [])
                else "fotos a confirmar"
            )
            lines.append(f"• {capacity} — {color} — {photo_status}")
        return AgentDecision(
            reply=(
                f"Encontrei o {model} {condition} em mais de uma opção:\n"
                + "\n".join(lines)
                + "\nQual capacidade você quer que eu envie nas fotos?"
            ),
            confidence="high",
        )

    if photo_model_batch_request:
        try:
            items = await self.cache.search(query, limit=300)
        except Exception:
            return None
        items = multiple_photo_matches(
            items,
            include_all_matching_units=return_all_matching_photo_units,
        )
    elif callable(finder):
        try:
            selected = await finder(query)
        except Exception:
            return None
        if (
            selected is None
            and current_color
            and _requested_photo_condition(current_query) is None
        ):
            # The preceding assistant card can carry a stale condition
            # label after the catalog row changes. Preserve that label on
            # the first lookup, but let the current explicit color select
            # the live item when the constrained lookup finds nothing.
            relaxed_query = _product_context_query(
                current_query,
                history,
                strip_assistant_constraints=True,
            )
            if len(explicit_capacity_keys) == 1:
                relaxed_query = (
                    f"capacidade solicitada: {explicit_capacity_keys[0]}\n{relaxed_query}"
                ).strip()
            relaxed_query = f"foto_cor_atual: {current_color}\n{relaxed_query}".strip()
            if relaxed_query != query:
                try:
                    selected = await finder(relaxed_query)
                except Exception:
                    return None
                if selected is not None:
                    query = relaxed_query
                    requested_condition = _requested_photo_condition(query)
        if selected is None:
            fallback: list[Any] = []
            sealed_cache = getattr(self.cache, "sealed_cache", None)
            if sealed_cache is not None:
                try:
                    ensure_fresh = getattr(sealed_cache, "ensure_fresh", None)
                    if callable(ensure_fresh):
                        await ensure_fresh()
                    search_sealed = getattr(sealed_cache, "search", None)
                    if callable(search_sealed):
                        fallback.extend(await search_sealed(query, limit=5))
                    else:
                        fallback.extend(list(getattr(sealed_cache, "items", []))[:5])
                except Exception:
                    pass
            try:
                fallback.extend(await self.cache.search(query, limit=5))
            except Exception:
                pass
            items = exact_candidates(fallback)
            if requested_condition is None:
                used_items = [item for item in items if not _is_sealed_item(item)]
                if used_items:
                    items = used_items
            if not items:
                if requested_condition == "sealed":
                    return AgentDecision(reply=SEALED_PHOTO_REPLY, confidence="high")
                alternative = await self._try_unavailable_seminew_alternative(
                    query,
                    requested_label=_extract_bare_catalog_model_reference(text),
                )
                if alternative is not None:
                    return alternative
                requested_label = _extract_bare_catalog_model_reference(text) or "esse produto"
                return AgentDecision(
                    reply=(
                        f"No momento, não localizei o {requested_label} disponível no estoque. "
                        "Posso verificar outro modelo ou capacidade?"
                    ),
                    confidence="medium",
                )
        else:
            items = [selected]
    else:
        try:
            items = await self.cache.search(query, limit=5)
        except Exception:
            return None
    items = [item for item in items if condition_matches(item)]
    if items:
        if photo_model_batch_request:
            photo_rows = [
                (
                    item,
                    [
                        url
                        for url in (getattr(item, "photo_urls", []) or [])
                        if isinstance(url, str) and url.lower().startswith("https://")
                    ],
                )
                for item in items
            ]
            available_items = [item for item, urls in photo_rows if urls]
            confirmed_missing = [
                item
                for item, urls in photo_rows
                if not urls and not photo_lookup_failed(item)
            ]
            failed_lookup = [
                item
                for item, urls in photo_rows
                if not urls and photo_lookup_failed(item)
            ]
            approved_urls = list(
                dict.fromkeys(url for _item, urls in photo_rows for url in urls)
            )[:MAX_PRODUCT_PHOTOS]
            product_references = [
                str(getattr(item, "external_id", ""))
                for item in items
                if getattr(item, "external_id", "")
            ]
            if not confirmed_missing and not failed_lookup:
                return AgentDecision(
                    reply=(
                        "Claro! Seguem as fotos dos aparelhos que você indicou: "
                        f"{photo_item_label_list(available_items)}."
                    ),
                    image_urls=approved_urls,
                    product_references=product_references,
                    confidence="high",
                )
            reply_parts: list[str] = []
            if available_items:
                reply_parts.append(
                    "Claro! Seguem as fotos cadastradas de "
                    f"{photo_item_label_list(available_items)}."
                )
            if confirmed_missing:
                reply_parts.append(
                    "Não há fotos cadastradas para "
                    f"{photo_item_label_list(confirmed_missing)}. "
                    "Vou encaminhar o pedido para um atendente confirmar as fotos."
                )
            if failed_lookup:
                reply_parts.append(
                    "Não consegui confirmar as fotos de "
                    f"{photo_item_label_list(failed_lookup)} agora. "
                    "Tente novamente em instantes."
                )
            return AgentDecision(
                reply=" ".join(reply_parts),
                handoff=bool(confirmed_missing),
                handoff_reason=(
                    PRODUCT_PHOTO_HANDOFF_REASON if confirmed_missing else None
                ),
                image_urls=approved_urls,
                product_references=product_references,
                confidence="medium" if failed_lookup else "high",
            )
        if len(items) > 1:
            capacities = {
                _capacity_key(getattr(item, "capacity", None) or getattr(item, "name", ""))
                for item in items
            }
            approved_urls = [
                url
                for item in items
                for url in (getattr(item, "photo_urls", []) or [])
                if isinstance(url, str) and url
            ]
            if (
                len(capacities) == 1
                and None not in capacities
                and not any(_is_sealed_item(item) for item in items)
                and not _has_requested_catalog_color(current_query, items)
                and len(approved_urls) == sum(
                    len(getattr(item, "photo_urls", []) or []) for item in items
                )
                and approved_urls
            ):
                return AgentDecision(
                    reply=(
                        f"Claro! Seguem as fotos do {items[0].name} "
                        f"{items[0].capacity}."
                    ),
                    image_urls=list(dict.fromkeys(approved_urls))[:MAX_PRODUCT_PHOTOS],
                    product_references=[item.external_id for item in items],
                    confidence="high",
                )
            return ambiguous_reply(items)
        selected = items[0]
        if _is_made_to_order_sealed_item(selected):
            return AgentDecision(reply=SEALED_PHOTO_REPLY, confidence="high")
        urls = list(dict.fromkeys(getattr(selected, "photo_urls", []) or []))[:MAX_PRODUCT_PHOTOS]
        if urls:
            return AgentDecision(
                reply=f"Claro! Seguem as fotos do {selected.name}{f' {selected.capacity}' if selected.capacity else ''}.",
                image_urls=urls,
                product_references=[selected.external_id],
                confidence="high",
            )
        product_label = photo_item_label(selected)
        product_reference = str(getattr(selected, "external_id", "") or "")
        if photo_lookup_failed(selected):
            return AgentDecision(
                reply=(
                    f"Não consegui confirmar as fotos do {product_label} agora. "
                    "Tente novamente em instantes."
                ),
                confidence="low",
                product_references=[product_reference] if product_reference else [],
            )
        return AgentDecision(
            reply=(
                f"Encontrei o {product_label}, mas ainda não há fotos cadastradas para essa opção. "
                "Vou encaminhar o pedido para um atendente confirmar as fotos."
            ),
            handoff=True,
            handoff_reason=PRODUCT_PHOTO_HANDOFF_REASON,
            confidence="high",
            product_references=[product_reference] if product_reference else [],
        )
    return None
