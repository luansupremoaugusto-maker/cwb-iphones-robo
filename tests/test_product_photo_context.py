from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.agent import AgentService, PRODUCT_PHOTO_HANDOFF_REASON
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem
from app.trade_in import is_photo_offer_confirmation


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


def test_photo_offer_confirmation_matches_customer_facing_offer_wording():
    for offer in (
        "Quer ver as fotos de algum deles?",
        "Quer que eu envie as fotos?",
    ):
        assert is_photo_offer_confirmation(
            "Simm",
            [{"role": "assistant", "content": offer}],
        )

    assert not is_photo_offer_confirmation(
        "Simm",
        [
            {
                "role": "assistant",
                "content": (
                    "Não encontrei fotos cadastradas desse iPhone 14. "
                    "Se quiser, posso encaminhar seu pedido para um atendente."
                ),
            }
        ],
    )


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


def _build_agent_with_items(tmp_path, items: list[InventoryItem]) -> AgentService:
    agent = _build_agent(tmp_path)
    agent.cache.items = items
    return agent


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
async def test_capacity_availability_followup_does_not_send_previous_variant_photos(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-14-pro-max-capacity.json",
        sealed_cache=SealedCatalog(),
    )
    photo_128 = "https://photos.example/iphone-14-pro-max-128.jpg"
    photo_256 = "https://photos.example/iphone-14-pro-max-256.jpg"
    cache.items = [
        InventoryItem(
            external_id="iphone-14-pro-max-128",
            name="IPHONE 14 PRO MAX",
            category="Celular",
            capacity="128GB",
            color="PRETO ESPACIAL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3450.0,
            battery_health=85,
            source="mercado_phone",
            search_text="iphone 14 pro max preto espacial 128gb celular seminovo",
            photo_urls=[photo_128],
        ),
        InventoryItem(
            external_id="iphone-14-pro-max-256",
            name="IPHONE 14 PRO MAX",
            category="Celular",
            capacity="256GB",
            color="PRATA",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3590.0,
            battery_health=86,
            source="mercado_phone",
            search_text="iphone 14 pro max prata 256gb celular seminovo",
            photo_urls=[photo_256],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Tem esse msm modelo com 256gb?",
        history=[
            {
                "role": "user",
                "content": "Descricao visual da imagem recebida: anuncio de iPhone 14 Pro Max 128GB preto espacial.",
            },
            {
                "role": "assistant",
                "content": (
                    "Sim 😊 O iPhone 14 Pro Max 128GB Preto Espacial está disponível "
                    "para venda por R$ 3.450,00. A bateria está com 85%."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert decision.image_urls == []
    assert decision.product_references == ["iphone-14-pro-max-256"]
    assert "256gb" in decision.reply.lower()
    assert "128gb" not in decision.reply.lower()
    assert "3.590" in decision.reply


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
async def test_pronoun_photo_followup_keeps_historical_case_context(tmp_path):
    agent = _build_agent(tmp_path)
    history = [
        {"role": "user", "content": "Quero o iPhone 15 Pro Max seminovo"},
        {
            "role": "assistant",
            "content": (
                "O iPhone 15 Pro Max 512GB seminovo está disponível por R$ 4.230,00."
            ),
        },
        {"role": "user", "content": "E a capinha para ele, quanto custa?"},
        {"role": "assistant", "content": "A capinha sai por R$ 10,00."},
    ]

    decision = await agent.respond("Manda foto dela", history=history)

    assert decision.image_urls == []
    assert decision.product_references == []


@pytest.mark.asyncio
async def test_image_described_device_photo_followup_uses_phone_context(tmp_path):
    agent = _build_agent(tmp_path)
    history = [
        {"role": "user", "content": "Quero o iPhone 15 Pro Max seminovo"},
        {
            "role": "assistant",
            "content": (
                "O iPhone 15 Pro Max 512GB seminovo está disponível por R$ 4.230,00."
            ),
        },
        {"role": "user", "content": "E a capinha para ele, quanto custa?"},
        {"role": "assistant", "content": "A capinha sai por R$ 10,00."},
    ]

    decision = await agent.respond(
        "Manda foto dele",
        history=history,
        image_description="Descrição visual da imagem recebida: um celular preto visto de frente.",
    )

    assert decision.image_urls == ["https://photos.example/15-pro-max-512.jpg"]
    assert decision.product_references == ["9902333"]


@pytest.mark.asyncio
async def test_image_described_case_does_not_send_previous_iphone_photos(tmp_path):
    agent = _build_agent(tmp_path)
    history = [
        {"role": "user", "content": "Quero o iPhone 15 Pro Max seminovo"},
        {
            "role": "assistant",
            "content": (
                "O iPhone 15 Pro Max 512GB seminovo está disponível por R$ 4.230,00."
            ),
        },
        {"role": "user", "content": "E a capinha para ele, quanto custa?"},
        {"role": "assistant", "content": "A capinha sai por R$ 10,00."},
    ]

    decision = await agent.respond(
        "Manda foto dela",
        history=history,
        image_description=(
            "Descrição visual da imagem recebida: uma capinha para iPhone 15 Pro Max."
        ),
    )

    assert decision.image_urls == []


@pytest.mark.asyncio
async def test_generic_cellphone_photo_followup_sends_previous_used_iphone_photos(tmp_path):
    agent = _build_agent(tmp_path)
    photo_url = "https://photos.example/16-pro-max-512.jpg"
    target_item = InventoryItem(
        external_id="iphone-16-pro-max-512-seminovo",
        name="IPHONE 16 PRO MAX",
        category="Celular",
        capacity="512GB",
        color="PRETO",
        condition="SEMINOVO",
        availability="Disponível para venda",
        quantity=1,
        price_brl=5510.0,
        battery_health=90,
        source="mercado_phone",
        search_text="iphone 16 pro max preto 512gb celular seminovo",
        photo_urls=[photo_url],
    )
    agent.cache.items = [
        target_item,
        target_item.model_copy(
            update={
                "external_id": "iphone-16-pro-max-256-seminovo",
                "capacity": "256GB",
                "color": "AZUL",
                "search_text": "iphone 16 pro max azul 256gb celular seminovo",
                "photo_urls": ["https://photos.example/16-pro-max-256.jpg"],
            }
        ),
        target_item.model_copy(
            update={
                "external_id": "iphone-15-pro-max-512-seminovo",
                "name": "IPHONE 15 PRO MAX",
                "search_text": "iphone 15 pro max preto 512gb celular seminovo",
                "photo_urls": ["https://photos.example/15-pro-max-512.jpg"],
            }
        ),
        target_item.model_copy(
            update={
                "external_id": "iphone-16-pro-max-512-lacrado",
                "condition": "LACRADO",
                "search_text": "iphone 16 pro max preto 512gb celular lacrado",
                "photo_urls": ["https://photos.example/16-pro-max-512-sealed.jpg"],
            }
        ),
    ]
    history = [
        {"role": "user", "content": "Quero o iPhone 16 Pro Max 512GB seminovo"},
        {
            "role": "assistant",
            "content": (
                "Parcelamento do IPHONE 16 PRO MAX 512GB\n"
                "Preço à vista: R$ 5.510,00\n"
                "1x de R$ 5.796,95 (total R$ 5.796,95)"
            ),
        },
        {"role": "user", "content": "Já vem com capinha e película"},
        {"role": "user", "content": "?"},
        {
            "role": "assistant",
            "content": (
                "O seminovo acompanha cabo e fonte novos, homologados pela Anatel. "
                "Capinha e película são vendidas separadamente por R$ 10,00 cada."
            ),
        },
    ]

    decision = await agent.respond("Consegue mandar foto do celular", history=history)

    assert decision.handoff is False
    assert decision.image_urls == [photo_url]
    assert decision.product_references == ["iphone-16-pro-max-512-seminovo"]
    assert "não localizei esse produto seminovo" not in decision.reply.lower()

    accessory_photo = await agent.respond("Consegue mandar foto da capinha", history=history)

    assert accessory_photo.image_urls == []


@pytest.mark.asyncio
async def test_photo_request_selects_exact_row_from_customer_catalog_list(tmp_path):
    def item(external_id, name, capacity, color, price):
        return InventoryItem(
            external_id=external_id,
            name=name,
            category="Celular",
            capacity=capacity,
            color=color,
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=price,
            battery_health=95,
            source="mercado_phone",
            search_text=f"{name} {color} {capacity} celular seminovo",
            photo_urls=[f"https://photos.example/{external_id}.jpg"],
        )

    items = [
        item("pro-256-black", "IPHONE 16 PRO", "256GB", "TITÂNIO PRETO", 3470),
        item("pro-1tb-natural", "IPHONE 16 PRO", "1TB", "TITÂNIO NATURAL", 3790),
        item("iphone-16-black", "IPHONE 16", "128GB", "PRETO", 3630),
        item("iphone-16-rose", "IPHONE 16", "128GB", "ROSA", 2670),
        item("plus-rose", "IPHONE 16 PLUS", "128GB", "ROSA", 3750),
    ]
    agent = _build_agent_with_items(tmp_path, items)
    catalog = (
        "IPHONE 15\n"
        "• - AZUL - 128GB - SEMINOVO — R$ 2.640,00 | Bat: 88%\n"
        "• - PRETO - 256GB - SEMINOVO — R$ 2.820,00 | Bat: 95%\n"
        "IPHONE 15 PLUS\n"
        "• - ROSA - 128GB - SEMINOVO — R$ 2.670,00 | Bat: 95%\n"
        "• - PRETO - 256GB - SEMINOVO — R$ 2.830,00 | Bat: 95%\n"
        "IPHONE 15 PRO\n"
        "• - TITÂNIO PRETO - 128GB - SEMINOVO — R$ 3.470,00 | Bat: 97%\n"
        "• - TITÂNIO PRETO - 256GB - SEMINOVO — R$ 3.410,00 | Bat: 81%\n"
        "• - TITÂNIO NATURAL - 1TB - SEMINOVO — R$ 3.790,00 | Bat: 91%\n"
        "IPHONE 16E\n"
        "• - PRETO - 128GB - SEMINOVO — R$ 2.610,00 | Bat: 100%\n"
        "IPHONE 16\n"
        "• - PRETO - 128GB - SEMINOVO — R$ 3.630,00 | Bat: 88%\n"
        "• - PRETO - 256GB - SEMINOVO — R$ 3.850,00 | Bat: 88%\n"
        "IPHONE 16 PLUS\n"
        "• - ROSA - 128GB - SEMINOVO — R$ 3.750,00 | Bat: 92%"
    )
    selected = item("iphone-15-plus-rose", "IPHONE 15 PLUS", "128GB", "ROSA", 2670)
    agent = _build_agent_with_items(tmp_path, [selected, *items])
    history = [
        {
            "role": "assistant",
            "content": catalog,
        },
        {
            "role": "user",
            "content": "ROSA - 128GB - SEMINOVO — R$ 2.670,00 | Bat: 95%",
        },
    ]

    decision = await agent.respond("consegue me enviar foto desse?", history=history)

    assert decision.handoff is False
    assert decision.image_urls == ["https://photos.example/iphone-15-plus-rose.jpg"]
    assert decision.product_references == ["iphone-15-plus-rose"]


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
@pytest.mark.parametrize("last_has_photo", [True, False])
async def test_yes_to_iphone_14_photo_offer_resolves_the_last_selected_unit(
    tmp_path,
    monkeypatch,
    last_has_photo,
):
    async def no_product_files(*_args, **_kwargs):
        return {"data": []}

    monkeypatch.setattr(
        "app.adapters.catalog_cache.list_product_files", no_product_files
    )
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "iphone-14-last-selected-photos.json",
    )
    photo_urls = {
        "iphone-14-83": "https://photos.example/iphone-14-83.jpg",
        "iphone-14-91-1890": "https://photos.example/iphone-14-91-1890.jpg",
        "iphone-14-91-1900": "https://photos.example/iphone-14-91-1900.jpg",
    }
    cache.items = [
        InventoryItem(
            external_id=external_id,
            name="iPhone 14",
            category="Celular",
            capacity="128 GB",
            color="Meia-Noite",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=price,
            battery_health=battery,
            source="mercado_phone",
            search_text="iPhone 14 Meia-Noite 128 GB celular seminovo",
            photo_urls=(
                [photo_urls[external_id]]
                if last_has_photo or external_id != "iphone-14-91-1900"
                else []
            ),
        )
        for external_id, price, battery in (
            ("iphone-14-83", 1760, 83),
            ("iphone-14-91-1890", 1890, 91),
            ("iphone-14-91-1900", 1900, 91),
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [
        {"role": "user", "content": "Fique interessado no iPhone 14"},
        {
            "role": "assistant",
            "content": (
                "Temos 3 iPhone 14 seminovos de 128 GB, todos na cor Meia-Noite: "
                "um por R$ 1.760 com bateria em 83%, outro por R$ 1.890 com 91% "
                "e outro por R$ 1.900 também com 91%. Quer ver as fotos de algum deles?"
            ),
        },
        {"role": "user", "content": "Queria saber mais sobre o último"},
        {
            "role": "assistant",
            "content": (
                "O último é um iPhone 14 seminovo, 128 GB, cor Meia-Noite, "
                "com saúde da bateria em 91%, por R$ 1.900. Está disponível "
                "para venda; o estado de conservação não consta no cadastro. "
                "Quer que eu envie as fotos?"
            ),
        },
    ]

    decision = await agent.respond("Simm", history=history)

    if last_has_photo:
        assert decision.handoff is False
        assert decision.image_urls == [photo_urls["iphone-14-91-1900"]], decision
        assert decision.product_references == ["iphone-14-91-1900"], decision
    else:
        assert decision.handoff is True, decision
        assert decision.handoff_reason == PRODUCT_PHOTO_HANDOFF_REASON, decision
        assert decision.image_urls == [], decision
        assert "não há fotos cadastradas" in decision.reply.lower(), decision
        assert photo_urls["iphone-14-91-1890"] not in decision.image_urls


@pytest.mark.asyncio
async def test_current_battery_selection_overrides_previous_photo_price(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "iphone-14-current-battery-photos.json",
    )
    photo_urls = {
        "iphone-14-83": "https://photos.example/iphone-14-83.jpg",
        "iphone-14-91-1890": "https://photos.example/iphone-14-91-1890.jpg",
        "iphone-14-91-1900": "https://photos.example/iphone-14-91-1900.jpg",
    }
    cache.items = [
        InventoryItem(
            external_id=external_id,
            name="iPhone 14",
            category="Celular",
            capacity="128 GB",
            color="Meia-Noite",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=price,
            battery_health=battery,
            source="mercado_phone",
            search_text="iPhone 14 Meia-Noite 128 GB celular seminovo",
            photo_urls=[photo_urls[external_id]],
        )
        for external_id, price, battery in (
            ("iphone-14-83", 1760, 83),
            ("iphone-14-91-1890", 1890, 91),
            ("iphone-14-91-1900", 1900, 91),
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [
        {"role": "user", "content": "Queria saber mais sobre o último iPhone 14"},
        {
            "role": "assistant",
            "content": (
                "O último é um iPhone 14 seminovo, 128 GB, cor Meia-Noite, "
                "com saúde da bateria em 91%, por R$ 1.900. Quer que eu envie as fotos?"
            ),
        },
    ]

    decision = await agent.respond(
        "Na verdade, quero o de bateria 83%; manda fotos",
        history=history,
    )

    assert decision.handoff is False
    assert decision.image_urls == [photo_urls["iphone-14-83"]], decision
    assert decision.product_references == ["iphone-14-83"], decision


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_text", "history_suffix", "photos_available"),
    [
        (
            "Tem foto desse de 1890,00 e 1900,00",
            [],
            True,
        ),
        (
            "É os seminovos ali",
            [
                {"role": "user", "content": "Tem foto desse de 1890,00 e 1900,00"},
                {
                    "role": "assistant",
                    "content": (
                        "Os aparelhos novos lacrados são vendidos por encomenda, "
                        "então não temos fotos do produto cadastradas no sistema."
                    ),
                },
            ],
            True,
        ),
        (
            "Tem foto desse de 1890,00 e 1900,00",
            [],
            False,
        ),
    ],
)
async def test_photo_price_followup_resolves_older_iphone_14_seminovos(
    tmp_path, monkeypatch, request_text, history_suffix, photos_available
):
    async def no_product_files(*_args, **_kwargs):
        return {"data": []}

    monkeypatch.setattr("app.adapters.catalog_cache.list_product_files", no_product_files)
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed_cache = SealedCatalog()
    sealed_cache.items = [
        InventoryItem(
            external_id="sheet:iphone-16-128",
            name="iPhone 16",
            category="Celular",
            capacity="128 GB",
            condition="novo lacrado",
            price_brl=4700,
            source="google_sheets",
            search_text="iphone 16 128 gb novo lacrado",
        )
    ]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "iphone-14-price-photo-followup.json",
        sealed_cache=sealed_cache,
    )
    cache.items = [
        InventoryItem(
            external_id=external_id,
            name="IPHONE 14",
            category="Celular",
            capacity="128GB",
            color="MEIA NOITE",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=price,
            battery_health=battery,
            source="mercado_phone",
            search_text="iphone 14 meia noite 128gb celular seminovo",
            photo_urls=(
                [f"https://photos.example/{external_id}.jpg"]
                if photos_available
                else []
            ),
        )
        for external_id, price, battery in (
            ("iphone-14-1760", 1760, 83),
            ("iphone-14-1890", 1890, 91),
            ("iphone-14-1900", 1900, 91),
        )
    ] + [
        InventoryItem(
            external_id="iphone-16-used",
            name="IPHONE 16",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3630,
            battery_health=88,
            source="mercado_phone",
            search_text="iphone 16 preto 128gb celular seminovo",
            photo_urls=["https://photos.example/iphone-16-used.jpg"],
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [
        {
            "role": "user",
            "content": "Poderia me mandar as opções que tem de iPhone 14 e 15",
        },
        {
            "role": "assistant",
            "content": (
                "Sim 😊 Encontrei estas opções de IPHONE 14 disponíveis:\n"
                "• IPHONE 14 — MEIA NOITE — 128GB — SEMINOVO — R$ 1.760,00 | Bat: 83%\n"
                "• IPHONE 14 — MEIA NOITE — 128GB — SEMINOVO — R$ 1.890,00 | Bat: 91%\n"
                "• IPHONE 14 — MEIA NOITE — 128GB — SEMINOVO — R$ 1.900,00 | Bat: 91%"
            ),
        },
        {"role": "user", "content": "E 15 e 16 tem algo?"},
        {
            "role": "assistant",
            "content": (
                "Sim 😊 Encontrei estas opções de IPHONE 16 disponíveis:\n"
                "📱 Seminovos disponíveis para pronta entrega:\n"
                "• IPHONE 16 — PRETO — 128GB — SEMINOVO — R$ 3.750,00 | Bat: 90%\n"
                "• IPHONE 16 — PRETO — 128GB — SEMINOVO — R$ 3.630,00 | Bat: 88%\n"
                "📦 Novos lacrados por encomenda:\n"
                "• iPhone 16 — Azul Ultramarino — 128 GB — NOVO LACRADO — R$ 4.700,00"
            ),
        },
        *history_suffix,
    ]

    decision = await agent.respond(request_text, history=history)

    if photos_available:
        assert decision.handoff is False, decision
        assert decision.image_urls == [
            "https://photos.example/iphone-14-1890.jpg",
            "https://photos.example/iphone-14-1900.jpg",
        ], decision
        assert decision.product_references == ["iphone-14-1890", "iphone-14-1900"]
    else:
        assert decision.handoff is True, decision
        assert decision.handoff_reason == PRODUCT_PHOTO_HANDOFF_REASON
        assert decision.image_urls == []
        assert decision.product_references == ["iphone-14-1890", "iphone-14-1900"]
        assert "não há fotos cadastradas para" in decision.reply.lower()
    assert "iphone 14" in decision.reply.lower()
    assert "lacrado" not in decision.reply.lower()


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
async def test_bare_iphone_13_photo_request_ignores_stale_history_variants(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-iphone-13-photo-followup.json",
    )
    photo_urls = {
        "iphone-13-estelar-128": "https://photos.example/iphone-13-estelar-128.jpg",
        "iphone-13-meia-noite-128": "https://photos.example/iphone-13-meia-noite-128.jpg",
    }
    cache.items = [
        InventoryItem(
            external_id="iphone-13-estelar-128",
            name="IPHONE 13",
            category="Celular",
            capacity="128GB",
            color="ESTELAR",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1740.0,
            source="mercado_phone",
            search_text="iphone 13 estelar 128gb celular seminovo",
            photo_urls=[photo_urls["iphone-13-estelar-128"]],
        ),
        InventoryItem(
            external_id="iphone-13-meia-noite-128",
            name="IPHONE 13",
            category="Celular",
            capacity="128GB",
            color="MEIA-NOITE",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1900.0,
            source="mercado_phone",
            search_text="iphone 13 meia-noite 128gb celular seminovo",
            photo_urls=[photo_urls["iphone-13-meia-noite-128"]],
        ),
        InventoryItem(
            external_id="iphone-13-pro-128",
            name="IPHONE 13 PRO",
            category="Celular",
            capacity="128GB",
            color="GRAFITE",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2350.0,
            source="mercado_phone",
            search_text="iphone 13 pro grafite 128gb celular seminovo",
            photo_urls=["https://photos.example/iphone-13-pro-128.jpg"],
        ),
        InventoryItem(
            external_id="iphone-13-pro-max-128",
            name="IPHONE 13 PRO MAX",
            category="Celular",
            capacity="128GB",
            color="AZUL SIERRA",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3000.0,
            source="mercado_phone",
            search_text="iphone 13 pro max azul sierra 128gb celular seminovo",
            photo_urls=["https://photos.example/iphone-13-pro-max-128.jpg"],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [
        {"role": "user", "content": "Quais iPhones 13 tem disponível?"},
        {
            "role": "assistant",
            "content": (
                "Sim. Encontrei estas opções de IPHONE 13 disponíveis: "
                "IPHONE 13 — MEIA NOITE — 128GB — SEMINOVO — R$ 1.900,00 | Bat: 95%\n"
                "IPHONE 13 — ESTELAR — 128GB — SEMINOVO — R$ 1.740,00 | Bat: 100%"
            ),
        },
        {"role": "user", "content": "Qual os 12?"},
        {
            "role": "assistant",
            "content": (
                "No momento, não temos iPhone 12 disponível no estoque. "
                "Temos apenas os iPhones 13 seminovos que te enviei."
            ),
        },
        {"role": "user", "content": "Tem como enviar a foto dos que tem disponível?"},
        {
            "role": "assistant",
            "content": (
                "No momento, não localizei esse produto seminovo disponível no sistema. "
                "Algum outro modelo também interessaria? Para facilitar, segue a lista "
                "dos seminovos disponíveis para você escolher: IPHONE 13, IPHONE 13 PRO, "
                "IPHONE 13 PRO MAX"
            ),
        },
    ]

    decision = await agent.respond("Quero vê as fotos do 13", history=history)

    assert decision.handoff is False
    assert set(decision.image_urls) == set(photo_urls.values())
    assert set(decision.product_references) == set(photo_urls)
    assert "iphone 13 pro" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_multi_model_photo_request_sends_all_options_for_each_requested_model(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-13-14-photos.json",
    )
    photo_urls = {
        "iphone-13-128": "https://photos.example/iphone-13-128.jpg",
        "iphone-14-128": "https://photos.example/iphone-14-128.jpg",
        "iphone-14-256": "https://photos.example/iphone-14-256.jpg",
    }
    cache.items = [
        InventoryItem(
            external_id="iphone-13-128",
            name="IPHONE 13",
            category="Celular",
            capacity="128GB",
            color="MEIA NOITE",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=1900,
            search_text="iphone 13 meia noite 128gb celular seminovo",
            photo_urls=[photo_urls["iphone-13-128"]],
        ),
        InventoryItem(
            external_id="iphone-14-128",
            name="IPHONE 14",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2400,
            search_text="iphone 14 azul 128gb celular seminovo fotos cadastradas",
            photo_urls=[photo_urls["iphone-14-128"]],
        ),
        InventoryItem(
            external_id="iphone-14-256",
            name="IPHONE 14",
            category="Celular",
            capacity="256GB",
            color="ROXO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2700,
            search_text="iphone 14 roxo 256gb celular seminovo",
            photo_urls=[photo_urls["iphone-14-256"]],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Manda as fotos do iPhone 13 e 14")

    assert decision.handoff is False
    assert set(decision.image_urls) == set(photo_urls.values())
    assert set(decision.product_references) == set(photo_urls)
    assert "iphone 13" in decision.reply.lower()
    assert "iphone 14" in decision.reply.lower()


@pytest.mark.asyncio
async def test_literal_followup_sends_14_and_15_pro_max_photos_after_13_photo(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-14-15-pro-max-followup.json",
    )
    photo_urls = {
        "14-pro-max-256": "https://photos.example/14-pro-max-256.jpg",
        "15-pro-max-256-a": "https://photos.example/15-pro-max-256-a.jpg",
        "15-pro-max-256-b": "https://photos.example/15-pro-max-256-b.jpg",
    }
    cache.items = [
        InventoryItem(
            external_id="14-pro-max-256",
            name="IPHONE 14 PRO MAX",
            category="Celular",
            capacity="256GB",
            color="ROXO PROFUNDO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3570.0,
            source="mercado_phone",
            search_text="iphone 14 pro max roxo profundo 256gb celular seminovo",
            photo_urls=[photo_urls["14-pro-max-256"]],
        ),
        InventoryItem(
            external_id="15-pro-max-256-a",
            name="IPHONE 15 PRO MAX",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO AZUL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=4130.0,
            source="mercado_phone",
            search_text="iphone 15 pro max titanio azul 256gb celular seminovo",
            photo_urls=[photo_urls["15-pro-max-256-a"]],
        ),
        InventoryItem(
            external_id="15-pro-max-256-b",
            name="IPHONE 15 PRO MAX",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO AZUL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=4070.0,
            source="mercado_phone",
            search_text="iphone 15 pro max titanio azul 256gb celular seminovo",
            photo_urls=[photo_urls["15-pro-max-256-b"]],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    decision = await agent.respond(
        "e do 14 pro max e do 15 pro max\npor favor",
        history=[
            {
                "role": "user",
                "content": "Boa tarde, gostaria de ver os modelos de IPhone com 256GB",
            },
            {
                "role": "assistant",
                "content": (
                    "📋 Lista completa de produtos disponíveis:\n"
                    "IPHONE 14 PRO MAX — ROXO PROFUNDO — 256GB — SEMINOVO — R$ 3.570,00\n"
                    "IPHONE 15 PRO MAX — TITÂNIO AZUL — 256GB — SEMINOVO — R$ 4.130,00\n"
                    "IPHONE 15 PRO MAX — TITÂNIO AZUL — 256GB — SEMINOVO — R$ 4.070,00"
                ),
            },
            {"role": "user", "content": "consegue me mandar uma foto do 13 pro 256GB"},
            {"role": "assistant", "content": "Claro! Seguem as fotos do IPHONE 13 PRO 256GB."},
        ],
    )

    assert decision.handoff is False
    assert set(decision.image_urls) == set(photo_urls.values())
    assert set(decision.product_references) == set(photo_urls)
    assert "14 pro max" in decision.reply.lower()
    assert "15 pro max" in decision.reply.lower()
    assert "não possui fotos" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_range_photo_request_sends_all_available_range_options(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-iphone-range-photos.json",
    )
    photo_urls = {
        "iphone-13-128": "https://photos.example/range-iphone-13-128.jpg",
        "iphone-14-128": "https://photos.example/range-iphone-14-128.jpg",
    }
    cache.items = [
        InventoryItem(
            external_id=external_id,
            name=f"IPHONE {number}",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=1900 + number * 100,
            search_text=f"iphone {number} preto 128gb celular seminovo",
            photo_urls=[photo_urls[external_id]],
        )
        for external_id, number in (("iphone-13-128", 13), ("iphone-14-128", 14))
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "passa valores dols outros iphone e fotos que seja iphone 13 pra cima",
        history=[
            {
                "role": "user",
                "content": "o que tem de iphone 13 pra cima",
            },
            {
                "role": "assistant",
                "content": (
                    "Sim 😊 Encontrei estas opções de IPHONE 13 disponíveis:\n"
                    "• IPHONE 13 — MEIA NOITE — 128GB — SEMINOVO — R$ 1.900,00 | Bat: 95%"
                ),
            },
            {
                "role": "user",
                "content": "vcs trocam a tela do 13?",
            },
            {
                "role": "assistant",
                "content": (
                    "A assistência técnica, incluindo troca de bateria, tela e outros reparos, "
                    "é tratada por um atendente. Vou encaminhar sua mensagem para ele confirmar "
                    "valores e disponibilidade."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert set(decision.image_urls) == set(photo_urls.values())
    assert set(decision.product_references) == set(photo_urls)
    assert "iphone 13" in decision.reply.lower()
    assert "iphone 14" in decision.reply.lower()


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
@pytest.mark.parametrize("fetch_error, expected_handoff", [(False, True), (True, False)])
async def test_iphone_13_rosa_photo_request_hands_off_only_after_empty_lookup(
    tmp_path, monkeypatch, fetch_error, expected_handoff
):
    async def no_product_files(*_args, **_kwargs):
        return {"data": []}

    async def failed_product_files(*_args, **_kwargs):
        raise TimeoutError("attachment service unavailable")

    monkeypatch.setattr(
        "app.adapters.catalog_cache.list_product_files",
        failed_product_files if fetch_error else no_product_files,
    )
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-iphone-13-rosa-no-photo.json",
        sealed_cache=None,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-13-meia-noite-128",
            name="IPHONE 13",
            category="Celular",
            capacity="128GB",
            color="MEIA NOITE",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1650.0,
            source="mercado_phone",
            search_text="iphone 13 meia noite 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-13-rosa-128",
            name="IPHONE 13",
            category="Celular",
            capacity="128GB",
            color="ROSA",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1700.0,
            battery_health=100,
            source="mercado_phone",
            search_text="iphone 13 rosa 128gb celular seminovo",
            photo_urls=[],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    history = [
        {"role": "user", "content": "E o 13"},
        {
            "role": "assistant",
            "content": (
                "No momento, o iPhone 13 branco não está disponível. Encontrei estas opções "
                "em outras cores:\n"
                "• IPHONE 13 — MEIA NOITE — 128GB — SEMINOVO — R$ 1.650,00 | Bat: 86%\n"
                "• IPHONE 13 — ROSA — 128GB — SEMINOVO — R$ 1.700,00 | Bat: 100%"
            ),
        },
    ]
    decision = await agent.respond("Tem foto do rosa?", history=history)

    assert decision.handoff is expected_handoff
    assert decision.image_urls == []
    assert decision.product_references == ["iphone-13-rosa-128"]
    assert "rosa" in decision.reply.lower()
    if expected_handoff:
        assert decision.handoff_reason == PRODUCT_PHOTO_HANDOFF_REASON
        assert "não há fotos cadastradas" in decision.reply.lower()
        assert "vou encaminhar" in decision.reply.lower()
    else:
        assert decision.handoff_reason is None
        assert "não consegui confirmar as fotos" in decision.reply.lower()
        assert "não há fotos cadastradas" not in decision.reply.lower()

        monkeypatch.setattr(
            "app.adapters.catalog_cache.list_product_files", no_product_files
        )
        retried = await agent.respond("Tem foto do rosa?", history=history)
        assert retried.handoff is True
        assert retried.handoff_reason == PRODUCT_PHOTO_HANDOFF_REASON
        assert retried.product_references == ["iphone-13-rosa-128"]


@pytest.mark.asyncio
@pytest.mark.parametrize("include_iphone_14_photo", [False, True])
async def test_multi_model_photo_request_hands_off_only_confirmed_missing_photos(
    tmp_path, monkeypatch, include_iphone_14_photo
):
    async def no_product_files(*_args, **_kwargs):
        return {"data": []}

    monkeypatch.setattr("app.adapters.catalog_cache.list_product_files", no_product_files)
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-multi-iphone-photo-handoff.json",
        sealed_cache=None,
    )
    iphone_14_photo = "https://photos.example/iphone-14-azul-128.jpg"
    cache.items = [
        InventoryItem(
            external_id="iphone-13-rosa-128",
            name="IPHONE 13",
            category="Celular",
            capacity="128GB",
            color="ROSA",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1700,
            source="mercado_phone",
            search_text="iphone 13 rosa 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-14-azul-128",
            name="IPHONE 14",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1900,
            source="mercado_phone",
            search_text="iphone 14 azul 128gb celular seminovo",
            photo_urls=[iphone_14_photo] if include_iphone_14_photo else [],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Manda as fotos do iPhone 13 e 14")

    assert decision.handoff is True
    assert decision.handoff_reason == PRODUCT_PHOTO_HANDOFF_REASON
    assert decision.product_references == ["iphone-13-rosa-128", "iphone-14-azul-128"]
    assert decision.image_urls == ([iphone_14_photo] if include_iphone_14_photo else [])
    assert "iphone 13" in decision.reply.lower()
    assert "iphone 14" in decision.reply.lower()
    assert "vou encaminhar" in decision.reply.lower()


@pytest.mark.asyncio
async def test_ultramarine_photo_followup_ignores_first_capacity_in_previous_list(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-iphone-16-ultramarino.json",
        sealed_cache=None,
    )
    black_256_url = "https://photos.example/iphone-16-black-256.jpg"
    ultramarine_128_url = "https://photos.example/iphone-16-ultramarino-128.jpg"
    cache.items = [
        InventoryItem(
            external_id="iphone-16-black-256",
            name="IPHONE 16",
            category="Celular",
            capacity="256GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3850.0,
            battery_health=88,
            source="mercado_phone",
            search_text="iphone 16 preto 256 gb celular seminovo",
            photo_urls=[black_256_url],
        ),
        InventoryItem(
            external_id="iphone-16-ultramarino-128",
            name="IPHONE 16",
            category="Celular",
            capacity="128GB",
            color="ULTRAMARINO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3630.0,
            battery_health=90,
            source="mercado_phone",
            search_text="iphone 16 ultramarino 128 gb celular seminovo",
            photo_urls=[ultramarine_128_url],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Pode me manda umas fotos do ultramarino",
        history=[
            {"role": "user", "content": "Queria saber do 16 normal"},
            {"role": "user", "content": "Quantos q está saindo"},
            {
                "role": "assistant",
                "content": (
                    "Olá! Tudo bem 😊 O iPhone 16 normal está assim:\n"
                    "Seminovos em estoque:\n"
                    "• 256 GB, preto, bateria 88%: R$ 3.850\n"
                    "• 128 GB, preto, bateria 90%: R$ 3.750\n"
                    "• 128 GB, preto, bateria 88%: R$ 3.630\n"
                    "• 128 GB, ultramarino, bateria 90%: R$ 3.630\n"
                    "Novo lacrado: 128 GB por R$ 4.700."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert decision.image_urls == [ultramarine_128_url], decision.reply
    assert decision.product_references == ["iphone-16-ultramarino-128"]
    assert black_256_url not in decision.image_urls


@pytest.mark.asyncio
async def test_blue_iphone_15_photo_followup_uses_latest_model_after_iphone_14_photo_history(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-iphone-15-after-14-photo.json",
        sealed_cache=None,
    )
    iphone_14_photo = "https://photos.example/iphone-14-meia-noite-256.jpg"
    iphone_15_blue_photo = "https://photos.example/iphone-15-azul-128.jpg"
    iphone_15_green_photo = "https://photos.example/iphone-15-verde-256.jpg"
    cache.items = [
        InventoryItem(
            external_id="iphone-14-256",
            name="IPHONE 14",
            category="Celular",
            capacity="256GB",
            color="MEIA NOITE",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2080.0,
            battery_health=85,
            source="mercado_phone",
            search_text="iphone 14 meia noite 256 gb celular seminovo",
            photo_urls=[iphone_14_photo],
        ),
        InventoryItem(
            external_id="iphone-15-blue-128",
            name="IPHONE 15",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2820.0,
            battery_health=86,
            source="mercado_phone",
            search_text="iphone 15 azul 128 gb celular seminovo",
            photo_urls=[iphone_15_blue_photo],
        ),
        InventoryItem(
            external_id="iphone-15-green-256",
            name="IPHONE 15",
            category="Celular",
            capacity="256GB",
            color="VERDE",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2890.0,
            battery_health=87,
            source="mercado_phone",
            search_text="iphone 15 verde 256 gb celular seminovo",
            photo_urls=[iphone_15_green_photo],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "O azul tem fotos",
        history=[
            {"role": "user", "content": "Qual valor dos iPhone 14"},
            {
                "role": "assistant",
                "content": (
                    "Sim 😊 Encontrei estas opções de IPHONE 14 disponíveis:\n"
                    "• IPHONE 14 — MEIA NOITE — 256GB — SEMINOVO — R$ 2.080,00 | Bat: 85%"
                ),
            },
            {"role": "user", "content": "Tem fotos"},
            {
                "role": "assistant",
                "content": "Claro! Seguem as fotos do IPHONE 14 256GB.",
            },
            {"role": "user", "content": "Parcela em quantas vezes ?"},
            {
                "role": "assistant",
                "content": "Parcelamento do IPHONE 14 256GB. Preço à vista: R$ 2.080,00",
            },
            {"role": "user", "content": "O 15 tem algum"},
            {
                "role": "assistant",
                "content": (
                    "Sim 😊 Encontrei estas opções de IPHONE 15 disponíveis:\n"
                    "• IPHONE 15 — AZUL — 128GB — SEMINOVO — R$ 2.820,00 | Bat: 86%\n"
                    "• IPHONE 15 — VERDE — 256GB — SEMINOVO — R$ 2.890,00 | Bat: 87%"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert decision.image_urls == [iphone_15_blue_photo], decision.reply
    assert decision.product_references == ["iphone-15-blue-128"]
    assert iphone_14_photo not in decision.image_urls
    assert iphone_15_green_photo not in decision.image_urls
    assert "IPHONE 15 128GB" in decision.reply


@pytest.mark.asyncio
async def test_iphone_14_photo_followup_survives_intervening_warranty_questions(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-iphone-14-warranty-photo.json",
        sealed_cache=None,
    )
    purple_url = "https://photos.example/iphone-14-roxo-128-1940.jpg"
    cache.items = [
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
            price_brl=1940.0,
            battery_health=85,
            source="mercado_phone",
            search_text="iphone 14 roxo 128 gb celular seminovo",
            photo_urls=[purple_url],
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Vc tem foto dele?",
        history=[
            {"role": "user", "content": "Está em média quantos o iPhone 13 e 14?"},
            {
                "role": "assistant",
                "content": (
                    "Opa! No momento, encontrei disponível apenas:\n"
                    "iPhone 14 seminovo 128GB roxo — R$ 1.940,00\n"
                    "Saúde da bateria: 85%. Não encontrei iPhone 13 disponível no catálogo agora."
                ),
            },
            {"role": "user", "content": "Tem garantia Apple?"},
            {"role": "user", "content": "Ou garantia msm"},
            {
                "role": "assistant",
                "content": (
                    "Produtos seminovos têm garantia de 90 dias. "
                    "Produtos novos e lacrados têm garantia de 1 ano pela Apple."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert decision.image_urls == [purple_url], decision.reply
    assert decision.product_references == ["10310968"]
    assert "não localizei" not in decision.reply.lower()
    assert "iphone 13" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_color_photo_followup_recovers_when_history_condition_drifts(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-iphone-17-condition-drift.json",
        sealed_cache=None,
    )
    blue_url = "https://photos.example/iphone-17-pro-max-azul-intenso.jpg"
    silver_url = "https://photos.example/iphone-17-pro-max-prateado.jpg"
    cache.items = [
        InventoryItem(
            external_id="10382800",
            name="IPHONE 17 PRO MAX",
            category="Celular",
            capacity="256GB",
            color="PRATEADO",
            condition="LACRADO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=7040.0,
            source="mercado_phone",
            search_text="iphone 17 pro max prateado 256gb celular lacrado",
            photo_urls=[silver_url],
        ),
        InventoryItem(
            external_id="10403807",
            name="IPHONE 17 PRO MAX",
            category="Celular",
            capacity="256GB",
            color="AZUL INTENSO",
            condition="LACRADO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=6930.0,
            source="mercado_phone",
            search_text="iphone 17 pro max azul intenso 256gb celular lacrado",
            photo_urls=[blue_url],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Poderia mandar a foto desse azul intenso",
        history=[
            {
                "role": "assistant",
                "content": (
                    "Encontrei estas opções de iPhone 17 Pro Max seminovo: "
                    "256 GB — Prateado — R$ 7.040,00 — bateria 100%; "
                    "256 GB — Azul intenso — R$ 6.930,00 — bateria 100%; "
                    "Quer fotos de alguma opção?"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert decision.image_urls == [blue_url]
    assert decision.product_references == ["10403807"]
    assert "não localizei" not in decision.reply.lower()
    assert silver_url not in decision.image_urls


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


@pytest.mark.asyncio
async def test_photo_question_after_two_alternative_models_sends_both_offered_photos(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-photo-alternative-models.json",
    )
    iphone_16_url = "https://photos.example/iphone-16-256-preto.jpg"
    iphone_16_pro_max_url = "https://photos.example/iphone-16-pro-max-256-preto.jpg"
    unrelated_color_url = "https://photos.example/iphone-16-pro-max-256-deserto.jpg"
    cache.items = [
        InventoryItem(
            external_id="iphone-16-256-preto",
            name="IPHONE 16",
            category="Celular",
            capacity="256GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3850.0,
            source="mercado_phone",
            search_text="iphone 16 preto 256gb celular seminovo",
            photo_urls=[iphone_16_url],
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-256-preto",
            name="IPHONE 16 PRO MAX",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=5280.0,
            source="mercado_phone",
            search_text="iphone 16 pro max titanio preto 256gb celular seminovo",
            photo_urls=[iphone_16_pro_max_url],
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-256-deserto",
            name="IPHONE 16 PRO MAX",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO DESERTO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=5170.0,
            source="mercado_phone",
            search_text="iphone 16 pro max titanio deserto 256gb celular seminovo",
            photo_urls=[unrelated_color_url],
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [
        {
            "role": "user",
            "content": "Quais iPhone 16 Pro Max seminovos de 256 GB tem?",
        },
        {
            "role": "assistant",
            "content": (
                "IPHONE 16 PRO MAX\n"
                "• TITÂNIO DESERTO - 256GB - SEMINOVO — R$ 5.170,00 | Bat: 89%\n"
                "• TITÂNIO DESERTO - 256GB - SEMINOVO — R$ 5.170,00 | Bat: 85%\n"
                "• TITÂNIO PRETO - 256GB - SEMINOVO — R$ 5.280,00 | Bat: 89%\n"
                "• TITÂNIO NATURAL - 512GB - SEMINOVO — R$ 5.510,00 | Bat: 91%\n"
                "• TITÂNIO NATURAL - 1TB - SEMINOVO — R$ 5.500,00 | Bat: 89%"
            ),
        },
        {"role": "user", "content": "Quero o 256g"},
        {"role": "user", "content": "Preto"},
        {
            "role": "assistant",
            "content": (
                "Entendi: iPhone 16 Pro, 256 GB, preto. Não localizei essa opção "
                "disponível no momento. Como alternativas, temos o iPhone 16 256 GB "
                "preto por R$ 3.850 ou o iPhone 16 Pro Max 256 GB titânio preto por "
                "R$ 5.280. Algum deles te interessa?"
            ),
        },
    ]

    decision = await agent.respond("Tem fotos deles ?", history=history)

    assert decision.handoff is False
    assert set(decision.image_urls) == {iphone_16_url, iphone_16_pro_max_url}
    assert set(decision.product_references) == {
        "iphone-16-256-preto",
        "iphone-16-pro-max-256-preto",
    }
    assert unrelated_color_url not in decision.image_urls
    assert "iphone 16" in decision.reply.lower()
    assert "iphone 16 pro max" in decision.reply.lower()
    assert "não localizei esse produto seminovo" not in decision.reply.lower()
