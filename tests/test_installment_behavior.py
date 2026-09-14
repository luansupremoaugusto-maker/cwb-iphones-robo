from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.agent import AgentService, _installment_context_query
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem


class FakeMercadoClient:
    async def fetch_all_inventory(self):
        return []


class FakeSealedCache:
    def __init__(self):
        self.items = [
            InventoryItem(
                external_id="sheet:e",
                name="iPhone 17 E",
                capacity="256 GB",
                price_brl=4600.0,
                condition="novo lacrado",
                source="google_sheets",
                search_text="iphone 17 e 256 gb novo lacrado",
            ),
            InventoryItem(
                external_id="sheet:air",
                name="iPhone 17 Air",
                capacity="256 GB",
                price_brl=5700.0,
                condition="novo lacrado",
                source="google_sheets",
                search_text="iphone 17 air 256 gb novo lacrado",
            ),
            InventoryItem(
                external_id="sheet:base",
                name="iPhone 17",
                capacity="256 GB",
                price_brl=5700.0,
                condition="novo lacrado",
                source="google_sheets",
                search_text="iphone 17 256 gb novo lacrado",
            ),
            InventoryItem(
                external_id="sheet:pro",
                name="iPhone 17 Pro",
                capacity="256 GB",
                price_brl=6800.0,
                condition="novo lacrado",
                source="google_sheets",
                search_text="iphone 17 pro 256 gb novo lacrado",
            ),
        ]

    async def search(self, query: str, limit: int = 5):
        return self.items[:limit]

    async def get(self, product_id: str):
        return next((item for item in self.items if item.external_id == product_id), None)


def build_cache(tmp_path):
    settings = Settings(
        google_sheets_enabled=True,
        mercado_cache_ttl_seconds=60,
        faq_path=str(tmp_path / "faq.yaml"),
    )
    cache = StoreCatalogCache(
        FakeMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=FakeSealedCache(),
    )
    cache.last_refresh = time.time()
    return cache, settings


@pytest.mark.asyncio
async def test_normal_model_is_preferred_for_installment_lookup(tmp_path):
    cache, _settings = build_cache(tmp_path)

    result = await cache.simulate_all_installments("iPhone 17 normal 256 GB")

    assert result["encontrado"] is True
    assert result["nome"] == "iPhone 17"
    assert len(result["parcelas"]) == 18


@pytest.mark.asyncio
async def test_contextual_installment_question_returns_full_table(tmp_path):
    cache, settings = build_cache(tmp_path)
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "preço, quanto fica parcelado?",
        history=[
            {
                "role": "assistant",
                "content": "O iPhone 17 normal 256 GB novo lacrado está disponível. O valor é R$ 5.700.",
            }
        ],
    )

    assert decision.handoff is False
    assert "1x de" in decision.reply
    assert "18x de" in decision.reply
    assert "em quantas vezes" not in decision.reply.lower()


def test_installment_context_prefers_explicit_current_model_over_stale_catalog_answer():
    history = [
        {
            "role": "assistant",
            "content": (
                "Seminovos disponíveis:\n"
                "- iPhone XR 64 GB - BRANCO - SEMINOVO — R$ 500,00"
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Taxas do cartão na máquina física:\n"
                "1x: 4,95%\n"
                "2x: 5,62%\n"
                "Qual modelo e capacidade você gostaria de simular?"
            ),
        },
    ]

    query = _installment_context_query("Do iPhone 12", history)

    assert query == "Do iPhone 12"


