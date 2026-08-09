from __future__ import annotations

from app.adapters.catalog_cache import _catalog_score
from app.schemas import InventoryItem


def test_conjunction_does_not_select_iphone_17_e():
    query = "iPhone 17 256 GB novo lacrado nas cores branco e preto quanto fica parcelado"
    base = InventoryItem(external_id="base", name="iPhone 17", search_text="iphone 17 256 gb")
    edition_e = InventoryItem(external_id="e", name="iPhone 17 E", search_text="iphone 17 e 256 gb")

    assert _catalog_score(query, base) > _catalog_score(query, edition_e)


def test_compact_17e_alias_selects_the_e_edition():
    query = "iPhone 17e"
    base = InventoryItem(external_id="base", name="iPhone 17", search_text="iphone 17")
    edition_e = InventoryItem(external_id="e", name="iPhone 17 E", search_text="iphone 17 e")

    assert _catalog_score(query, edition_e) > _catalog_score(query, base)


def test_pro_max_phrase_does_not_select_plain_pro():
    query = "iPhone 17 Pro Max"
    pro = InventoryItem(external_id="pro", name="iPhone 17 Pro", search_text="iphone 17 pro")
    pro_max = InventoryItem(
        external_id="pro-max",
        name="iPhone 17 Pro Max",
        search_text="iphone 17 pro max",
    )

    assert _catalog_score(query, pro_max) > _catalog_score(query, pro)
