from __future__ import annotations

import asyncio
import json
import re
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings
from app.schemas import InventoryItem


SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
SHEETS_API_ROOT = "https://sheets.googleapis.com/v4/spreadsheets"


class GoogleSheetsError(RuntimeError):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


def normalize_sheet_text(value: Any) -> str:
    text = str(value or "")
    without_accents = "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", without_accents.replace("\n", " ")).strip().lower()


def parse_brazilian_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^0-9,.-]", "", str(value).strip())
    if not text or text in {"-", ".", ","}:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    elif "." in text and len(text.rsplit(".", 1)[1]) == 3:
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_rate(value: Any) -> float | None:
    parsed = parse_brazilian_number(value)
    if parsed is None:
        return None
    return parsed / 100 if parsed > 1 else parsed


def _find_column(headers: list[str], *aliases: str) -> int | None:
    normalized_headers = [normalize_sheet_text(header) for header in headers]
    normalized_aliases = [normalize_sheet_text(alias) for alias in aliases]
    for alias in normalized_aliases:
        for index, header in enumerate(normalized_headers):
            if header == alias:
                return index
    for alias in normalized_aliases:
        for index, header in enumerate(normalized_headers):
            if alias and alias in header:
                return index
    return None


def _cell(row: list[Any], index: int | None) -> Any:
    return row[index] if index is not None and index < len(row) else None


def parse_catalog_rows(values: list[list[Any]]) -> list[InventoryItem]:
    if not values:
        return []
    headers = [str(value or "") for value in values[0]]
    model_col = _find_column(headers, "Modelo", "Produto")
    capacity_col = _find_column(headers, "Capacidade", "Memória")
    colors_col = _find_column(headers, "Cores", "Cor")
    price_col = _find_column(headers, "Preço de Venda (R$)", "Preço de Venda", "Preço")
    price_18_col = _find_column(
        headers,
        "Preço de Venda em 18x por Parcela(R$)",
        "18x por Parcela",
        "18x",
    )
    if model_col is None or price_col is None:
        raise GoogleSheetsError("A aba de preços não possui as colunas Modelo e Preço de Venda")

    products: list[InventoryItem] = []
    for row_number, row in enumerate(values[1:], start=2):
        model = str(_cell(row, model_col) or "").strip()
        price = parse_brazilian_number(_cell(row, price_col))
        if not model or price is None:
            continue
        capacity = str(_cell(row, capacity_col) or "-").strip()
        colors = str(_cell(row, colors_col) or "-").replace("\n", " ").strip()
        description = f"{model} {capacity}".strip()
        normalized_model = normalize_sheet_text(model)
        accessory_aliases = ""
        if re.search(r"\b(?:fonte|carregador)\b", normalized_model):
            accessory_aliases = " fonte carregador"
            if "20w" in normalized_model:
                accessory_aliases += " usb c tipo c 20w"
        search_text = normalize_sheet_text(
            f"{model} {capacity} {colors} {accessory_aliases} novo lacrado lacrado {row_number}"
        )
        products.append(
            InventoryItem(
                external_id=f"sheet:bot:{row_number}",
                name=model,
                description=description,
                category="Novo lacrado",
                price_brl=price,
                quantity=None,
                availability="Preço de lacrado informado; disponibilidade deve ser confirmada",
                updated_at=None,
                search_text=search_text,
                source="google_sheets",
                condition="novo lacrado",
                capacity=capacity,
                colors=colors,
                installment_18_price_brl=parse_brazilian_number(_cell(row, price_18_col)),
            )
        )
    return products


def parse_installment_rates(values: list[list[Any]]) -> dict[int, float]:
    if not values:
        return {}
    headers = [str(value or "") for value in values[0]]
    count_col = _find_column(
        headers,
        "Número de vezes de parcelamento",
        "Número de vezes",
        "Parcelamento",
        "Vezes",
    )
    rate_col = _find_column(headers, "Taxa", "Taxa (%)", "Percentual")
    if count_col is None or rate_col is None:
        raise GoogleSheetsError("A aba de taxas não possui as colunas de vezes e taxa")

    rates: dict[int, float] = {}
    for row in values[1:]:
        count_match = re.search(r"\d+", str(_cell(row, count_col) or ""))
        rate = parse_rate(_cell(row, rate_col))
        if not count_match or rate is None:
            continue
        count = int(count_match.group(0))
        if 1 <= count <= 18:
            rates[count] = rate
    return rates


