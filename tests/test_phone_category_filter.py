from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache, _is_device_item
from app.config import Settings
from app.schemas import InventoryItem


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


class MixedSealedCache:
    def __init__(self):
        self.items = [
            InventoryItem(
                external_id="sheet:iphone-16",
                name="iPhone 16",
                category="Novo lacrado",
                source="google_sheets",
                condition="novo lacrado",
                price_brl=4600,
                search_text="iphone 16 novo lacrado",
            ),
            InventoryItem(
                external_id="sheet:ipad-11",
                name="iPad 11",
                category="Novo lacrado",
                source="google_sheets",
                condition="novo lacrado",
                price_brl=3000,
                search_text="ipad 11 novo lacrado",
            ),
            InventoryItem(
                external_id="sheet:macbook",
                name="MacBook Air",
                category="Novo lacrado",
                source="google_sheets",
                condition="novo lacrado",
                price_brl=8300,
                search_text="macbook air novo lacrado",
            ),
            InventoryItem(
                external_id="sheet:fonte",
                name="Fonte Tipo-C 20W original",
                category="Novo lacrado",
                source="google_sheets",
                condition="novo lacrado",
                price_brl=150,
                search_text="fonte tipo c novo lacrado",
            ),
        ]

    async def ensure_fresh(self):
        return None

    async def search(self, query: str, limit: int = 5):
        normalized = query.lower()
        return [item for item in self.items if normalized in item.search_text.lower()][:limit]

    async def get(self, product_id: str):
        return next((item for item in self.items if item.external_id == product_id), None)


def make_item(item_id: str, name: str, category: str, *, source: str = "mercado_phone") -> InventoryItem:
    return InventoryItem(
        external_id=item_id,
        name=name,
        category=category,
        source=source,
        condition="seminovo" if source == "mercado_phone" else "novo lacrado",
        quantity=1 if source == "mercado_phone" else None,
        availability="Disponível" if source == "mercado_phone" else "Preço confirmado",
        price_brl=1000,
        search_text=f"{name} {category}".lower(),
    )


def build_cache(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=MixedSealedCache(),
    )
    cache.items = [
        make_item("iphone", "iPhone 15", "Celular"),
        # These are intentionally classified as Celular in the store system.
        make_item("airpods", "AirPods Pro 3", "Celular"),
        make_item("watch", "Apple Watch SE", "Celular"),
        # These must remain outside the Mercado Phone customer catalog.
        make_item("case", "Capa iPhone 15", "Acessórios"),
        make_item("test", "iPhone de teste", "TESTE"),
    ]
    cache.last_refresh = time.time()
    return cache


def test_cellular_category_is_the_source_of_truth_for_mercado_items():
    assert _is_device_item(make_item("airpods", "AirPods Pro 3", "Celular")) is True
    assert _is_device_item(make_item("watch", "Apple Watch SE", "Celular")) is True
    assert _is_device_item(make_item("case", "Capa iPhone 15", "Acessórios")) is False
    assert _is_device_item(make_item("test", "iPhone de teste", "TESTE")) is False


@pytest.mark.asyncio
async def test_list_contains_cellular_category_items_and_removes_other_categories(tmp_path):
    cache = build_cache(tmp_path)

    result = await cache.list_available_products()
    names = {
        entry["nome"]
        for section in (result["seminovos"], result["lacrados"])
        for entry in section
    }

    assert {"iPhone 15", "AirPods Pro 3", "Apple Watch SE", "iPad 11", "MacBook Air"} <= names
    assert "Capa iPhone 15" not in names
    assert "iPhone de teste" not in names
    assert "Fonte Tipo-C 20W original" not in names


@pytest.mark.asyncio
async def test_search_and_direct_availability_never_return_non_cellular_categories(tmp_path):
    cache = build_cache(tmp_path)

    assert await cache.search("Capa iPhone", limit=5) == []
    assert [item.name for item in await cache.search("AirPods", limit=5)] == ["AirPods Pro 3"]
    assert await cache.get("case") is None
    assert (await cache.get("airpods")).name == "AirPods Pro 3"
