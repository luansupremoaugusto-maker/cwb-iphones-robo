from __future__ import annotations

import time

from app.adapters.google_sheets import GoogleSheetsCache, GoogleSheetsError


class SafeGoogleSheetsCache(GoogleSheetsCache):
    """Google Sheets cache that never serves stale prices after a failed refresh."""

    async def ensure_fresh(self) -> None:
        if not self.enabled:
            return
        if not self.client.configured:
            self.last_error = "Credencial Google Sheets não configurada"
            if self.items and time.time() - self.last_refresh < self.settings.google_sheets_cache_ttl_seconds:
                return
            if self.items:
                raise GoogleSheetsError(self.last_error)
            return
        try:
            await self.refresh(force=False)
        except GoogleSheetsError as exc:
            self.last_error = str(exc)[:1000]
            if self.items and time.time() - self.last_refresh < self.settings.google_sheets_cache_ttl_seconds:
                return
            raise

    async def search(self, query: str, limit: int = 5):
        try:
            return await super().search(query, limit=limit)
        except GoogleSheetsError:
            return []

    async def get(self, product_id: str):
        try:
            return await super().get(product_id)
        except GoogleSheetsError:
            return None
