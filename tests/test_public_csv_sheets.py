from __future__ import annotations

import httpx
import pytest

from app.adapters.google_sheets_public_csv import PublicCsvSheetsClient
from app.config import Settings


class FakeHttpClient:
    def __init__(self, payload: str):
        self.payload = payload
        self.requested_url = None

    async def get(self, url: str):
        self.requested_url = url
        return httpx.Response(200, content=self.payload.encode("utf-8"))


@pytest.mark.asyncio
async def test_public_csv_client_reads_only_catalog_prices():
    settings = Settings(
        google_sheets_enabled=True,
        google_sheets_public_csv_url="https://example.test/bot.csv",
    )
    client = FakeHttpClient(
        "Modelo,Capacidade,Cores,Preço de Venda (R$),Preço de Venda em 18x por Parcela(R$)\n"
        "iPhone 15,128 GB,Rosa,R$ 4.000,\"R$ 275,03\"\n"
    )
    sheets = PublicCsvSheetsClient(settings, client=client)

    products = await sheets.fetch_catalog()

    assert client.requested_url == "https://example.test/bot.csv"
    assert products[0].name == "iPhone 15"
    assert products[0].price_brl == 4000.0
    assert products[0].installment_18_price_brl == 275.03
    assert products[0].source == "google_sheets"
