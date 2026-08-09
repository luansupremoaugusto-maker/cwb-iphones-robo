from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from app.adapters.google_sheets import GoogleSheetsError
from app.adapters.google_sheets_safe import SafeGoogleSheetsCache


class GoogleSheetsPricesCache(SafeGoogleSheetsCache):
    """Read-only Google Sheets cache for sealed prices only.

    Installment rates are fixed in app.installments and are not read from Sheets.
    """

    @property
    def ready(self) -> bool:
        return not self.enabled or (
            self.client.configured
            and bool(self.items)
            and self.last_refresh > 0
            and time.time() - self.last_refresh < self.settings.google_sheets_cache_ttl_seconds
        )

    async def refresh(self, force: bool = False) -> dict[str, int]:
        if not self.enabled:
            return {"products": len(self.items), "rates": 0}
        if not self.client.configured:
            raise GoogleSheetsError("GOOGLE_SERVICE_ACCOUNT_FILE/JSON não configurado")
        if (
            not force
            and self.last_refresh
            and time.time() - self.last_refresh < self.settings.google_sheets_cache_ttl_seconds
        ):
            return {"products": len(self.items), "rates": 0}

        async with self._lock:
            if (
                not force
                and self.last_refresh
                and time.time() - self.last_refresh < self.settings.google_sheets_cache_ttl_seconds
            ):
                return {"products": len(self.items), "rates": 0}

            products = await self.client.fetch_catalog()
            if not products:
                raise GoogleSheetsError("Google Sheets retornou preços vazios")
            self.items = products
            self.rates = {}
            self.last_refresh = time.time()
            self.last_error = None
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "last_refresh": self.last_refresh,
                "items": [item.model_dump() for item in products],
                "rates": {},
            }
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.cache_path.parent,
                prefix="google-sheets-prices-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False)
                temporary_path = Path(handle.name)
            temporary_path.replace(self.cache_path)
            return {"products": len(products), "rates": 0}
