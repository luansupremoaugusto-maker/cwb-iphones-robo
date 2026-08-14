from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.adapters.mercado_phone import normalize_inventory_item
from app.agent import AgentService, _extract_catalog_product_id
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


def test_catalog_message_extracts_stock_code_without_url():
    assert _extract_catalog_product_id("Produto de c\u00f3digo (estoque): 9445935") == "9445935"


@pytest.mark.asyncio
async def test_catalog_link_message_returns_only_the_referenced_stock_item(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    referenced = InventoryItem(
        external_id="9445935",
        name="IPHONE 15 PRO MAX",
        category="Celular",
        capacity="256GB",
        color="TIT\u00c2NIO NATURAL",
        colors="TIT\u00c2NIO NATURAL",
        condition="SEMINOVO",
        availability="Dispon\u00edvel para venda",
        quantity=1,
        price_brl=6490,
        battery_health=100,
        search_text="iphone 15 pro max 256 gb titanio natural celular seminovo",
    )
    unrelated = referenced.model_copy(
        update={
            "external_id": "iphone-12-blue",
            "name": "IPHONE 12",
            "capacity": "128GB",
            "color": "AZUL",
            "colors": "AZUL",
            "search_text": "iphone 12 128 gb azul celular seminovo",
        }
    )
    cache.items = [referenced, unrelated]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    message = (
        "Ol\u00e1, entro em contato para saber mais informa\u00e7\u00f5es sobre o produto "
        "IPHONE 15 PRO MAX 256gb TIT\u00c2NIO NATURAL de c\u00f3digo (estoque): 9445935 "
        "visto no cat\u00e1logo online https://app.mercadophone.tech/index.php?"
        "class=CatalogoProdutoView&tag=cwbiphones&produto_id=9445935&catalogo_produto_id=0"
    )

    assert _extract_catalog_product_id(message) == "9445935"
    decision = await agent.respond(message)

    assert decision.product_references == ["9445935"]
    assert "IPHONE 15 PRO MAX" in decision.reply
    assert "TIT\u00c2NIO NATURAL \u2014 256GB \u2014 SEMINOVO" in decision.reply
    assert "Lista completa" not in decision.reply
    assert "IPHONE 12" not in decision.reply


@pytest.mark.asyncio
async def test_short_photo_followup_keeps_the_last_confirmed_iphone_14_not_plus(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    iphone_14 = InventoryItem(
        external_id="14-blue-256",
        name="IPHONE 14",
        category="Celular",
        capacity="256GB",
        color="AZUL",
        colors="AZUL",
        condition="SEMINOVO",
        availability="Disponivel para venda",
        quantity=1,
        price_brl=2200,
        battery_health=91,
        search_text="iphone 14 azul 256 gb celular seminovo",
        photo_urls=["https://photos.example/iphone-14-blue-256.jpg"],
    )
    iphone_14_plus = iphone_14.model_copy(
        update={
            "external_id": "14-plus-blue-128",
            "name": "IPHONE 14 PLUS",
            "capacity": "128GB",
            "search_text": "iphone 14 plus azul 128 gb celular seminovo",
            "photo_urls": ["https://photos.example/iphone-14-plus-blue.jpg"],
        }
    )
    cache.items = [iphone_14, iphone_14_plus]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    history = [
        {"role": "user", "content": "Tem iPhone 14?"},
        {
            "role": "assistant",
            "content": "Para iPhone 14 de 128 GB, nao encontrei. Temos iPhone 14 seminovo de 256 GB.",
        },
        {"role": "user", "content": "O azul"},
        {
            "role": "assistant",
            "content": "O iPhone 14 azul, 256 GB, seminovo esta disponivel por R$ 2.200. A bateria esta com 91%.",
        },
    ]

    decision = await agent.respond("Tem fotos?", history=history)

    assert decision.image_urls == ["https://photos.example/iphone-14-blue-256.jpg"]
    assert decision.product_references == ["14-blue-256"]
    assert "IPHONE 14 PLUS" not in decision.reply


@pytest.mark.asyncio
async def test_photo_followup_prefers_customer_image_over_wrong_assistant_product(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    iphone_13_pro = InventoryItem(
        external_id="13-pro-silver-256",
        name="IPHONE 13 PRO",
        category="Celular",
        capacity="256GB",
        color="PRATEADO",
        colors="PRATEADO",
        condition="SEMINOVO",
        availability="Disponivel para venda",
        quantity=1,
        price_brl=2550,
        search_text="iphone 13 pro prateado 256 gb celular seminovo",
        photo_urls=["https://photos.example/iphone-13-pro-256.jpg"],
    )
    iphone_14_plus = iphone_13_pro.model_copy(
        update={
            "external_id": "14-plus-blue-128",
            "name": "IPHONE 14 PLUS",
            "capacity": "128GB",
            "color": "AZUL",
            "colors": "AZUL",
            "search_text": "iphone 14 plus azul 128 gb celular seminovo",
            "photo_urls": ["https://photos.example/iphone-14-plus-128.jpg"],
        }
    )
    cache.items = [iphone_13_pro, iphone_14_plus]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    history = [
        {
            "role": "user",
            "content": "Descricao visual da imagem recebida: anuncio de iPhone 13 Pro 256GB prateado.",
        },
        {
            "role": "assistant",
            "content": "Claro! Seguem as fotos do IPHONE 14 PLUS 128GB.",
        },
        {"role": "user", "content": "Esta disponivel"},
    ]

    decision = await agent.respond("Mande foto ou video", history=history)

    assert decision.image_urls == ["https://photos.example/iphone-13-pro-256.jpg"]
    assert decision.product_references == ["13-pro-silver-256"]
    assert "IPHONE 13 PRO" in decision.reply
    assert "14 PLUS" not in decision.reply


@pytest.mark.asyncio
async def test_photo_followup_keeps_customer_seminovo_condition_after_wrong_assistant_reply(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)

    class SealedWatchCatalog:
        def __init__(self):
            self.items = [
                InventoryItem(
                    external_id="sheet:watch-se3",
                    name="Apple Watch SE 3",
                    category="Novo lacrado",
                    capacity="40MM",
                    source="google_sheets",
                    condition="novo lacrado",
                    price_brl=2100,
                    search_text="apple watch se 3 40mm novo lacrado",
                )
            ]

        async def ensure_fresh(self):
            return None

        async def search(self, query: str, limit: int = 5):
            return self.items[:limit]

        async def get(self, product_id: str):
            return next((item for item in self.items if item.external_id == product_id), None)

    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=SealedWatchCatalog(),
    )
    cache.items = [
        InventoryItem(
            external_id="watch-se2-seminovo",
            name="Apple Watch SE 2",
            category="Celular",
            capacity="40MM",
            color="ESTELAR",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            search_text="apple watch se 2 40mm estelar celular seminovo",
            photo_urls=["https://photos.example/apple-watch-se2.jpg"],
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    history = [
        {"role": "user", "content": "Vi que voces postaram um Apple Watch SE 2 semi novo."},
        {
            "role": "assistant",
            "content": "Encontrei Apple Watch Series 11 e Apple Watch SE 3 - NOVO LACRADO - R$ 2.700,00.",
        },
    ]

    decision = await agent.respond("Pode me mandar foto por favor", history=history)

    assert decision.image_urls == ["https://photos.example/apple-watch-se2.jpg"]
    assert decision.product_references == ["watch-se2-seminovo"]


@pytest.mark.asyncio
async def test_photo_followup_after_iphone_14_plus_confirmation_uses_that_product(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)

    class SealedPriceCache:
        def __init__(self):
            self.items = [
                InventoryItem(
                    external_id="sheet:iphone-17",
                    name="iPhone 17",
                    category="Celular",
                    capacity="256 GB",
                    source="google_sheets",
                    condition="novo lacrado",
                    search_text="iphone 17 256 gb novo lacrado",
                )
            ]

        async def ensure_fresh(self):
            return None

        async def search(self, query: str, limit: int = 5):
            return self.items[:limit]

    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=SealedPriceCache(),
    )
    iphone_14_plus = InventoryItem(
        external_id="14-plus-blue-128",
        name="IPHONE 14 PLUS",
        category="Celular",
        capacity="128GB",
        color="AZUL",
        colors="AZUL",
        source="mercado_phone",
        condition="SEMINOVO",
        availability="Disponivel para venda",
        quantity=1,
        price_brl=2310,
        battery_health=97,
        search_text="iphone 14 plus azul 128gb celular seminovo",
        photo_urls=["https://photos.example/iphone-14-plus-blue-128.jpg"],
    )
    cache.items = [iphone_14_plus]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    history = [
        {"role": "user", "content": "Queria saber se voces ainda tem esse iPhone disponivel."},
        {
            "role": "assistant",
            "content": "Encontrei estas opcoes de iPhone: iPhone 17 - NOVO LACRADO - R$ 8.000,00.",
        },
        {"role": "user", "content": "O iPhone 14 plus azul"},
        {
            "role": "assistant",
            "content": (
                "Sim. O iPhone 14 Plus azul, 128 GB, esta disponivel por R$ 2.310,00. "
                "Bateria com 97% de saude. Acompanha cabo e fonte novos."
            ),
        },
    ]

    decision = await agent.respond("Tem a foto dele?", history=history)

    assert decision.image_urls == ["https://photos.example/iphone-14-plus-blue-128.jpg"]
    assert decision.product_references == ["14-plus-blue-128"]

@pytest.mark.asyncio
async def test_standalone_photo_followup_keeps_seminovo_context_after_entry_message(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)

    class SealedPriceCache:
        items = [
            InventoryItem(
                external_id="sheet:iphone-16e",
                name="IPHONE 16 E",
                category="Celular",
                capacity="128GB",
                source="google_sheets",
                condition="novo lacrado",
                search_text="iphone 16 e 128gb novo lacrado",
            )
        ]

        async def ensure_fresh(self):
            return None

        async def search(self, query: str, limit: int = 5):
            return self.items[:limit]

    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=SealedPriceCache(),
    )
    seminevo = InventoryItem(
        external_id="16e-black-seminovo",
        name="IPHONE 16 E",
        category="Celular",
        capacity="128GB",
        color="PRETO",
        colors="PRETO",
        source="mercado_phone",
        condition="SEMINOVO",
        availability="Disponivel para venda",
        quantity=1,
        price_brl=2660,
        battery_health=92,
        search_text="iphone 16 e preto 128 gb celular seminovo",
        photo_urls=["https://photos.example/iphone-16e-black.jpg"],
    )
    cache.items = [seminevo]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    history = [
        {"role": "user", "content": "Voce ainda ta com aquele iPhone 16e?"},
        {
            "role": "assistant",
            "content": (
                "Sim, temos um iPhone 16e seminovo disponivel: 128GB, preto, "
                "bateria com 92% de saude, por R$ 2.660. Quer mais detalhes ou fotos?"
            ),
        },
        {"role": "user", "content": "Quero sim por favor"},
        {
            "role": "assistant",
            "content": "Voce quer receber as fotos ou mais detalhes do iPhone 16e?",
        },
        {"role": "user", "content": "Eu te dando 1k de entrada e parcelando o restante vc nao faz um preco melhor nele?"},
    ]

    decision = await agent.respond("Fotos", history=history)

    assert decision.image_urls == ["https://photos.example/iphone-16e-black.jpg"]
    assert decision.product_references == ["16e-black-seminovo"]
    assert "lacrados" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_affirmative_photo_confirmation_does_not_send_evaluation_form(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    iphone_12 = InventoryItem(
        external_id="iphone-12-blue-128",
        name="IPHONE 12",
        category="Celular",
        capacity="128GB",
        color="AZUL",
        colors="AZUL",
        source="mercado_phone",
        condition="SEMINOVO",
        availability="Disponivel para venda",
        quantity=1,
        battery_health=80,
        search_text="iphone 12 azul 128gb celular seminovo",
        photo_urls=["https://photos.example/iphone-12-blue-128.jpg"],
    )
    cache.items = [iphone_12]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Sim",
        history=[
            {
                "role": "assistant",
                "content": (
                    "Temos apenas 1 iPhone 12 disponivel: azul, 128GB, seminovo, "
                    "bateria 80%, por R$ 1.210,00."
                ),
            },
            {"role": "user", "content": "Mais o azul tem muita marca de uso?"},
            {
                "role": "assistant",
                "content": (
                    "No cadastro nao consta o nivel de marcas de uso dele. "
                    "E um iPhone 12 azul, 128GB, seminovo. "
                    "Posso enviar as fotos cadastradas para voce avaliar melhor."
                ),
            },
        ],
    )
    assert decision.handoff is False
    assert decision.image_urls == ["https://photos.example/iphone-12-blue-128.jpg"]
    assert "lista de avaliacao" not in decision.reply.lower()
