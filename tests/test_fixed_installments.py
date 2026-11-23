from __future__ import annotations

import pytest

from app.adapters.google_sheets_prices import GoogleSheetsPricesCache
from app.config import Settings
from app.installments import FIXED_INSTALLMENT_RATES, simulate_installment
from app.schemas import InventoryItem


def test_fixed_installment_rates_match_approved_table():
    assert len(FIXED_INSTALLMENT_RATES) == 18
    assert FIXED_INSTALLMENT_RATES[1] == 0.0495
    assert FIXED_INSTALLMENT_RATES[12] == 0.1405
    assert FIXED_INSTALLMENT_RATES[18] == 0.192

    item = InventoryItem(external_id="test-iphone-12", name="iPhone 12", price_brl=1600.0)
    result = simulate_installment(item, 18)

    assert result["encontrado"] is True
    assert "taxa_percentual" not in result
    assert result["valor_parcela_brl"] == 110.01


class PricesOnlyClient:
    configured = True
    model = "iPhone 15"

    async def fetch_catalog(self):
        return [
            InventoryItem(
                external_id="sheet:2",
                name=self.model,
                search_text=self.model,
                price_brl=4000.0,
            )
        ]

    async def fetch_installment_rates(self):
        raise AssertionError("A tabela de taxas não deve mais ser lida")

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_google_prices_cache_does_not_read_installment_tab(tmp_path):
    settings = Settings(
        google_sheets_enabled=True,
        google_sheets_cache_ttl_seconds=3600,
        faq_path=str(tmp_path / "faq.yaml"),
    )
    cache = GoogleSheetsPricesCache(
        PricesOnlyClient(),
        settings,
        cache_path=tmp_path / "prices.json",
    )

    result = await cache.refresh(force=True)

    assert result == {"products": 1, "rates": 0}
    assert cache.items[0].name == "iPhone 15"


@pytest.mark.asyncio
async def test_manual_price_refresh_replaces_worker_snapshot_before_ttl(tmp_path):
    settings = Settings(google_sheets_enabled=True, google_sheets_cache_ttl_seconds=3600)
    cache_path = tmp_path / "prices.json"
    admin_client = PricesOnlyClient()
    worker_client = PricesOnlyClient()
    admin_client.model = worker_client.model = "Apple Watch Series 11"
    admin = GoogleSheetsPricesCache(admin_client, settings, cache_path=cache_path)
    worker = GoogleSheetsPricesCache(worker_client, settings, cache_path=cache_path)
    await worker.refresh(force=True)

    admin_client.model = "Apple Watch Series 12"
    await admin.refresh(force=True)

    item = await worker.get("sheet:2")
    assert item.name == "Apple Watch Series 12"
    assert worker.last_refresh == admin.last_refresh
