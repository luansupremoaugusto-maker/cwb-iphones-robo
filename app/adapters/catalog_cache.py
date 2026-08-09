from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.adapters.mercado_phone import InventoryCache, score_item
from app.adapters.mercado_phone_files import MAX_PRODUCT_PHOTOS, extract_file_urls, list_product_files
from app.config import Settings
from app.installments import (
    simulate_installment,
    simulate_installment_table,
    simulate_installment_table_with_entry,
    simulate_installment_with_entry,
)


def _normalize(value: str) -> str:
    without_accents = "".join(
        char for char in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(char)
    )
    normalized = re.sub(r"\s+", " ", without_accents).strip().lower()
    return re.sub(r"(?<=\d)(?=[a-z])", " ", normalized)


def _score_text(value: Any) -> str:
    """Normalize search text while treating punctuation as a separator."""
    return re.sub(r"[^a-z0-9]+", " ", _normalize(str(value or ""))).strip()


def _catalog_score(query: str, item: Any) -> int:
    score = score_item(query, item)
    normalized_query = _score_text(query)
    query_tokens = set(normalized_query.split())
    model_phrase = _score_text(item.name)
    model_tokens = model_phrase.split()

    # Match the complete model phrase as written. A standalone conjunction
    # such as the "e" in "branco e preto" must not select "iPhone 17 E".
    if model_phrase and re.search(rf"(?<!\w){re.escape(model_phrase)}(?!\w)", normalized_query):
        score += 500 + len(model_tokens) * 25

    # Customers often omit the brand and type "16e" or "16 pro". Treat the
    # model suffix as an exact alias too, so 16 E cannot lose to 16 Pro merely
    # because both rows contain the digit 16.
    if model_phrase.startswith("iphone "):
        model_alias = model_phrase[len("iphone ") :].strip()
        if model_alias and re.search(rf"(?<!\w){re.escape(model_alias)}(?!\w)", normalized_query):
            score += 450 + len(model_alias.split()) * 25

    # Color is part of the identity when the customer specifies it. Strip the
    # question mark and other punctuation first, then reward an exact match so
    # "16e preto?" cannot tie with the white unit of the same model.
    item_color = _score_text(getattr(item, "color", None) or getattr(item, "colors", None))
    if item_color and re.search(rf"(?<!\w){re.escape(item_color)}(?!\w)", normalized_query):
        score += 300 + len(item_color.split()) * 20

    if query_tokens.intersection({"normal", "comum", "base"}):
        variants = {"air", "pro", "max", "mini", "plus", "e"}
        if not any(token in variants for token in model_tokens):
            score += 150

    if item.source == "google_sheets":
        score += 80
    return score


def _is_device_item(item: Any) -> bool:
    """Return whether an item belongs to the customer-facing device catalog.

    For Mercado Phone, tipoProdutoDescricao=Celular is intentionally the
    source of truth. The shop groups iPhones, AirPods, Apple Watch, iPad and
    MacBook in that category, so names must not narrow this decision. The BOT
    sheet has no equivalent product-type column, so its Apple-family names are
    retained while obvious accessories remain excluded.
    """

    name = _normalize(getattr(item, "name", ""))
    category = _normalize(getattr(item, "category", ""))
    excluded = (
        "pelicula",
        "capa ",
        "capinha",
        "case",
        "fonte ",
        "cabo ",
        "carregador",
        "protetor",
        "suporte",
    )
    if any(marker in name for marker in excluded):
        return False

    source = getattr(item, "source", "")
    if source == "mercado_phone":
        return category == "celular" or category.startswith("celular ") or category in {
            "celulares",
            "aparelho celular",
            "aparelhos celulares",
            "aparelho de celular",
        }

    if source == "google_sheets":
        return any(marker in name for marker in ("iphone", "ipad", "macbook", "apple watch", "airpods"))

    # Preserve predictable behavior for test fixtures or other approved
    # catalog sources that already expose a device category.
    return category == "celular" or category.startswith("celular ") or any(
        marker in name for marker in ("iphone", "ipad", "macbook", "apple watch", "airpods")
    )


def _is_excluded_query(query: str) -> bool:
    normalized = _normalize(query)
    return any(
        marker in normalized
        for marker in ("pelicula", "capa", "capinha", "case", "fonte", "cabo", "carregador", "protetor", "suporte")
    )


