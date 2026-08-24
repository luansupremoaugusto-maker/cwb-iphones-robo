from __future__ import annotations

import json

import pytest

from app.adapters.google_sheets import (
    GoogleSheetsCache,
    parse_catalog_rows,
    parse_installment_rates,
)
from app.adapters.mercado_phone import InventoryCache
from app.config import Settings


BOT_VALUES = [
    [
        "Modelo",
        "Capacidade",
        "Cores",
        "Preço de Venda (R$)",
        "Preço de Venda em 18x por Parcela(R$)",
    ],
    ["iPhone 15", "128 GB", "Rosa | Preto", "R$ 4.000", "R$ 275,03"],
]

RATE_VALUES = [
    ["Número de vezes de parcelamento", "Taxa"],
    ["1x", "4,95%"],
    ["18x", "19,20%"],
]


def test_sheet_values_parse_brazilian_prices_and_rates():
    products = parse_catalog_rows(BOT_VALUES)
    rates = parse_installment_rates(RATE_VALUES)

    assert len(products) == 1
    assert products[0].source == "google_sheets"
    assert products[0].condition == "novo lacrado"
    assert products[0].price_brl == 4000.0
    assert products[0].installment_18_price_brl == 275.03
    assert rates == {1: 0.0495, 18: 0.192}


def test_source_row_has_a_carregador_alias_for_catalog_lookup():
    values = [
        BOT_VALUES[0],
        ["Fonte Tipo-C 20W original", "-", "-", "R$ 150", "R$ 10,31"],
    ]

    product = parse_catalog_rows(values)[0]

    assert "fonte" in product.search_text
    assert "carregador" in product.search_text


class FakeSheetsClient:
    configured = True

    async def fetch_catalog(self):
        return parse_catalog_rows(BOT_VALUES)

    async def fetch_installment_rates(self):
        return parse_installment_rates(RATE_VALUES)

    async def aclose(self):
        return None


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


@pytest.mark.asyncio
async def test_sheet_cache_simulates_installment_and_catalog_search(tmp_path):
    settings = Settings(
        google_sheets_enabled=True,
        google_sheets_cache_ttl_seconds=60,
        mercado_cache_ttl_seconds=60,
        faq_path=str(tmp_path / "faq.yaml"),
    )
    sheets = GoogleSheetsCache(FakeSheetsClient(), settings, cache_path=tmp_path / "sheets.json")
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sheets,
    )

    await sheets.refresh(force=True)
    products = await cache.search("iPhone 15 128 GB", limit=3)
    simulation = await cache.simulate_installment("iPhone 15 128 GB", 18)

    assert products[0].source == "google_sheets"
    assert products[0].name == "iPhone 15"
    assert simulation["encontrado"] is True
    assert simulation["valor_parcela_brl"] == 275.03
    assert "taxa_percentual" not in simulation


def test_disabled_sheet_cache_does_not_load_persisted_products(tmp_path):
    settings = Settings(google_sheets_enabled=False)
    cached_product = parse_catalog_rows(BOT_VALUES)[0].model_dump()
    cache_path = tmp_path / "sheets.json"
    cache_path.write_text(
        json.dumps({"last_refresh": 1, "items": [cached_product], "rates": {}}),
        encoding="utf-8",
    )

    sheets = GoogleSheetsCache(
        FakeSheetsClient(),
        settings,
        cache_path=cache_path,
        enabled=False,
    )

    assert sheets.items == []
    assert sheets.rates == {}
