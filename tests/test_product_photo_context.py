from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


class SealedCatalog:
    def __init__(self):
        self.items = [
            InventoryItem(
                external_id="sheet:iphone-17-pro-max",
                name="iPhone 17 Pro Max",
                capacity="256 GB",
                price_brl=8000.0,
                source="google_sheets",
                condition="novo lacrado",
                search_text="iphone 17 pro max 256 gb novo lacrado",
            )
        ]

    async def search(self, query: str, limit: int = 5):
        return self.items[:limit]

    async def get(self, product_id: str):
        return next((item for item in self.items if item.external_id == product_id), None)


def _used_15_pro_max(external_id: str, capacity: str, price: float, photo: str) -> InventoryItem:
    return InventoryItem(
        external_id=external_id,
        name="IPHONE 15 PRO MAX",
        category="Celular",
        capacity=capacity,
        color="TITÂNIO NATURAL",
        condition="SEMINOVO",
        availability="Disponível para venda",
        quantity=1,
        price_brl=price,
        search_text=f"iphone 15 pro max titanio natural {capacity} celular seminovo",
        photo_urls=[photo],
    )


def _build_agent(tmp_path) -> AgentService:
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=SealedCatalog(),
    )
    cache.items = [
        _used_15_pro_max(
            "9445935",
            "256GB",
            4130.0,
            "https://photos.example/15-pro-max-256.jpg",
        ),
        _used_15_pro_max(
            "9902333",
            "512GB",
            4230.0,
            "https://photos.example/15-pro-max-512.jpg",
        ),
    ]
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


def _build_1tb_agent(tmp_path) -> AgentService:
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-1tb.json",
        sealed_cache=SealedCatalog(),
    )
    cache.items = [
        _used_15_pro_max(
            "used-256",
            "256GB",
            4130.0,
            "https://photos.example/15-pro-max-256.jpg",
        ),
        _used_15_pro_max(
            "used-1tb",
            "1TB",
            4290.0,
            "https://photos.example/15-pro-max-1tb.jpg",
        ),
    ]
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


def _model_history() -> list[dict[str, str]]:
    return [
        {"role": "user", "content": "To vendo se vou pro 15 pro max"},
        {
            "role": "assistant",
            "content": (
                "Tranquilo. Se decidir pelo iPhone 15 Pro Max, me avise a capacidade "
                "e eu consulto as opcoes e valores disponiveis."
            ),
        },
    ]


@pytest.mark.asyncio
async def test_ambiguous_used_photo_request_does_not_fall_back_to_unrelated_sealed_catalog(tmp_path):
    agent = _build_agent(tmp_path)

    decision = await agent.respond("Pode me mandar fotos", history=_model_history())

    reply = decision.reply.lower()
    assert decision.handoff is False
    assert decision.image_urls == []
    assert "seminovo" in reply
    assert "256gb" in reply
    assert "512gb" in reply
    assert "fotos cadastradas" in reply
    assert "qual capacidade" in reply
    assert "novo lacrado" not in reply


@pytest.mark.asyncio
async def test_used_and_insistence_followups_keep_the_previous_photo_context(tmp_path):
    agent = _build_agent(tmp_path)
    history = [
        *_model_history(),
        {"role": "user", "content": "Pode me mandar fotos"},
        {
            "role": "assistant",
            "content": (
                "Os aparelhos novos lacrados sao vendidos por encomenda, entao nao temos "
                "fotos do produto cadastradas no sistema."
            ),
        },
        {"role": "user", "content": "Usado"},
    ]

    decision = await agent.respond("Eu vi que voce tem", history=history)

    reply = decision.reply.lower()
    assert decision.handoff is False
    assert decision.image_urls == []
    assert "seminovo" in reply
    assert "256gb" in reply
    assert "512gb" in reply
    assert "fotos cadastradas" in reply
    assert "nao encontrei fotos" not in reply
    assert "novo lacrado" not in reply


@pytest.mark.asyncio
async def test_capacity_followup_sends_the_selected_used_phone_photos(tmp_path):
    agent = _build_agent(tmp_path)
    history = [
        *_model_history(),
        {"role": "user", "content": "Pode me mandar fotos"},
        {
            "role": "assistant",
            "content": (
                "Encontrei o iPhone 15 Pro Max seminovo em 256 GB e 512 GB, "
                "ambos com fotos cadastradas. Qual capacidade você quer?"
            ),
        },
    ]

    decision = await agent.respond("256 GB", history=history)

    assert decision.handoff is False
    assert decision.image_urls == ["https://photos.example/15-pro-max-256.jpg"]
    assert decision.product_references == ["9445935"]
    assert "15 pro max" in decision.reply.lower()