@pytest.mark.asyncio
async def test_rate_followup_simulates_the_current_model_after_an_xr_catalog_answer(tmp_path):
    cache, settings = build_cache(tmp_path)
    cache.items = [
        InventoryItem(
            external_id="iphone-xr-64",
            name="iPhone XR",
            category="Celular Apple",
            capacity="64 GB",
            condition="SEMINOVO",
            availability="Disponível",
            quantity=1,
            price_brl=500.0,
            search_text="iphone xr 64 gb branco seminovo celular apple",
        ),
        InventoryItem(
            external_id="iphone-12-128",
            name="iPhone 12",
            category="Celular Apple",
            capacity="128 GB",
            condition="SEMINOVO",
            availability="Disponível",
            quantity=1,
            price_brl=2200.0,
            search_text="iphone 12 128 gb seminovo celular apple",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Do iPhone 12",
        history=[
            {
                "role": "assistant",
                "content": (
                    "Seminovos disponíveis:\n"
                    "- iPhone XR 64 GB - BRANCO - SEMINOVO — R$ 500,00"
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "Taxas do cartão na máquina física:\n"
                    "1x: 4,95%\n"
                    "2x: 5,62%\n"
                    "Qual modelo e capacidade você gostaria de simular?"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "Parcelamento do iPhone 12 128 GB" in decision.reply
    assert "Preço à vista: R$ 2.200,00" in decision.reply
    assert "Parcelamento do iPhone XR" not in decision.reply


@pytest.mark.asyncio
async def test_specific_installment_request_returns_full_comparison_table(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60, faq_path="data/faq.yaml")
    cache = StoreCatalogCache(
        FakeMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-13-pro-max-256-green",
            name="iPhone 13 Pro Max",
            capacity="256 GB",
            color="Verde Alpino",
            category="Celular",
            condition="seminovo",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3160.0,
            battery_health=90,
            search_text=(
                "iphone 13 pro max 256 gb verde alpino celular seminovo "
                "bateria 90"
            ),
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Quanto fica em 5 ou 6 X",
        history=[
            {
                "role": "assistant",
                "content": (
                    "O iPhone 13 Pro Max 512 GB Grafite do anúncio não está disponível. "
                    "Temos esta opção: iPhone 13 Pro Max 256 GB Verde Alpino — "
                    "seminovo, bateria 90%, R$ 3.160."
                ),
            },
            {"role": "user", "content": "só tem verde?"},
            {"role": "user", "content": "Tem outras opções?"},
            {
                "role": "assistant",
                "content": (
                    "No momento, para o iPhone 13 Pro Max, temos apenas esta opção "
                    "disponível: iPhone 13 Pro Max 256 GB Verde Alpino — seminovo, "
                    "bateria 90%, R$ 3.160."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "1x de" in decision.reply
    assert "5x de" in decision.reply
    assert "6x de" in decision.reply
    assert "18x de" in decision.reply


@pytest.mark.asyncio
async def test_specific_installment_keeps_selected_battery_on_short_followup(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        FakeMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    low_battery = InventoryItem(
        external_id="14-pro-max-128-81",
        name="IPHONE 14 PRO MAX",
        category="Celular",
        capacity="128GB",
        color="ROXO",
        condition="SEMINOVO",
        availability="Disponivel para venda",
        quantity=1,
        price_brl=3140.0,
        battery_health=81,
        search_text="iphone 14 pro max roxo 128 gb bateria 81 celular seminovo",
    )
    selected = low_battery.model_copy(
        update={
            "external_id": "14-pro-max-128-86",
            "price_brl": 3300.0,
            "battery_health": 86,
            "search_text": "iphone 14 pro max roxo 128 gb bateria 86 celular seminovo",
        }
    )
    cache.items = [low_battery, selected]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Bateria 86%",
        history=[
            {"role": "user", "content": "Parcelado ficaria quantos em 12x?"},
            {
                "role": "assistant",
                "content": "Claro! Para qual aparelho voce quer simular em 12x?",
            },
            {"role": "user", "content": "14 pro Max Roxo 128 gb bateria 86%"},
            {
                "role": "assistant",
                "content": (
                    "Encontrei duas opcoes de iPhone 14 Pro Max roxo, 128 GB: "
                    "R$ 3.140 (bateria 81%) e R$ 3.300 (bateria 86%)."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "R$ 3.300,00" in decision.reply
    assert "12x de R$ 319,95" in decision.reply
    assert "R$ 3.140,00" not in decision.reply


@pytest.mark.asyncio
async def test_batched_model_and_installment_question_keeps_selected_iphone(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        FakeMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="14-plus-blue-128",
            name="IPHONE 14 PLUS",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2250.0,
            battery_health=97,
            search_text="iphone 14 plus azul 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="14-plus-star-128",
            name="IPHONE 14 PLUS",
            category="Celular",
            capacity="128GB",
            color="ESTELAR",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2250.0,
            battery_health=97,
            search_text="iphone 14 plus estelar 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="14-pro-purple-128",
            name="IPHONE 14 PRO",
            category="Celular",
            capacity="128GB",
            color="ROXO PROFUNDO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2930.0,
            battery_health=85,
            search_text="iphone 14 pro roxo profundo 128 gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "14 plus azul\nEm 8 vezes ficaria quantos?",
        history=[
            {"role": "user", "content": "14 plus 14 pro"},
            {
                "role": "assistant",
                "content": (
                    "Temos iPhone 14 Plus azul e estelar, além de iPhone 14 Pro roxo. "
                    "Qual deles prefere simular no cartão?"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "Parcelamento do IPHONE 14 PLUS 128GB" in decision.reply
    assert "8x de R$ 315,37" in decision.reply
    assert "não localizei" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_current_bare_capacity_wins_over_historical_multi_unit_context(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        FakeMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="15-pro-512",
            name="IPHONE 15 PRO",
            category="Celular",
            capacity="512GB",
            color="TITÂNIO PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3900.0,
            battery_health=89,
            search_text="iphone 15 pro titanio preto 512 gb celular seminovo",
        ),
        InventoryItem(
            external_id="15-pro-128-black",
            name="IPHONE 15 PRO",
            category="Celular",
            capacity="128GB",
            color="TITÂNIO PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3530.0,
            battery_health=88,
            search_text="iphone 15 pro titanio preto 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="15-pro-128-natural",
            name="IPHONE 15 PRO",
            category="Celular",
            capacity="128GB",
            color="TITÂNIO NATURAL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3410.0,
            battery_health=86,
            search_text="iphone 15 pro titanio natural 128 gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "quanto fica o pro de 512 em 12x",
        history=[
            {"role": "user", "content": "boa noite, tudo bem?"},
            {
                "role": "assistant",
                "content": "Cwb.iphones agradece seu contato. Como podemos ajudar?",
            },
            {"role": "user", "content": "ainda tem o 15 pro disponível?"},
            {
                "role": "assistant",
                "content": (
                    "Sim 😊 Encontrei estas opções de IPHONE 15 PRO disponíveis:\n"
                    "• IPHONE 15 PRO — TITÂNIO PRETO — 512GB — SEMINOVO — "
                    "R$ 3.900,00 | Bat: 89%\n"
                    "• IPHONE 15 PRO — TITÂNIO PRETO — 128GB — SEMINOVO — "
                    "R$ 3.530,00 | Bat: 88%\n"
                    "• IPHONE 15 PRO — TITÂNIO NATURAL — 128GB — SEMINOVO — "
                    "R$ 3.410,00 | Bat: 86%"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "Parcelamento do IPHONE 15 PRO 512GB" in decision.reply
    assert "Preço à vista: R$ 3.900,00" in decision.reply
    assert "12x de R$ 378,13 (total R$ 4.537,52)" in decision.reply
    assert "128GB" not in decision.reply


@pytest.mark.asyncio
async def test_model_answer_after_photo_simulation_prompt_routes_to_installment_choice(tmp_path):
    settings = Settings(mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        FakeMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="15-pro-128-black",
            name="IPHONE 15 PRO",
            category="Celular",
            capacity="128GB",
            color="TITÂNIO PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3530.0,
            battery_health=88,
            photo_urls=["https://photos.example/15-pro-128-black.jpg"],
            search_text="iphone 15 pro titanio preto 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="15-pro-128-natural",
            name="IPHONE 15 PRO",
            category="Celular",
            capacity="128GB",
            color="TITÂNIO NATURAL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3410.0,
            battery_health=86,
            photo_urls=["https://photos.example/15-pro-128-natural.jpg"],
            search_text="iphone 15 pro titanio natural 128 gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "iPhone 15 pro 128GB titânio",
        history=[
            {"role": "user", "content": "Quais opções vcs tem até 3.900 ?"},
            {
                "role": "assistant",
                "content": (
                    "Até R$ 3.900, temos:\n"
                    "• iPhone 15 Pro 128GB Titânio natural — R$ 3.410 | bateria 86%\n"
                    "• iPhone 15 Pro 128GB Titânio preto — R$ 3.530 | bateria 88%"
                ),
            },
            {"role": "user", "content": "Teria fotos de 15 pro 128gb"},
            {
                "role": "assistant",
                "content": "Claro! Seguem as fotos do IPHONE 15 PRO 128GB.",
            },
            {"role": "user", "content": "Simula esse em 8 vezes"},
            {
                "role": "assistant",
                "content": (
                    "Claro! Qual é o modelo e a capacidade do aparelho para eu fazer "
                    "a simulação completa de 1x a 18x?"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "não localizei" not in decision.reply.lower()
    assert "qual delas você quer simular" in decision.reply.lower()
    assert "R$ 3.530,00" in decision.reply
    assert "R$ 3.410,00" in decision.reply
    assert set(decision.product_references) == {
        "15-pro-128-black",
        "15-pro-128-natural",
    }


@pytest.mark.asyncio
async def test_missing_seminew_installment_offers_available_seminew_models(tmp_path):
    cache, settings = build_cache(tmp_path)
    settings.openai_api_key = None
    cache.items = [
        InventoryItem(
            external_id="12-128",
            name="iPhone 12",
            capacity="128 GB",
            category="Celular",
            condition="seminovo",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=1600.0,
            battery_health=88,
            search_text="iphone 12 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="14-256",
            name="iPhone 14",
            capacity="256 GB",
            category="Celular",
            condition="seminovo",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2200.0,
            battery_health=91,
            search_text="iphone 14 256 gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=False)

    decision = await agent.respond(
        "consegue simular com 128GB e com o de 256, por gentileza? isso ate 10x por gentileza",
        history=[
            {
                "role": "assistant",
                "content": "Claro! O iPhone 13 pode ser de 128 GB ou 256 GB. Qual capacidade voce prefere?",
            }
        ],
    )

    assert decision.handoff is False
    assert "outro modelo" in decision.reply.lower()
    assert "seminovos dispon" in decision.reply.lower()
    assert "iPhone 12" in decision.reply
    assert "iPhone 14" in decision.reply
    assert "Novos lacrados" not in decision.reply
