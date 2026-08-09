from __future__ import annotations

import asyncio
import json
import re
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.schemas import InventoryItem


INVENTORY_CACHE_VERSION = 2


class MercadoPhoneError(RuntimeError):
    def __init__(self, message: str, status_code: int = 0, retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _parse_retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
    return None


class MercadoPhoneClient:
    """Read-only Mercado Phone REST client."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.mercado_phone_base_url.rstrip("/"),
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.settings.mercado_phone_api_key:
            headers["X-API-Key"] = self.settings.mercado_phone_api_key
        headers["X-Unit-Id"] = str(self.settings.mercado_system_unit_id)
        return headers

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.settings.mercado_phone_api_key:
            raise MercadoPhoneError("MERCADO_PHONE_API_KEY não configurada")

        for attempt in range(3):
            try:
                response = await self._client.get(path, params=params, headers=self.headers)
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise MercadoPhoneError(f"Falha de transporte Mercado Phone: {exc}") from exc
                await asyncio.sleep(0.25 * (attempt + 1))
                continue

            if response.status_code in {429, 502, 503, 504} and attempt < 2:
                retry_after = _parse_retry_after(response)
                await asyncio.sleep(min(retry_after or 0.25 * (attempt + 1), 4.0))
                continue

            if response.status_code >= 400:
                try:
                    body = response.json()
                except ValueError:
                    body = response.text[:500]
                detail = body.get("detail", body) if isinstance(body, dict) else body
                raise MercadoPhoneError(
                    f"Mercado Phone HTTP {response.status_code}: {detail}",
                    status_code=response.status_code,
                    retry_after=_parse_retry_after(response),
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise MercadoPhoneError("Mercado Phone retornou JSON inválido", response.status_code) from exc
            if not isinstance(data, dict):
                raise MercadoPhoneError("Mercado Phone retornou um formato inesperado", response.status_code)
            return data

        raise MercadoPhoneError("Mercado Phone não respondeu após as tentativas")

    async def list_stores(self) -> dict[str, Any]:
        return await self._get("/api/v1/stores")

    async def fetch_inventory_page(self, page: int = 1, limit: int | None = None) -> dict[str, Any]:
        page_limit = min(limit or self.settings.mercado_page_limit, 300)
        return await self._get("/api/v1/inventory", params={"page": page, "limit": page_limit})

    async def fetch_all_inventory(self) -> list[InventoryItem]:
        limit = min(self.settings.mercado_page_limit, 300)
        page = 1
        collected: list[InventoryItem] = []
        total: int | None = None

        while True:
            payload = await self.fetch_inventory_page(page=page, limit=limit)
            raw_items = payload.get("items", [])
            if not isinstance(raw_items, list):
                raise MercadoPhoneError("Resposta de estoque sem lista items")
            if total is None:
                try:
                    total = int(payload.get("total", len(raw_items)))
                except (TypeError, ValueError):
                    total = len(raw_items)
            collected.extend(normalize_inventory_item(item) for item in raw_items if isinstance(item, dict))
            returned_limit = int(payload.get("limit", limit) or limit)
            if not raw_items or len(collected) >= total or len(raw_items) < returned_limit:
                break
            page += 1

        return [item for item in collected if item.external_id and item.name]


def _as_float(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^0-9,.-]", "", str(value).strip())
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _normalize_text(value: str) -> str:
    without_accents = "".join(
        char for char in unicodedata.normalize("NFKD", str(value or "")) if not unicodedata.combining(char)
    )
    normalized = re.sub(r"\s+", " ", without_accents.replace("\n", " ")).strip().lower()
    # The API commonly stores "IPHONE 16 E", while customers type "iPhone 16e".
    # Also normalize forms such as 128GB and 17Pro for the same search behavior.
    return re.sub(r"(?<=\d)(?=[a-z])", " ", normalized)


def _first_raw_value(raw: dict[str, Any], *keys: str) -> Any:
    sources: list[dict[str, Any]] = [raw]
    for parent in ("produto", "aparelho", "item"):
        value = raw.get(parent)
        if isinstance(value, dict):
            sources.append(value)
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is not None and value != "":
                return value
    return None


def _text_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("descricao", "descrição", "nome", "name", "description", "label", "valor"):
            if value.get(key) not in (None, ""):
                return str(value[key]).strip()
        return ""
    if isinstance(value, (list, tuple, set)):
        return " | ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _clean_field(value: Any) -> str | None:
    text = _text_value(value)
    return text if text and text != "-" else None


def _extract_capacity(raw: dict[str, Any], name: str, description: str) -> str | None:
    direct = _clean_field(_first_raw_value(raw, "capacidade", "memoria", "memória", "armazenamento"))
    text = direct or ""
    if not text:
        match = re.search(r"\b(\d+(?:[.,]\d+)?\s*(?:GB|TB))\b", f"{name} {description}", flags=re.IGNORECASE)
        text = match.group(1) if match else ""
    return re.sub(r"\s+", " ", text).strip().upper() if text else None


def _extract_condition(raw: dict[str, Any], description: str) -> str | None:
    direct = _clean_field(
        _first_raw_value(raw, "condicao", "condição", "condicaoDescricao", "condiçãoDescricao", "estado", "estadoDescricao")
    )
    if direct:
        return direct
    match = re.search(r"(?i)\b(?:estado|condi(?:c|ç)[aã]o)\s*[:=-]\s*([^\-\n]+)", description)
    return match.group(1).strip() if match else None


def _extract_color(raw: dict[str, Any], name: str, description: str) -> str | None:
    direct = _clean_field(
        _first_raw_value(
            raw,
            "cor",
            "corDescricao",
            "corDescrição",
            "corNome",
            "color",
            "aparelhoCor",
            "aparelhoCorDescricao",
        )
    )
    if direct:
        return direct

    segments = [part.strip() for part in re.split(r"\s+-\s+", description) if part.strip()]
    normalized_name = _normalize_text(name)
    for index, segment in enumerate(segments):
        if _normalize_text(segment) != normalized_name:
            continue
        for candidate in segments[index + 1 :]:
            normalized_candidate = _normalize_text(candidate)
            if re.search(r"\b\d+(?:[.,]\d+)?\s*(?:gb|tb)\b", candidate, flags=re.IGNORECASE):
                break
            if normalized_candidate.startswith(("estado:", "condicao:", "condição:", "imei", "sn:", "serial")):
                break
            if candidate and not candidate.isdigit():
                return candidate

    capacity_match = re.search(r"\b\d+(?:[.,]\d+)?\s*(?:gb|tb)\b", description, flags=re.IGNORECASE)
    if capacity_match:
        prefix = description[: capacity_match.start()]
        candidates = [part.strip() for part in re.split(r"\s+-\s+", prefix) if part.strip()]
        for candidate in reversed(candidates):
            normalized_candidate = _normalize_text(candidate)
            if candidate.isdigit() or normalized_candidate == normalized_name:
                continue
            if not normalized_candidate.startswith(("estado:", "condicao:", "condição:")):
                return candidate
    return None


def _extract_battery_health(raw: dict[str, Any], description: str) -> float | None:
    direct = _as_float(
        _first_raw_value(
            raw,
            "saudeBateria",
            "saúdeBateria",
            "saudeDaBateria",
            "saúdeDaBateria",
            "saude_bateria",
            "saudeBateriaPercentual",
            "percentualSaudeBateria",
            "bateriaSaude",
            "batteryHealth",
            "healthBattery",
        )
    )
    if direct is not None and 0 <= direct <= 100:
        return direct
    match = re.search(
        r"(?i)(?:sa[uú]de(?:\s+da)?\s+bateria|battery\s*health)\s*[:=-]?\s*(\d{1,3}(?:[.,]\d+)?)\s*%?",
        description,
    )
    value = _as_float(match.group(1)) if match else None
    return value if value is not None and 0 <= value <= 100 else None


def _availability_text(raw: dict[str, Any]) -> str | None:
    value = _first_raw_value(
        raw,
        "produtoDisponibilidadeDescricao",
        "disponibilidadeDescricao",
        "disponibilidadeDescrição",
        "produtoDisponibilidadeNome",
        "disponibilidade",
        "produtoDisponibilidade",
    )
    return _clean_field(value)


def _availability_id(raw: dict[str, Any]) -> str | None:
    value = _first_raw_value(raw, "produtoDisponibilidadeId", "disponibilidadeId", "produto_disponibilidade_id")
    if isinstance(value, dict):
        value = value.get("id") or value.get("codigo")
    return str(value) if value not in (None, "") else None


def normalize_inventory_item(raw: dict[str, Any]) -> InventoryItem:
    name = str(raw.get("aparelhoDescricao") or raw.get("descricao") or "").strip()
    description = str(raw.get("descricao") or name).strip()
    category = raw.get("tipoProdutoDescricao") or raw.get("tipoProdutoNome")
    color = _extract_color(raw, name, description)
    capacity = _extract_capacity(raw, name, description)
    condition = _extract_condition(raw, description)
    battery_health = _extract_battery_health(raw, description)
    availability = _availability_text(raw)
    availability_id = _availability_id(raw)
    search_parts = [
        name,
        description,
        str(category or ""),
        str(raw.get("sku") or ""),
        str(raw.get("codigoProduto") or ""),
        str(raw.get("aparelhoId") or ""),
        str(color or ""),
        str(capacity or ""),
        str(condition or ""),
    ]
    return InventoryItem(
        external_id=str(raw.get("id") or ""),
        name=name,
        description=description,
        category=str(category) if category is not None else None,
        price_brl=_as_float(raw.get("valorVenda")),
        quantity=_as_float(raw.get("quantidade")),
        availability=availability,
        availability_id=availability_id,
        updated_at=str(raw.get("dataModificacao")) if raw.get("dataModificacao") is not None else None,
        search_text=_normalize_text(" ".join(search_parts)),
        source="mercado_phone",
        condition=condition,
        capacity=capacity,
        colors=color,
        color=color,
        battery_health=battery_health,
    )


def score_item(query: str, item: InventoryItem) -> int:
    normalized_query = _normalize_text(query)
    if not normalized_query:
        return 0
    query_tokens = [token for token in normalized_query.split(" ") if token]
    item_search_text = _normalize_text(item.search_text)
    score = 0
    if normalized_query == item_search_text:
        score += 1000
    if normalized_query in item_search_text:
        score += 200
    for token in query_tokens:
        if token in item_search_text:
            score += 20
    digits = re.sub(r"\D", "", normalized_query)
    if digits and digits in re.sub(r"\D", "", item_search_text):
        score += 60
    return score


class InventoryCache:
    def __init__(
        self,
        client: MercadoPhoneClient,
        settings: Settings,
        cache_path: str | Path = "data/inventory_cache.json",
        sealed_cache: Any | None = None,
    ):
        self.client = client
        self.settings = settings
        self.cache_path = Path(cache_path)
        self.sealed_cache = sealed_cache
        self.items: list[InventoryItem] = []
        self.last_refresh: float = 0.0
        self._lock = asyncio.Lock()
        self._load()

    @property
    def ready(self) -> bool:
        return bool(self.items) and self.last_refresh > 0 and (
            time.time() - self.last_refresh < self.settings.mercado_cache_ttl_seconds
        )

    def _load(self) -> None:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.last_refresh = float(payload.get("last_refresh", 0.0))
            self.items = [InventoryItem.model_validate(item) for item in payload.get("items", [])]
            try:
                cache_version = int(payload.get("cache_version", 0))
            except (TypeError, ValueError):
                cache_version = 0
            if cache_version != INVENTORY_CACHE_VERSION:
                # The old snapshot did not contain parsed color/battery fields.
                # Refresh once before serving it to the customer.
                self.last_refresh = 0.0
        except (OSError, ValueError, TypeError):
            self.items = []
            self.last_refresh = 0.0

    async def refresh(self, force: bool = False) -> int:
        if not force and self.items and time.time() - self.last_refresh < self.settings.mercado_cache_ttl_seconds:
            return len(self.items)
        async with self._lock:
            if not force and self.items and time.time() - self.last_refresh < self.settings.mercado_cache_ttl_seconds:
                return len(self.items)
            items = await self.client.fetch_all_inventory()
            self.items = items
            self.last_refresh = time.time()
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "cache_version": INVENTORY_CACHE_VERSION,
                "last_refresh": self.last_refresh,
                "items": [item.model_dump() for item in items],
            }
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.cache_path.parent, prefix="inventory-", suffix=".tmp", delete=False
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False)
                temporary_path = Path(handle.name)
            temporary_path.replace(self.cache_path)
            return len(items)

    async def ensure_fresh(self) -> None:
        await self.refresh(force=False)

    async def search_inventory(self, query: str, limit: int = 5) -> list[InventoryItem]:
        await self.ensure_fresh()
        ranked = sorted(
            ((score_item(query, item), item) for item in self.items if item.external_id),
            key=lambda pair: (pair[0], pair[1].name),
            reverse=True,
        )
        return [item for score, item in ranked if score > 0][:limit]

    async def search_sealed(self, query: str, limit: int = 5) -> list[InventoryItem]:
        if self.sealed_cache is None:
            return []
        return await self.sealed_cache.search(query, limit=limit)

    async def _enrich_sealed_item(self, item: InventoryItem) -> InventoryItem:
        candidates = await self.search_inventory(
            f"{item.name} {item.capacity or ''}".strip(),
            limit=10,
        )
        target_tokens = [token for token in _normalize_text(f"{item.name} {item.capacity or ''}").split() if token]
        for candidate in candidates:
            candidate_text = _normalize_text(f"{candidate.name} {candidate.description}")
            if all(token in candidate_text for token in target_tokens):
                return item.model_copy(
                    update={
                        "quantity": candidate.quantity,
                        "availability": candidate.availability or "Disponibilidade a confirmar",
                    }
                )
        return item

    async def search(self, query: str, limit: int = 5) -> list[InventoryItem]:
        inventory_items = await self.search_inventory(query, limit=limit)
        sealed_items = await self.search_sealed(query, limit=limit)
        enriched_sealed = [await self._enrich_sealed_item(item) for item in sealed_items]
        ranked: list[tuple[int, InventoryItem]] = [
            (score_item(query, item), item) for item in inventory_items
        ]
        ranked.extend((score_item(query, item) + 80, item) for item in enriched_sealed)
        ranked.sort(key=lambda pair: (pair[0], pair[1].name, pair[1].capacity or ""), reverse=True)
        seen: set[str] = set()
        result: list[InventoryItem] = []
        for score, item in ranked:
            if score <= 0 or item.external_id in seen:
                continue
            seen.add(item.external_id)
            result.append(item)
            if len(result) >= limit:
                break
        return result

    async def get(self, product_id: str) -> InventoryItem | None:
        if str(product_id).startswith("sheet:") and self.sealed_cache is not None:
            item = await self.sealed_cache.get(product_id)
            return await self._enrich_sealed_item(item) if item else None
        await self.ensure_fresh()
        for item in self.items:
            if item.external_id == str(product_id):
                return item
        return None

    async def simulate_installment(self, query: str, installments: int) -> dict[str, Any]:
        if self.sealed_cache is None:
            return {"encontrado": False, "motivo": "Tabela de preços de lacrados não configurada"}
        if not 1 <= int(installments) <= 18:
            return {"encontrado": False, "motivo": "O parcelamento deve estar entre 1x e 18x"}
        candidates = await self.search_sealed(query, limit=5)
        if not candidates:
            return {"encontrado": False, "motivo": "Produto lacrado não localizado na tabela"}
        if len(candidates) > 1:
            first_score = score_item(query, candidates[0])
            second_score = score_item(query, candidates[1])
            if first_score == second_score:
                return {
                    "encontrado": False,
                    "ambiguo": True,
                    "candidatos": [
                        {"nome": item.name, "capacidade": item.capacity, "preco_brl": item.price_brl}
                        for item in candidates[:3]
                    ],
                }
        return self.sealed_cache.simulate(candidates[0], int(installments))