@pytest.mark.asyncio
async def test_batched_1tb_selection_and_payment_question_sends_1tb_photos(tmp_path):
    agent = _build_1tb_agent(tmp_path)
    history = [
        {
            "role": "user",
            "content": "Pode me mandar fotos desse iPhone 15 Pro Max seminovo",
        },
        {
            "role": "assistant",
            "content": (
                "Encontrei o iPhone 15 Pro Max seminovo em 256GB e 1TB, "
                "ambos com fotos cadastradas. Qual capacidade você quer?"
            ),
        },
    ]

    decision = await agent.respond(
        "1TB\nVoces parcela?\nComo é a forma de pagamento? E quanto tempo chegaria pra mim?",
        history=history,
    )

    assert decision.handoff is False
    assert decision.image_urls == ["https://photos.example/15-pro-max-1tb.jpg"]
    assert decision.product_references == ["used-1tb"]
    assert "1tb" in decision.reply.lower()
    assert "256gb" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_photo_request_for_unavailable_model_continues_with_stock_alternatives(tmp_path):
    agent = _build_agent(tmp_path)

    decision = await agent.respond(
        "Tem foto do iPhone 13",
        history=[
            {
                "role": "assistant",
                "content": "Seminovos disponíveis para venda: IPHONE 12, IPHONE XR.",
            },
        ],
    )

    reply = decision.reply.lower()
    assert decision.handoff is False
    assert decision.image_urls == []
    assert "não localizei o iphone 13 disponível no estoque" in reply
    assert "15 pro max" in reply
    assert "atendente" not in reply


