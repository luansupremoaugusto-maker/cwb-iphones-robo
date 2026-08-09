from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.adapters.mercado_phone import normalize_inventory_item
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


def _iphone_16e_raw(
    *,
    color: str,
    battery: int,
    availability: str = "Disponível para venda",
    condition: str = "SEMINOVO",
) -> dict:
    return {
        "id": f"16e-{color.lower()}",
        "aparelhoDescricao": "IPHONE 16 E",
        "descricao": (
            f"16e-{color.lower()} - IPHONE 16 E - {color} - 128gb - "
            f"Estado: {condition} - Saúde bateria: {battery} - IMEI: 123456789012345"
        ),
        "quantidade": 1,
        "valorVenda": 2660,
        "tipoProdutoDescricao": "Celular",
        "produtoDisponibilidadeId": 1,
        "produtoDisponibilidadeDescricao": availability,
    }


def _iphone_14_raw(
    *,
    capacity: str,
    color: str,
    battery: int,
    model: str = "IPHONE 14",
    availability: str = "Disponível para venda",
) -> dict:
    return {
        "id": f"14-{model.lower().replace(' ', '-')}-{capacity}-{color.lower()}",
        "aparelhoDescricao": model,
        "descricao": (
            f"{model} - {color} - {capacity} - Estado: SEMINOVO - "
            f"Saúde bateria: {battery}"
        ),
        "quantidade": 1,
        "valorVenda": 2200 if capacity == "256gb" else 2260,
        "tipoProdutoDescricao": "Celular",
        "produtoDisponibilidadeId": 1,
        "produtoDisponibilidadeDescricao": availability,
    }


def test_inventory_normalization_extracts_iphone_16e_details():
    item = normalize_inventory_item(_iphone_16e_raw(color="PRETO", battery=88))

    assert item.name == "IPHONE 16 E"
    assert item.capacity == "128GB"
    assert item.color == "PRETO"
    assert item.battery_health == 88
    assert item.condition == "SEMINOVO"
    assert item.availability == "Disponível para venda"
    assert item.availability_id == "1"


