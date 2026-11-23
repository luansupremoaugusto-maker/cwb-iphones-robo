from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from app.adapters.google_sheets import GoogleSheetsError
from app.adapters.google_sheets_safe import SafeGoogleSheetsCache
from app.schemas import InventoryItem


class GoogleSheetsPricesCache(SafeGoogleSheetsCache):
    """Read-only Google Sheets cache for sealed prices only.

    Installment rates are fixed in app.installments and are not read from Sheets.
    """

    _snapshot_mtime_ns: int | None = None

    def _reload_shared_snapshot(self) -> None:
        # The admin app and WhatsApp worker share the file, but have separate memory.
        try:
            mtime_ns = self.cache_path.stat().st_mtime_ns
            if mtime_ns == self._snapshot_mtime_ns:
                return
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            refreshed_at = float(payload["last_refresh"])
            if refreshed_at > self.last_refresh:
                products = [InventoryItem.model_validate(item) for item in payload["items"]]
                if products:
                    self.items = products
                    self.rates = {}
                    self.last_refresh = refreshed_at
                    self.last_error = None
            self._snapshot_mtime_ns = mtime_ns
        except (OSError, ValueError, TypeError, KeyError):
            # A broken or missing file must not replace an in-memory catalog.
            return

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
        self._reload_shared_snapshot()
        if not self.client.configured:
            raise GoogleSheetsError("GOOGLE_SERVICE_ACCOUNT_FILE/JSON não configurado")
        if (
            not force
            and self.last_refresh
            and time.time() - self.last_refresh < self.settings.google_sheets_cache_ttl_seconds
        ):
            return {"products": len(self.items), "rates": 0}

        async with self._lock:
            self._reload_shared_snapshot()
            if (
                not force
                and self.last_refresh
                and time.time() - self.last_refresh < self.settings.google_sheets_cache_ttl_seconds
            ):
                return {"products": len(self.items), "rates": 0}

            products = await self.client.fetch_catalog()
            if not products:
                raise GoogleSheetsError("Google Sheets retornou preços vazios")
            refreshed_at = time.time()
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "last_refresh": refreshed_at,
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
            self.items = products
            self.rates = {}
            self.last_refresh = refreshed_at
            self.last_error = None
            return {"products": len(products), "rates": 0}