class GoogleSheetsClient:
    """Read-only Google Sheets API client using a service account."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
        self._owns_client = client is None
        self._credentials: Any | None = None

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.google_sheets_enabled
            and self.settings.google_sheets_spreadsheet_id
            and (
                self.settings.google_service_account_file
                or self.settings.google_service_account_json
            )
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _load_credentials(self) -> Any:
        try:
            from google.oauth2 import service_account
        except ImportError as exc:
            raise GoogleSheetsError(
                "A dependência google-auth não está instalada para ler a planilha"
            ) from exc

        try:
            if self.settings.google_service_account_file:
                return service_account.Credentials.from_service_account_file(
                    self.settings.google_service_account_file,
                    scopes=[SHEETS_SCOPE],
                )
            info = json.loads(self.settings.google_service_account_json or "")
            return service_account.Credentials.from_service_account_info(info, scopes=[SHEETS_SCOPE])
        except (OSError, ValueError, TypeError) as exc:
            raise GoogleSheetsError("Credencial da conta de serviço Google inválida") from exc

    async def _access_token(self) -> str:
        if not self.configured:
            raise GoogleSheetsError("Credencial Google Sheets não configurada")
        if self._credentials is None:
            self._credentials = self._load_credentials()

        def refresh_if_needed() -> str:
            try:
                from google.auth.transport.requests import Request
            except ImportError as exc:
                raise GoogleSheetsError(
                    "A dependência google-auth não está instalada para ler a planilha"
                ) from exc
            if not self._credentials.valid or self._credentials.expired:
                self._credentials.refresh(Request())
            if not self._credentials.token:
                raise GoogleSheetsError("A conta de serviço Google não retornou token")
            return str(self._credentials.token)

        return await asyncio.to_thread(refresh_if_needed)

    async def _read_range(self, sheet_name: str, cell_range: str) -> list[list[Any]]:
        token = await self._access_token()
        quoted_range = f"'{sheet_name.replace(chr(39), chr(39) * 2)}'!{cell_range}"
        encoded_range = quote(quoted_range, safe="!:$'")
        url = f"{SHEETS_API_ROOT}/{self.settings.google_sheets_spreadsheet_id}/values/{encoded_range}"
        response = await self._client.get(
            url,
            params={"valueRenderOption": "FORMATTED_VALUE", "majorDimension": "ROWS"},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:500]
            raise GoogleSheetsError(
                f"Google Sheets HTTP {response.status_code}: {detail}", response.status_code
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise GoogleSheetsError("Google Sheets retornou JSON inválido", response.status_code) from exc
        values = payload.get("values", []) if isinstance(payload, dict) else []
        return values if isinstance(values, list) else []

    async def fetch_catalog(self) -> list[InventoryItem]:
        values = await self._read_range(
            self.settings.google_sheets_prices_tab,
            self.settings.google_sheets_prices_range,
        )
        return parse_catalog_rows(values)

    async def fetch_installment_rates(self) -> dict[int, float]:
        values = await self._read_range(
            self.settings.google_sheets_rates_tab,
            self.settings.google_sheets_rates_range,
        )
        return parse_installment_rates(values)


class GoogleSheetsCache:
    def __init__(
        self,
        client: GoogleSheetsClient,
        settings: Settings,
        cache_path: str | Path = "data/google_sheets_cache.json",
        enabled: bool = True,
    ):
        self.client = client
        self.settings = settings
        self.cache_path = Path(cache_path)
        self.enabled = enabled
        self.items: list[InventoryItem] = []
        self.rates: dict[int, float] = {}
        self.last_refresh: float = 0.0
        self.last_error: str | None = None
        self._lock = asyncio.Lock()
        # A disabled integration must not expose a persisted snapshot. This
        # keeps offline/sandbox runs isolated and avoids serving prices after
        # the operator intentionally disabled the source.
        if self.enabled:
            self._load()

    @property
    def configured(self) -> bool:
        return not self.enabled or self.client.configured

    @property
    def ready(self) -> bool:
        return not self.enabled or (
            self.client.configured
            and bool(self.items)
            and bool(self.rates)
            and self.last_refresh > 0
            and time.time() - self.last_refresh < self.settings.google_sheets_cache_ttl_seconds
        )

    def _load(self) -> None:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.last_refresh = float(payload.get("last_refresh", 0.0))
            self.items = [InventoryItem.model_validate(item) for item in payload.get("items", [])]
            self.rates = {
                int(key): float(value) for key, value in (payload.get("rates", {}) or {}).items()
            }
        except (OSError, ValueError, TypeError):
            self.items = []
            self.rates = {}
            self.last_refresh = 0.0

    async def aclose(self) -> None:
        await self.client.aclose()

    async def refresh(self, force: bool = False) -> dict[str, int]:
        if not self.enabled:
            return {"products": len(self.items), "rates": len(self.rates)}
        if not self.client.configured:
            raise GoogleSheetsError("GOOGLE_SERVICE_ACCOUNT_FILE/JSON não configurado")
        if (
            not force
            and self.last_refresh
            and time.time() - self.last_refresh < self.settings.google_sheets_cache_ttl_seconds
        ):
            return {"products": len(self.items), "rates": len(self.rates)}
        async with self._lock:
            if (
                not force
                and self.last_refresh
                and time.time() - self.last_refresh < self.settings.google_sheets_cache_ttl_seconds
            ):
                return {"products": len(self.items), "rates": len(self.rates)}
            products, rates = await asyncio.gather(
                self.client.fetch_catalog(), self.client.fetch_installment_rates()
            )
            if not products or not rates:
                raise GoogleSheetsError("Google Sheets retornou preços ou taxas vazios")
            self.items = products
            self.rates = rates
            self.last_refresh = time.time()
            self.last_error = None
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "last_refresh": self.last_refresh,
                "items": [item.model_dump() for item in products],
                "rates": {str(key): value for key, value in rates.items()},
            }
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.cache_path.parent, prefix="google-sheets-", suffix=".tmp", delete=False
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False)
                temporary_path = Path(handle.name)
            temporary_path.replace(self.cache_path)
            return {"products": len(products), "rates": len(rates)}

    async def ensure_fresh(self) -> None:
        if not self.enabled or not self.client.configured:
            return
        try:
            await self.refresh(force=False)
        except GoogleSheetsError as exc:
            self.last_error = str(exc)[:1000]

    @staticmethod
    def _score(query: str, item: InventoryItem) -> int:
        normalized_query = normalize_sheet_text(query)
        if not normalized_query:
            return 0
        score = 0
        if normalized_query == normalize_sheet_text(f"{item.name} {item.capacity or ''}").strip():
            score += 1000
        if normalized_query in item.search_text:
            score += 200
        for token in normalized_query.split():
            if token in item.search_text:
                score += 20
        return score

    async def search(self, query: str, limit: int = 5) -> list[InventoryItem]:
        await self.ensure_fresh()
        ranked = sorted(
            ((self._score(query, item), item) for item in self.items),
            key=lambda pair: (pair[0], pair[1].name, pair[1].capacity or ""),
            reverse=True,
        )
        return [item for score, item in ranked if score > 0][:limit]

    async def get(self, product_id: str) -> InventoryItem | None:
        await self.ensure_fresh()
        for item in self.items:
            if item.external_id == str(product_id):
                return item
        return None

    def simulate(self, item: InventoryItem, installments: int) -> dict[str, Any]:
        rate = self.rates.get(int(installments))
        if rate is None or item.price_brl is None:
            return {"encontrado": False, "motivo": "Não há taxa aprovada para essa quantidade de parcelas"}
        total = item.price_brl / (1 - rate)
        installment = total / installments
        return {
            "encontrado": True,
            "nome": item.name,
            "capacidade": item.capacity,
            "preco_avista_brl": round(item.price_brl, 2),
            "vezes": int(installments),
            "taxa_percentual": round(rate * 100, 2),
            "valor_total_brl": round(total, 2),
            "valor_parcela_brl": round(installment, 2),
        }