@pytest.mark.asyncio
async def test_photo_followup_after_three_product_cards_sends_all_requested_photos(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-three-product-cards.json",
    )
    photo_urls = {
        "iphone-14-pro-max-128-roxo": "https://photos.example/iphone-14-pro-max-128-roxo.jpg",
        "iphone-14-pro-max-256-preto": "https://photos.example/iphone-14-pro-max-256-preto.jpg",
        "iphone-15-plus-128-preto": "https://photos.example/iphone-15-plus-128-preto.jpg",
    }
    cache.items = [
        InventoryItem(
            external_id="iphone-14-pro-max-128-roxo",
            name="iPhone 14 Pro Max",
            category="Celular",
            capacity="128GB",
            color="ROXO PROFUNDO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3140,
            battery_health=81,
            source="mercado_phone",
            search_text="iphone 14 pro max roxo profundo 128gb celular seminovo",
            photo_urls=[photo_urls["iphone-14-pro-max-128-roxo"]],
        ),
        InventoryItem(
            external_id="iphone-14-pro-max-256-preto",
            name="iPhone 14 Pro Max",
            category="Celular",
            capacity="256GB",
            color="PRETO ESPACIAL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3590,
            battery_health=86,
            source="mercado_phone",
            search_text="iphone 14 pro max preto espacial 256gb celular seminovo",
            photo_urls=[photo_urls["iphone-14-pro-max-256-preto"]],
        ),
        InventoryItem(
            external_id="iphone-15-plus-128-preto",
            name="iPhone 15 Plus",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2950,
            battery_health=87,
            source="mercado_phone",
            search_text="iphone 15 plus preto 128gb celular seminovo",
            photo_urls=[photo_urls["iphone-15-plus-128-preto"]],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [
        {
            "role": "user",
            "content": "iPhone 14 Pro Max 128GB — Roxo profundo — R$ 3.140 | Bateria 81%",
        },
        {
            "role": "user",
            "content": "iPhone 14 Pro Max 256GB — Preto espacial — R$ 3.590 | Bateria 86%",
        },
        {
            "role": "user",
            "content": "iPhone 15 Plus 128GB — Preto — R$ 2.950 | Bateria 87%",
        },
        {
            "role": "assistant",
            "content": (
                "Sim 😊 Encontrei estas opções de iPhone disponíveis:\n"
                "• iPhone 14 Pro Max — ROXO PROFUNDO — 128GB — SEMINOVO — R$ 3.140,00 | Bat: 81%\n"
                "• iPhone 14 Pro Max — PRETO ESPACIAL — 256GB — SEMINOVO — R$ 3.590,00 | Bat: 86%\n"
                "• iPhone 15 Plus — PRETO — 128GB — SEMINOVO — R$ 2.950,00 | Bat: 87%"
            ),
        },
    ]

    decision = await agent.respond("Você consegue me mandar foto desses 3 aparelhos", history=history)

    assert decision.handoff is False
    assert set(decision.image_urls) == set(photo_urls.values())
    assert set(decision.product_references) == set(photo_urls)
    assert "14 pro max" in decision.reply.lower()
    assert "15 plus" in decision.reply.lower()


@pytest.mark.asyncio
async def test_typo_photo_request_for_listed_16_pro_max_sends_all_same_capacity_units(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-16-pro-max.json",
        sealed_cache=SealedCatalog(),
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-pro-max-deserto-256",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO DESERTO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=5500.0,
            battery_health=100,
            search_text="iphone 16 pro max titanio deserto 256gb celular seminovo",
            photo_urls=["https://photos.example/16-pro-max-deserto-256.jpg"],
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-preto-256",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=5280.0,
            battery_health=89,
            search_text="iphone 16 pro max titanio preto 256gb celular seminovo",
            photo_urls=["https://photos.example/16-pro-max-preto-256.jpg"],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Quero doto do 16 pro Max",
        history=[
            {
                "role": "assistant",
                "content": (
                    "IPHONE 16 PRO MAX\n"
                    "• - TITÂNIO DESERTO - 256GB - SEMINOVO — R$ 5.500,00 | Bat: 100%\n"
                    "• - TITÂNIO PRETO - 256GB - SEMINOVO — R$ 5.280,00 | Bat: 89%"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert decision.image_urls == [
        "https://photos.example/16-pro-max-deserto-256.jpg",
        "https://photos.example/16-pro-max-preto-256.jpg",
    ]
    assert decision.product_references == [
        "iphone-16-pro-max-deserto-256",
        "iphone-16-pro-max-preto-256",
    ]
    assert "não encontrei fotos" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_color_photo_followup_uses_the_current_blue_iphone_14_unit(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-iphone-14-color.json",
        sealed_cache=SealedCatalog(),
    )
    blue_url = "https://photos.example/iphone-14-azul-128.jpg"
    purple_url = "https://photos.example/iphone-14-roxo-128.jpg"
    cache.items = [
        InventoryItem(
            external_id="10302981",
            name="IPHONE 14",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            colors="AZUL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1980.0,
            battery_health=82,
            search_text="iphone 14 azul 128 gb celular seminovo",
            photo_urls=[blue_url],
        ),
        InventoryItem(
            external_id="10310968",
            name="IPHONE 14",
            category="Celular",
            capacity="128GB",
            color="ROXO",
            colors="ROXO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1980.0,
            battery_health=84,
            search_text="iphone 14 roxo 128 gb celular seminovo",
            photo_urls=[purple_url],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Consegue me mandar uma foto desse azul?",
        history=[
            {"role": "user", "content": "iPhone 14 ainda está disponível?"},
            {
                "role": "assistant",
                "content": (
                    "Sim 😊 Encontrei estas opções de IPHONE 14 disponíveis:\n"
                    "• IPHONE 14 — AZUL — 128GB — SEMINOVO — R$ 1.980,00 | Bat: 82%\n"
                    "• IPHONE 14 — ROXO — 128GB — SEMINOVO — R$ 1.980,00 | Bat: 84%"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert decision.image_urls == [blue_url]
    assert decision.product_references == ["10302981"]
    assert purple_url not in decision.image_urls
    assert "não há fotos cadastradas" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_promotion_photo_request_returns_all_available_iphone_photos(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-promotion-photos.json",
        sealed_cache=SealedCatalog(),
    )
    green_url = "https://photos.example/iphone-11-pro-max-verde-128.jpg"
    blue_url = "https://photos.example/iphone-12-azul-64.jpg"
    cache.items = [
        InventoryItem(
            external_id="iphone-11-pro-max-verde-128",
            name="IPHONE 11 PRO MAX",
            category="Celular",
            capacity="128GB",
            color="VERDE",
            colors="VERDE",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2150.0,
            search_text="iphone 11 pro max verde 128gb celular seminovo",
            photo_urls=[green_url],
        ),
        InventoryItem(
            external_id="iphone-12-azul-64",
            name="IPHONE 12",
            category="Celular",
            capacity="64GB",
            color="AZUL",
            colors="AZUL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1800.0,
            search_text="iphone 12 azul 64gb celular seminovo",
            photo_urls=[blue_url],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Manda foto de iPhone na promoção pra mim fazendo um favor")

    assert decision.handoff is False
    assert set(decision.image_urls) == {green_url, blue_url}
    assert set(decision.product_references) == {
        "iphone-11-pro-max-verde-128",
        "iphone-12-azul-64",
    }
    assert "17 PRO MAX" not in decision.reply.upper()
    assert "NOVO LACRADO" not in decision.reply.upper()


@pytest.mark.asyncio
async def test_photo_followup_with_bare_11_and_green_color_finds_11_pro_max(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-iphone-11-pro-max-green.json",
        sealed_cache=SealedCatalog(),
    )
    green_url = "https://photos.example/iphone-11-pro-max-verde-128.jpg"
    black_url = "https://photos.example/iphone-11-pro-max-preto-128.jpg"
    cache.items = [
        InventoryItem(
            external_id="iphone-11-pro-max-verde-128",
            name="IPHONE 11 PRO MAX",
            category="Celular",
            capacity="128GB",
            color="VERDE",
            colors="VERDE",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2150.0,
            search_text="iphone 11 pro max verde 128gb celular seminovo",
            photo_urls=[green_url],
        ),
        InventoryItem(
            external_id="iphone-11-pro-max-preto-128",
            name="IPHONE 11 PRO MAX",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            colors="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2150.0,
            search_text="iphone 11 pro max preto 128gb celular seminovo",
            photo_urls=[black_url],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Eu vi ali que tem um 11 verde",
        history=[
            {
                "role": "user",
                "content": "Manda foto de iPhone na promoção pra mim fazendo um favor",
            },
            {
                "role": "assistant",
                "content": (
                    "Encontrei o iPhone 17 Pro Max novo lacrado em mais de uma opção; "
                    "qual capacidade você quer que eu envie nas fotos?"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert decision.image_urls == [green_url]
    assert decision.product_references == ["iphone-11-pro-max-verde-128"]
    assert "11 pro max" in decision.reply.lower()
    assert black_url not in decision.image_urls