@pytest.mark.asyncio
async def test_iphone_16e_alias_search_and_sale_status_filter(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    available = normalize_inventory_item(_iphone_16e_raw(color="PRETO", battery=88))
    laboratory = normalize_inventory_item(
        _iphone_16e_raw(color="AZUL", battery=90, availability="Laboratório")
    )
    cache.items = [available, laboratory]
    cache.last_refresh = time.time()

    matches = await cache.search("iPhone 16e", limit=5)

    assert [item.external_id for item in matches] == [available.external_id]
    assert await cache.get(laboratory.external_id) is None


@pytest.mark.asyncio
async def test_available_list_is_individual_grouped_and_includes_product_state(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        normalize_inventory_item(
            _iphone_16e_raw(
                color="PRETO",
                battery=88,
                condition="SEMINOVO",
            )
        ),
        normalize_inventory_item(
            _iphone_16e_raw(
                color="BRANCO",
                battery=100,
                condition="SEMINOVO COM GARANTIA APPLE",
            )
        ),
        normalize_inventory_item(_iphone_16e_raw(color="ROSA", battery=75, availability="Laboratório")),
    ]
    cache.last_refresh = time.time()

    result = await cache.list_available_products()
    entries = result["seminovos"]

    assert len(entries) == 2
    assert {(entry["cor"], entry["saude_bateria"]) for entry in entries} == {
        ("PRETO", 88.0),
        ("BRANCO", 100.0),
    }

    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    decision = await agent.respond("O que tem disponível?")

    assert "IPHONE 16 E" in decision.reply
    assert "BRANCO - 128GB - SEMINOVO COM GARANTIA APPLE — R$ 2.660,00 | Bat: 100%" in decision.reply
    assert "PRETO - 128GB - SEMINOVO — R$ 2.660,00 | Bat: 88%" in decision.reply
    assert "ROSA" not in decision.reply
    assert "laboratório" not in decision.reply.lower()
    assert "valores de" not in decision.reply


@pytest.mark.asyncio
async def test_photo_request_does_not_use_previous_complete_list_to_choose_model(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    iphone_16e = InventoryItem(
        external_id="16e-white",
        name="IPHONE 16 E",
        category="Celular",
        capacity="128GB",
        color="BRANCO",
        colors="BRANCO",
        condition="SEMINOVO COM GARANTIA APPLE",
        availability="Disponível para venda",
        quantity=1,
        search_text="iphone 16 e branco 128 gb celular seminovo com garantia apple",
        photo_urls=["https://photos.example/iphone-16e-white.jpg"],
    )
    iphone_16_pro = InventoryItem(
        external_id="16-pro",
        name="IPHONE 16 PRO",
        category="Celular",
        capacity="128GB",
        color="TITÂNIO PRETO",
        colors="TITÂNIO PRETO",
        condition="SEMINOVO",
        availability="Disponível para venda",
        quantity=1,
        search_text="iphone 16 pro titanio preto 128 gb celular seminovo",
        photo_urls=["https://photos.example/iphone-16-pro.jpg"],
    )
    cache.items = [iphone_16e, iphone_16_pro]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    history = [
        {"role": "user", "content": "O que tem disponível?"},
        {
            "role": "assistant",
            "content": "Lista completa: IPHONE 16 E, IPHONE 16 PRO, IPHONE 16 PRO MAX e vários outros modelos.",
        },
    ]
    decision = await agent.respond("Consegue me mandar foto do 16e branco?", history=history)

    assert decision.image_urls == ["https://photos.example/iphone-16e-white.jpg"]
    assert decision.product_references == ["16e-white"]
    assert "IPHONE 16 E" in decision.reply
    assert "16 PRO" not in decision.reply


@pytest.mark.asyncio
async def test_photo_request_never_falls_back_to_another_variant_with_photos(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    target = InventoryItem(
        external_id="16e-white",
        name="IPHONE 16 E",
        category="Celular",
        capacity="128GB",
        color="BRANCO",
        colors="BRANCO",
        condition="SEMINOVO",
        availability="Disponível para venda",
        quantity=1,
        search_text="iphone 16 e branco 128 gb celular seminovo",
    )
    other_variant = InventoryItem(
        external_id="16-pro",
        name="IPHONE 16 PRO",
        category="Celular",
        capacity="128GB",
        color="PRETO",
        colors="PRETO",
        condition="SEMINOVO",
        availability="Disponível para venda",
        quantity=1,
        search_text="iphone 16 pro preto 128 gb celular seminovo",
        photo_urls=["https://photos.example/iphone-16-pro.jpg"],
    )
    cache.items = [target, other_variant]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Pode mandar a foto do 16e branco?")

    assert decision.image_urls == []
    assert "IPHONE 16 E" in decision.reply
    assert "16 PRO" not in decision.reply
    assert "não há fotos cadastradas" in decision.reply


@pytest.mark.asyncio
async def test_short_photo_request_uses_model_and_color(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    black = InventoryItem(
        external_id="16e-black",
        name="IPHONE 16 E",
        category="Celular",
        capacity="128GB",
        color="PRETO",
        colors="PRETO",
        condition="SEMINOVO",
        availability="Disponível para venda",
        quantity=1,
        search_text="iphone 16 e preto 128 gb celular seminovo",
        photo_urls=["https://photos.example/iphone-16e-black.jpg"],
    )
    white = black.model_copy(
        update={
            "external_id": "16e-white",
            "color": "BRANCO",
            "colors": "BRANCO",
            "search_text": "iphone 16 e branco 128 gb celular seminovo",
            "photo_urls": ["https://photos.example/iphone-16e-white.jpg"],
        }
    )
    base = black.model_copy(
        update={
            "external_id": "16-base",
            "name": "IPHONE 16",
            "color": "ULTRAMARINO",
            "colors": "ULTRAMARINO",
            "search_text": "iphone 16 ultramarino 128 gb celular seminovo",
            "photo_urls": ["https://photos.example/iphone-16.jpg"],
        }
    )
    cache.items = [white, base, black]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("E fotos do 16e preto?")

    assert decision.image_urls == ["https://photos.example/iphone-16e-black.jpg"]
    assert decision.product_references == ["16e-black"]
    assert "IPHONE 16 E" in decision.reply
    assert "16" in decision.reply


@pytest.mark.asyncio
async def test_specific_availability_prefers_exact_base_model_and_capacity(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    base = normalize_inventory_item(
        _iphone_14_raw(capacity="256gb", color="AZUL", battery=91)
    )
    base_128 = normalize_inventory_item(
        _iphone_14_raw(capacity="128gb", color="AMARELO", battery=86)
    )
    pro = normalize_inventory_item(
        _iphone_14_raw(
            capacity="256gb",
            color="PRETO",
            battery=90,
            model="IPHONE 14 PRO",
        )
    )
    cache.items = [base, base_128, pro]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Tem iPhone 14 256GB?")

    assert decision.product_references == [base.external_id]
    assert "IPHONE 14" in decision.reply
    assert "AZUL — 256GB — SEMINOVO" in decision.reply
    assert "AMARELO" not in decision.reply
    assert "14 PRO" not in decision.reply


@pytest.mark.asyncio
async def test_specific_availability_without_capacity_lists_exact_model_rows(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    base_256 = normalize_inventory_item(
        _iphone_14_raw(capacity="256gb", color="AZUL", battery=91)
    )
    base_128 = normalize_inventory_item(
        _iphone_14_raw(capacity="128gb", color="AMARELO", battery=86)
    )
    pro = normalize_inventory_item(
        _iphone_14_raw(
            capacity="256gb",
            color="PRETO",
            battery=90,
            model="IPHONE 14 PRO",
        )
    )
    cache.items = [base_256, base_128, pro]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Tem iPhone 14?")

    assert "AZUL — 256GB — SEMINOVO" in decision.reply
    assert "AMARELO — 128GB — SEMINOVO" in decision.reply
    assert "14 PRO" not in decision.reply