def _is_sale_status(value: Any) -> bool:
    normalized = _normalize(str(value or ""))
    # Keep the short legacy label for older test/API responses, but never use
    # quantity alone: Laboratório, teste and other commercial states are not
    # customer inventory even when their quantity is positive.
    return normalized == "disponivel" or normalized.startswith("disponivel para venda")


def _is_available_item(item: Any) -> bool:
    if not _is_sale_status(getattr(item, "availability", "")):
        return False
    quantity = getattr(item, "quantity", None)
    if quantity is None:
        return True
    try:
        return float(quantity) > 0
    except (TypeError, ValueError):
        return False


def _display_capacity(item: Any) -> str | None:
    capacity = str(getattr(item, "capacity", "") or "").strip()
    if capacity and capacity != "-":
        return capacity
    text = f"{getattr(item, 'name', '')} {getattr(item, 'description', '')}"
    match = re.search(r"\b(\d+(?:[.,]\d+)?\s*(?:GB|TB))\b", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", match.group(1).upper()) if match else None


def _display_condition(item: Any, sealed: bool = False) -> str:
    if sealed or getattr(item, "source", "") == "google_sheets":
        return "novo lacrado"
    return str(getattr(item, "condition", "") or "seminovo").strip().lower()


def _as_public_quantity(value: float | None) -> int | float | None:
    if value is None:
        return None
    number = float(value)
    return int(number) if number.is_integer() else round(number, 2)


def _split_colors(item: Any) -> list[str]:
    raw = getattr(item, "color", None) or getattr(item, "colors", None)
    if isinstance(raw, (list, tuple, set)):
        values = [str(value).strip() for value in raw]
    else:
        values = [part.strip() for part in re.split(r"\s*[|;/]\s*", str(raw or ""))]
    result: list[str] = []
    for value in values:
        if value and value != "-" and value not in result:
            result.append(value)
    return result


class StoreCatalogCache(InventoryCache):
    """Catalog cache with fixed rates, complete availability and API photos."""

    def __init__(self, client: Any, settings: Settings, *args: Any, **kwargs: Any):
        super().__init__(client, settings, *args, **kwargs)
        self._remote_photo_cache: dict[str, list[str]] = {}

    def _attach_photos(self, item: Any) -> Any:
        # Photo URLs are populated only by Mercado Phone's file endpoint. This
        # method keeps only public HTTPS URLs already attached to the item.
        existing = [
            url
            for url in getattr(item, "photo_urls", [])
            if isinstance(url, str) and url.lower().startswith("https://")
        ]
        urls = list(dict.fromkeys(existing))[:MAX_PRODUCT_PHOTOS]
        if urls == getattr(item, "photo_urls", []):
            return item
        return item.model_copy(update={"photo_urls": urls})

    async def _attach_remote_photos(self, item: Any) -> Any:
        item = self._attach_photos(item)
        if item is None or getattr(item, "photo_urls", []):
            return item
        if getattr(item, "source", "") != "mercado_phone" or not _is_device_item(item):
            return item

        product_id = str(getattr(item, "external_id", "") or "")
        if not product_id:
            return item
        if product_id in self._remote_photo_cache:
            urls = self._remote_photo_cache[product_id]
        else:
            try:
                payload = await list_product_files(self.client, product_id, origin=1)
                urls = extract_file_urls(payload)
            except Exception:
                # A photo failure must not break an otherwise valid product
                # answer. Do not cache failures so a later request can retry.
                return item
            self._remote_photo_cache[product_id] = urls

        return item.model_copy(update={"photo_urls": urls}) if urls else item

    async def search(self, query: str, limit: int = 5):
        if _is_excluded_query(query):
            return []
        # Fetch enough candidates before re-ranking so an exact base model is
        # not discarded by a close Pro/Max variant. Unavailable Mercado Phone
        # states are removed before every customer-facing search path.
        candidates = [
            item
            for item in await super().search(query, limit=max(limit, 300))
            if _is_device_item(item)
            and (getattr(item, "source", "") != "mercado_phone" or _is_available_item(item))
        ]
        ranked = sorted(
            candidates,
            key=lambda item: (_catalog_score(query, item), item.name, item.capacity or ""),
            reverse=True,
        )
        selected = [item for item in ranked if _catalog_score(query, item) > 0][:limit]
        return [await self._attach_remote_photos(item) for item in selected]

    async def get(self, product_id: str):
        item = await super().get(product_id)
        if item is None or not _is_device_item(item):
            return None
        if getattr(item, "source", "") == "mercado_phone" and not _is_available_item(item):
            return None
        return await self._attach_remote_photos(item)

    @staticmethod
    def _individualize(items: list[Any], *, sealed: bool) -> list[dict[str, Any]]:
        """Expose one entry per catalog row instead of merging equal models."""
        entries: list[dict[str, Any]] = []
        for item in items:
            if not _is_device_item(item):
                continue
            if not sealed and not _is_available_item(item):
                continue
            name = str(getattr(item, "name", "") or "").strip()
            colors = _split_colors(item)
            price = getattr(item, "price_brl", None)
            entries.append(
                {
                    "nome": name,
                    "capacidade": _display_capacity(item),
                    "condicao": _display_condition(item, sealed=sealed),
                    "quantidade": None if sealed else _as_public_quantity(getattr(item, "quantity", None)),
                    "precos_brl": [round(float(price), 2)] if price is not None else [],
                    "cores": colors,
                    "cor": getattr(item, "color", None) or (colors[0] if len(colors) == 1 else None),
                    "saude_bateria": getattr(item, "battery_health", None),
                    "fotos_disponiveis": min(MAX_PRODUCT_PHOTOS, len(getattr(item, "photo_urls", []) or [])),
                }
            )
        return sorted(
            entries,
            key=lambda entry: (
                str(entry.get("nome") or "").lower(),
                str(entry.get("capacidade") or ""),
                str(entry.get("cor") or ""),
                str(entry.get("condicao") or ""),
            ),
        )

    async def list_available_products(self) -> dict[str, Any]:
        await self.ensure_fresh()
        sealed_items: list[Any] = []
        if self.sealed_cache is not None:
            ensure_fresh = getattr(self.sealed_cache, "ensure_fresh", None)
            if callable(ensure_fresh):
                try:
                    await ensure_fresh()
                except Exception:
                    # A cached BOT snapshot can still be used; its source is
                    # explicitly marked as by-order rather than in-stock.
                    pass
            sealed_items = [self._attach_photos(item) for item in getattr(self.sealed_cache, "items", [])]

        # Do not request every attachment just to build the complete list.
        # Photos are loaded on demand when the customer names a product.
        seminovos = self._individualize(self.items, sealed=False)
        lacrados = self._individualize(sealed_items, sealed=True)
        return {
            "encontrado": bool(seminovos or lacrados),
            "seminovos": seminovos,
            "lacrados": lacrados,
            "total_modelos": len(seminovos) + len(lacrados),
        }

    async def _select_priced_candidate(self, query: str) -> tuple[Any | None, dict[str, Any] | None]:
        candidates = [item for item in await self.search(query, limit=10) if item.price_brl is not None]
        if not candidates:
            return None, {"encontrado": False, "motivo": "Produto com preço confirmado não localizado"}

        if len(candidates) > 1 and _catalog_score(query, candidates[0]) == _catalog_score(query, candidates[1]):
            return None, {
                "encontrado": False,
                "ambiguo": True,
                "candidatos": [
                    {"nome": item.name, "capacidade": item.capacity, "preco_brl": item.price_brl}
                    for item in candidates[:3]
                ],
            }
        return candidates[0], None

    @staticmethod
    def _add_price_source(result: dict[str, Any], item: Any) -> dict[str, Any]:
        result["condicao"] = item.condition
        result["fonte_preco"] = (
            "planilha BOT - novo lacrado"
            if item.source == "google_sheets"
            else "catálogo Mercado Phone"
        )
        return result

    async def simulate_installment(self, query: str, installments: int) -> dict[str, Any]:
        item, error = await self._select_priced_candidate(query)
        if error:
            return error
        result = simulate_installment(item, int(installments))
        return self._add_price_source(result, item)

    async def simulate_all_installments(self, query: str) -> dict[str, Any]:
        item, error = await self._select_priced_candidate(query)
        if error:
            return error
        result = simulate_installment_table(item)
        return self._add_price_source(result, item)

    async def simulate_installment_with_entry(
        self,
        query: str,
        entry_amount_brl: float,
        installments: int,
    ) -> dict[str, Any]:
        item, error = await self._select_priced_candidate(query)
        if error:
            return error
        result = simulate_installment_with_entry(item, entry_amount_brl, int(installments))
        return self._add_price_source(result, item)

    async def simulate_all_installments_with_entry(
        self,
        query: str,
        entry_amount_brl: float,
    ) -> dict[str, Any]:
        item, error = await self._select_priced_candidate(query)
        if error:
            return error
        result = simulate_installment_table_with_entry(item, entry_amount_brl)
        return self._add_price_source(result, item)
