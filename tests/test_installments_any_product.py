from __future__ import annotations

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.adapters.google_sheets import GoogleSheetsCache, parse_installment_rates
from app.config import Settings
from app.schemas import InventoryItem


RATE_VALUES = [
    ["Número de vezes de parcelamento", "Taxa"],
    ["18x", "19,20%"],
]


class FakeSheetsClient:
    configured = True

    async def fetch_catalog(self):
        return [
            InventoryItem(
                external_id="sheet:bot:2",
                name="iPhone 15",
                description="iPhone 15 128 GB",
                category="Novo lacrado",
                condition="novo lacrado",
                capacity="128 GB",
                price_brl=4000.0,
                search_text="iphone 15 128 gb novo lacrado",
                source="google_sheets",
            )
        ]

    async def fetch_installment_rates(self):
        return parse_installment_rates(RATE_VALUES)

    async def aclose(self):
        return None


class FakeMercadoClient:
    async def fetch_all_inventory(self):
        return [
            InventoryItem(
                external_id="mercado:12",
                name="iPhone 12",
                description="iPhone 12 128 GB seminovo",
                category="Celular Apple",
                condition="seminovo",
                capacity="128 GB",
                price_brl=1600.0,
                quantity=1,
                availability="Disponível",
                search_text="iphone 12 128 gb seminovo celular apple",
                source="mercado_phone",
            )
        ]


@pytest.mark.asyncio
async def test_installment_rates_apply_to_seminovo_mercado_phone_product(tmp_path):
    settings = Settings(
        google_sheets_enabled=True,
        google_sheets_cache_ttl_seconds=60,
        mercado_cache_ttl_seconds=60,
        faq_path=str(tmp_path / "faq.yaml"),
    )
    sheets = GoogleSheetsCache(FakeSheetsClient(), settings, cache_path=tmp_path / "sheets.json")
    cache = StoreCatalogCache(
        FakeMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sheets,
    )

    await sheets.refresh(force=True)
    simulation = await cache.simulate_installment("iPhone 12 128 GB", 18)

    assert simulation["encontrado"] is True
    assert simulation["condicao"] == "seminovo"
    assert simulation["fonte_preco"] == "catálogo Mercado Phone"
    assert simulation["preco_avista_brl"] == 1600.0
    assert "taxa_percentual" not in simulation
    assert simulation["valor_parcela_brl"] == 110.01
