from __future__ import annotations

import csv
import io

import httpx

from app.adapters.google_sheets import GoogleSheetsError, parse_catalog_rows
from app.config import Settings


class PublicCsvSheetsClient:
    """Read-only client for a Google Sheets tab published as CSV.

    This mode avoids service-account keys. The published URL must expose only
    the price tab that the bot is allowed to read.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=True,
        )
        self._owns_client = client is None

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.google_sheets_enabled
            and self.settings.google_sheets_public_csv_url
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_catalog(self):
        if not self.configured:
            raise GoogleSheetsError("URL CSV pública do Google Sheets não configurada")

        response = await self._client.get(self.settings.google_sheets_public_csv_url)
        if response.status_code >= 400:
            raise GoogleSheetsError(
                f"Google Sheets CSV HTTP {response.status_code}: {response.text[:500]}",
                response.status_code,
            )

        try:
            text = response.content.decode("utf-8-sig")
            values = list(csv.reader(io.StringIO(text)))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise GoogleSheetsError("Google Sheets CSV inválido") from exc

        products = parse_catalog_rows(values)
        if not products:
            raise GoogleSheetsError("Google Sheets CSV não retornou preços válidos")
        return products

    async def fetch_installment_rates(self):
        raise GoogleSheetsError("As taxas de parcelamento são fixas no robô")
