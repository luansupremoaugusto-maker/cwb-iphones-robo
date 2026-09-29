from __future__ import annotations

from app.adapters.catalog_cache import StoreCatalogCache
from app.admin import public_catalog_payload
from app.agent import _format_available_products, _format_product_availability
from app.schemas import InventoryItem


def phone(name: str, *, item_id: str | None = None) -> InventoryItem:
    return InventoryItem(
        external_id=item_id or name,
        name=name,
        category="Celular",
        condition="seminovo",
        capacity="128 GB",
        price_brl=3000.0,
        quantity=1,
        availability="Disponível",
        color="Preto",
        search_text=f"{name} 128 gb celular seminovo",
        source="mercado_phone",
    )


def test_customer_availability_reply_places_e_iphone_models_before_base_models():
    items = [
        phone("iPhone 16"),
        phone("iPhone 17"),
        phone("iPhone 16 E"),
        phone("iPhone 17 E"),
    ]

    reply = _format_product_availability(items)
    listed_models = [
        line.split(" — ", 1)[0][2:]
        for line in reply.splitlines()
        if line.startswith("• iPhone ")
    ]

    assert listed_models == ["iPhone 16 E", "iPhone 16", "iPhone 17 E", "iPhone 17"]


def test_admin_catalog_and_full_list_place_e_models_before_base_models():
    items = [
        phone("iPhone 16"),
        phone("iPhone 17"),
        phone("iPhone 16 E"),
        phone("iPhone 17 E"),
    ]

    entries = StoreCatalogCache._individualize(items, sealed=False)
    admin_payload = public_catalog_payload(
        {"seminovos": entries}, None, None, "2026-09-29T12:00:00Z"
    )
    expected = ["iPhone 16 E", "iPhone 16", "iPhone 17 E", "iPhone 17"]
    assert [entry["nome"] for entry in admin_payload["seminovos"]] == expected

    reply = _format_available_products(admin_payload)
    listed_models = [line for line in reply.splitlines() if line in expected]
    assert listed_models == expected
