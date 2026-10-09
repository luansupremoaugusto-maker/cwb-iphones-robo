from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.adapters.catalog_cache import StoreCatalogCache, _requested_iphone_model_keys
from app.agent import (
    AgentService,
    CATALOG_BUYER_DETAILS_REPLY,
    Runner,
    _extract_budget_limit,
    _extract_bare_catalog_model_reference,
    _format_product_availability,
    _is_bare_model_availability_request,
    _is_cheapest_catalog_request,
    _is_available_list_request,
    _is_product_availability_request,
    _normalize,
    _requested_catalog_colors,
    _unavailable_catalog_color_header,
    _requested_device_quantity,
)
from app.config import Settings
from app.faq import FAQStore
from app.schemas import AgentDecision, InventoryItem
from app.trade_in import (
    TRADE_IN_FORM,
    TRADE_IN_REASON,
    is_trade_in_context_request,
    is_trade_in_request,
)


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


def _sealed_item(item_id: str, name: str, capacity: str, price: float) -> InventoryItem:
    return InventoryItem(
        external_id=item_id,
        name=name,
        category="Novo lacrado",
        capacity=capacity,
        price_brl=price,
        source="google_sheets",
        condition="novo lacrado",
        availability="Preco confirmado",
        search_text=f"{name} {capacity} novo lacrado",
    )


class SealedCatalog:
    def __init__(self):
        self.items = [
            _sealed_item("13-pro-128", "iPhone 13 Pro", "128 GB", 5000),
            _sealed_item("13-pro-256", "iPhone 13 Pro", "256 GB", 5500),
            _sealed_item("17-pro-max-128", "iPhone 17 Pro Max", "128 GB", 7000),
            _sealed_item("17-pro-max-256", "iPhone 17 Pro Max", "256 GB", 7800),
            _sealed_item("17-pro-512", "iPhone 17 Pro", "512 GB", 8000),
            _sealed_item("17-air-512", "iPhone 17 Air", "512 GB", 8500),
            _sealed_item("macbook-neo-13", "MacBook Neo 2026 13", "256 GB", 4900),
            _sealed_item("macbook-air-15", "MacBook Air", "512 GB", 8500),
            _sealed_item("ipad-air-128", "iPad Air", "128 GB", 3000),
        ]

    async def ensure_fresh(self):
        return None

    async def search(self, query: str, limit: int = 5):
        return self.items[:limit]

    async def get(self, product_id: str):
        return next((item for item in self.items if item.external_id == product_id), None)


def test_shared_pro_max_suffix_extracts_each_number_as_a_requested_model():
    assert _requested_iphone_model_keys("Queria saber sobre valores 15 16 17 pro Max") == (
        (15, "pro max"),
        (16, "pro max"),
        (17, "pro max"),
    )


def test_pro_max_conjunction_with_do_keeps_both_requested_models():
    assert _requested_iphone_model_keys(
        "Gostaria de saber quais cores do iPhone 14 Pro Max e do 15 pro Max "
        "tem disponível para encomenda"
    ) == ((14, "pro max"), (15, "pro max"))


def test_model_range_and_e_joined_base_models_keep_model_identity():
    assert _requested_iphone_model_keys(
        "Quais iPhones estão disponíveis do 14 até o 16?"
    ) == ((14, ""), (15, ""), (16, ""))
    assert _requested_iphone_model_keys(
        "Quero saber os modelos entre o 14, 15 e 16 de 256 GB"
    ) == ((14, ""), (15, ""), (16, ""))
    assert _requested_iphone_model_keys("iPhone 16e") == ((16, "e"),)


def test_past_purchase_count_is_not_treated_as_requested_quantity():
    assert _requested_device_quantity("Já comprei 2 celular com vc") is None
    assert _requested_device_quantity("Preciso de 2 aparelhos") == 2


def build_agent(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=SealedCatalog(),
    )
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


def test_delivery_deadline_is_not_parsed_as_budget_limit():
    assert _extract_budget_limit("iPhone 17 com entrega em até 1 semana") is None
    assert _extract_budget_limit("iPhone 17 até R$ 7.200,00") == 7200
    assert _extract_budget_limit("orçamento do iPhone 15 Pro Max") is None
    model_range = "Gostaria de ver quais iPhones têm disponível do 14 até o 16, com 256 GB"
    assert _extract_budget_limit(model_range) is None
    assert _extract_budget_limit(f"{model_range}, com orçamento até R$ 7.200,00") == 7200


def test_latest_valid_budget_ceiling_wins_within_one_message():
    revised_budget = (
        "Quais iPhones vocês têm até uns 3,200? "
        "Na verdade, pode considerar até uns 4,000."
    )
    deadline_then_budget = (
        "iPhone 15 com entrega em até 1 semana; orçamento até R$ 4.000"
    )

    assert _extract_budget_limit(revised_budget) == 4000
    assert _extract_budget_limit(deadline_then_budget) == 4000


def test_thousands_separator_budget_is_not_parsed_as_a_bare_model():
    query = "Olá, gostaria de saber quais celulares vc tem na faixa de 1.000 reais"

    assert _extract_budget_limit(query) == 1000
    assert _extract_bare_catalog_model_reference(query) is None
    assert _is_bare_model_availability_request(query) is False
    assert _is_product_availability_request(query) is True
    assert (
        _extract_bare_catalog_model_reference(
            "Tem o 15 na faixa de 1.000 reais?"
        )
        == "iPhone 15"
    )


def test_battery_percentage_is_not_parsed_as_a_bare_catalog_model():
    assert _extract_bare_catalog_model_reference("Procuro com bateria acima de 85%") is None
    assert _extract_bare_catalog_model_reference("Qual a saúde da bateria do 15?") == "iPhone 15"


def test_warranty_question_without_stock_request_is_not_product_availability():
    assert _is_product_availability_request("Qual a garantia do iPhone 13 Pro Max seminovo?") is False


@pytest.mark.asyncio
async def test_orcamento_do_iphone_15_pro_max_nao_vira_limite_de_15_reais(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-15-pro-max-budget-regression.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-pro-max-256-86",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO AZUL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=4130,
            battery_health=86,
            search_text="iphone 15 pro max titanio azul 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-max-256-87",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO AZUL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=4070,
            battery_health=87,
            search_text="iphone 15 pro max titanio azul 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Gostaria de saber sobre o orçamento do iPhone 15 pro Max",
        history=[
            {"role": "user", "content": "Ola boa tarde"},
            {"role": "assistant", "content": "Olá, boa tarde! 😊 Como posso ajudar?"},
        ],
    )
    normalized = _normalize(decision.reply)

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-15-pro-max-256-86",
        "iphone-15-pro-max-256-87",
    }
    assert "iPhone 15 Pro Max" in decision.reply
    assert "R$ 4.130,00" in decision.reply
    assert "R$ 4.070,00" in decision.reply
    assert "até R$ 15,00" not in decision.reply
    assert "não localizei aparelhos" not in normalized


@pytest.mark.asyncio
async def test_explicit_iphone_13_pro_keeps_model_and_requested_capacities(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Queria o 13 PRO que tem tela de 120hz, armazenamento pode ser 128 ou 256"
    )

    assert set(decision.product_references) == {"13-pro-128", "13-pro-256"}
    assert "IPHONE 13 PRO" in decision.reply.upper()
    assert "128 GB" in decision.reply.upper()
    assert "256 GB" in decision.reply.upper()
    assert "17 PRO" not in decision.reply.upper()
    assert "MACBOOK" not in decision.reply.upper()


@pytest.mark.asyncio
async def test_real_17_pro_max_request_returns_both_bare_capacity_options(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = []
    agent.cache.sealed_cache.items = [
        _sealed_item("sheet:bot:10", "iPhone 17 Pro Max", "512 GB", 8700).model_copy(
            update={"colors": "Laranja-cósmico | Azul-intenso | Prateado"}
        ),
        _sealed_item("sheet:bot:11", "iPhone 17 Pro Max", "1 TB", 10500).model_copy(
            update={"colors": "Laranja-cósmico | Azul-intenso | Prateado"}
        ),
    ]

    decision = await agent.respond(
        "queria saber se vcs tem disponível algum iPhone 17 pro max de 512gb "
        "ou de 1t na cor branca"
    )

    assert decision.handoff is False
    assert decision.product_references == ["sheet:bot:10", "sheet:bot:11"]
    assert "512 GB" in decision.reply
    assert "1 TB" in decision.reply
    assert decision.reply.count("NOVO LACRADO") == 2


@pytest.mark.asyncio
async def test_bare_variant_followup_after_base_model_reply_lists_17_pro_max_options(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "iphone-17-pro-max-followup.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="17-pro-max-seminovo",
            name="iPhone 17 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=6500,
            battery_health=95,
            search_text="iphone 17 pro max preto 256 gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "17 pro max",
        history=[
            {"role": "user", "content": "Olá tudo bem"},
            {
                "role": "assistant",
                "content": "Cwb.iphones agradece seu contato. Como podemos ajudar?",
            },
            {"role": "user", "content": "Qual valor do 17"},
            {
                "role": "assistant",
                "content": (
                    "Sim 😊 Encontrei estas opções de iPhone 17 disponíveis:\n"
                    "• iPhone 17 — Lavanda | Azul-névoa | Sálvia | Branco | Preto — 256 GB — "
                    "NOVO LACRADO — R$ 5.600,00 | Bat: não se aplica"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert decision.confidence == "high"
    assert set(decision.product_references) == {
        "17-pro-max-seminovo",
        "17-pro-max-128",
        "17-pro-max-256",
    }
    assert "iPhone 17 Pro Max" in decision.reply
    assert "SEMINOVO" in decision.reply.upper()
    assert "NOVO LACRADO" in decision.reply.upper()
    assert "6.500,00" in decision.reply
    assert "7.000,00" in decision.reply
    assert "7.800,00" in decision.reply
    assert "iPhone 17 —" not in decision.reply


@pytest.mark.asyncio
async def test_missing_iphone_13_pro_512_does_not_return_17_pro_options(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("iPhone 13 Pro verde 512 GB")

    assert decision.product_references == []
    assert "17" not in decision.reply
    assert "MacBook" not in decision.reply


@pytest.mark.asyncio
async def test_bare_iphone_13_followup_does_not_return_macbook_13(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("O 13 nao tem mais?")

    assert decision.product_references == []
    assert "MacBook" not in decision.reply
    assert "17" not in decision.reply


@pytest.mark.asyncio
async def test_bare_model_sell_question_returns_only_requested_iphone(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.sealed_cache.items.insert(
        0,
        _sealed_item("iphone-17-lacrado", "iPhone 17", "256 GB", 5700),
    )

    decision = await agent.respond(
        "Vocês tem o 17 pra vender?",
        history=[
            {"role": "user", "content": "Eu comprei um iPhone com vocês e queria indicar a loja."},
            {"role": "assistant", "content": "Tudo certo! Como posso ajudar você hoje?"},
        ],
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-17-lacrado"]
    assert "iPhone 17" in decision.reply
    assert "iPhone 17 Pro" not in decision.reply
    assert "iPhone 17 Pro Max" not in decision.reply
    assert "lista completa" not in decision.reply.lower()


def _iphone15_price_followup_agent(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        InventoryItem(
            external_id="iphone-15-seminovo",
            name="iPhone 15",
            category="Celular",
            capacity="128 GB",
            color="PRETO",
            colors="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3500,
            battery_health=89,
            photo_urls=["https://photos.example/iphone-15-preto-128.jpg"],
            search_text="iphone 15 preto 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-rosa-seminovo",
            name="iPhone 15",
            category="Celular",
            capacity="128 GB",
            color="ROSA",
            colors="ROSA",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3600,
            battery_health=90,
            search_text="iphone 15 rosa 128 gb celular seminovo",
        ),
    ]
    agent.cache.last_refresh = time.time()
    agent.cache.sealed_cache.items.append(
        _sealed_item("iphone-15-lacrado", "iPhone 15", "128 GB", 4400)
    )
    return agent


@pytest.mark.asyncio
async def test_bare_model_reply_to_product_interest_lists_available_iphone_details(tmp_path):
    agent = _iphone15_price_followup_agent(tmp_path)

    decision = await agent.respond(
        "15",
        history=[
            {
                "role": "assistant",
                "content": "Bom dia, tudo bem? Teria interesse em algum produto específico?",
            }
        ],
    )

    assert decision.handoff is False
    assert decision.image_urls == []
    assert set(decision.product_references) == {
        "iphone-15-seminovo",
        "iphone-15-rosa-seminovo",
    }
    assert "iPhone 15" in decision.reply
    assert "SEMINOVO" in decision.reply.upper()
    assert "3.500,00" in decision.reply
    assert "3.600,00" in decision.reply
    assert "89%" in decision.reply
    assert "90%" in decision.reply
    assert "NOVO LACRADO" not in decision.reply.upper()
    assert "4.400,00" not in decision.reply


@pytest.mark.asyncio
async def test_iphone15_price_followup_after_photos_keeps_seminovo_context(tmp_path):
    agent = _iphone15_price_followup_agent(tmp_path)

    decision = await agent.respond(
        "Qual valor",
        history=[
            {
                "role": "assistant",
                "content": "Bom dia, tudo bem? Teria interesse em algum produto específico?",
            },
            {"role": "user", "content": "15"},
            {
                "role": "assistant",
                "content": "Claro! Seguem as fotos do IPHONE 15 128GB.",
            },
        ],
    )

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-15-seminovo",
        "iphone-15-rosa-seminovo",
    }
    assert "SEMINOVO" in decision.reply.upper()
    assert "3.500,00" in decision.reply
    assert "3.600,00" in decision.reply
    assert "NOVO LACRADO" not in decision.reply.upper()
    assert "4.400,00" not in decision.reply


@pytest.mark.asyncio
async def test_iphone15_explicit_photo_and_sealed_requests_keep_their_conditions(tmp_path):
    agent = _iphone15_price_followup_agent(tmp_path)

    photo_decision = await agent.respond("Pode me mandar foto do iPhone 15 seminovo?")
    sealed_decision = await agent.respond("Qual valor do iPhone 15 novo lacrado?")

    assert photo_decision.image_urls == ["https://photos.example/iphone-15-preto-128.jpg"]
    assert "iphone-15-seminovo" in photo_decision.product_references
    assert "iphone-15-lacrado" not in photo_decision.product_references
    assert sealed_decision.product_references == ["iphone-15-lacrado"]
    assert "NOVO LACRADO" in sealed_decision.reply.upper()
    assert "4.400,00" in sealed_decision.reply
    assert "SEMINOVO" not in sealed_decision.reply.upper()


@pytest.mark.asyncio
async def test_iphone_range_from_13_up_lists_every_available_model(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "iphone-range.json",
    )
    cache.items = [
        InventoryItem(
            external_id=f"iphone-{number}-128",
            name=f"iPhone {number}",
            category="Celular",
            capacity="128 GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=1900 + number * 100,
            search_text=f"iphone {number} preto 128 gb celular seminovo",
        )
        for number in (13, 14, 15, 16, 17)
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("o que tem de iphone 13 pra cima")

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-13-128",
        "iphone-14-128",
        "iphone-15-128",
        "iphone-16-128",
        "iphone-17-128",
    }
    for number in (13, 14, 15, 16, 17):
        assert f"iPhone {number}" in decision.reply


@pytest.mark.asyncio
async def test_batched_base_model_query_keeps_all_options_for_each_requested_model(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "iphone-13-14-options.json",
    )
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
            search_text="iphone 14 azul 128gb celular seminovo valores cadastrados",
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
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Quero os valores do iPhone 13 e 14")

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-13-128",
        "iphone-14-128",
        "iphone-14-256",
    }
    assert "IPHONE 13" in decision.reply.upper()
    assert decision.reply.upper().count("IPHONE 14") >= 2
    assert "128GB" in decision.reply.upper()
    assert "256GB" in decision.reply.upper()


@pytest.mark.asyncio
async def test_bare_model_budget_request_keeps_only_iphone_14_and_15(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "bare-iphone-14-15-budget.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-14-base-128",
            name="iPhone 14",
            category="Celular",
            capacity="128 GB",
            color="AZUL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2400,
            source="mercado_phone",
            search_text="iphone 14 azul 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-14-pro-128",
            name="iPhone 14 Pro",
            category="Celular",
            capacity="128 GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3010,
            source="mercado_phone",
            search_text="iphone 14 pro preto 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-base-128",
            name="iPhone 15",
            category="Celular",
            capacity="128 GB",
            color="AZUL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2820,
            source="mercado_phone",
            search_text="iphone 15 azul 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-128",
            name="iPhone 15 Pro",
            category="Celular",
            capacity="128 GB",
            color="TITÂNIO PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3530,
            source="mercado_phone",
            search_text="iphone 15 pro titanio preto 128 gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("gostaria de saber o orçamento do 15 e do 14")

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-14-base-128",
        "iphone-15-base-128",
    }
    assert "IPHONE 14" in decision.reply.upper()
    assert "IPHONE 15" in decision.reply.upper()
    assert "2.400,00" in decision.reply
    assert "2.820,00" in decision.reply
    assert "IPHONE 14 PRO" not in decision.reply.upper()
    assert "IPHONE 15 PRO" not in decision.reply.upper()
    assert "lista completa" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_bare_model_budget_request_reports_unavailable_base_model(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "bare-iphone-14-15-budget-missing-base.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-14-pro-256",
            name="iPhone 14 Pro",
            category="Celular",
            capacity="256 GB",
            color="ROXO PROFUNDO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3010,
            source="mercado_phone",
            search_text="iphone 14 pro roxo profundo 256 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-14-pro-max-128",
            name="iPhone 14 Pro Max",
            category="Celular",
            capacity="128 GB",
            color="PRETO ESPACIAL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3080,
            source="mercado_phone",
            search_text="iphone 14 pro max preto espacial 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-base-128",
            name="iPhone 15",
            category="Celular",
            capacity="128 GB",
            color="AZUL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2820,
            source="mercado_phone",
            search_text="iphone 15 azul 128 gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("gostaria de saber o orçamento do 15 e do 14")

    assert decision.handoff is False
    assert decision.product_references == ["iphone-15-base-128"]
    assert "IPHONE 15" in decision.reply.upper()
    assert "2.820,00" in decision.reply
    assert "Não localizei opção disponível para iPhone 14 (modelo base)" in decision.reply
    assert "IPHONE 14 PRO" not in decision.reply.upper()
    assert "IPHONE 14 PRO MAX" not in decision.reply.upper()
    assert "lista completa" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_batched_bare_iphone_13_or_14_availability_returns_all_requested_models(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "batched-bare-iphone-13-or-14.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-13-midnight-128",
            name="IPHONE 13",
            category="Celular",
            capacity="128GB",
            color="MEIA NOITE",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=1900,
            source="mercado_phone",
            search_text="iphone 13 meia noite 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-14-blue-128",
            name="IPHONE 14",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2400,
            source="mercado_phone",
            search_text="iphone 14 azul 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-14-purple-256",
            name="IPHONE 14",
            category="Celular",
            capacity="256GB",
            color="ROXO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2700,
            source="mercado_phone",
            search_text="iphone 14 roxo 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Tô querendo comprar mais um celular um 13 ou 14\n"
        "Tem algum disponível\n"
        "?"
    )

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-13-midnight-128",
        "iphone-14-blue-128",
        "iphone-14-purple-256",
    }
    assert "IPHONE 13" in decision.reply.upper()
    assert decision.reply.upper().count("IPHONE 14") >= 2
    assert "128GB" in decision.reply.upper()
    assert "256GB" in decision.reply.upper()


@pytest.mark.asyncio
async def test_bare_model_capacity_value_question_returns_only_requested_iphone(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.sealed_cache.items.insert(
        0,
        _sealed_item("iphone-17-lacrado", "iPhone 17", "256 GB", 5700),
    )

    decision = await agent.respond("Qual o valor do 17 256?")

    assert decision.handoff is False
    assert decision.product_references == ["iphone-17-lacrado"]
    assert "iPhone 17" in decision.reply
    assert "256 GB" in decision.reply
    assert "iPhone 17 Pro" not in decision.reply
    assert "iPhone 17 Pro Max" not in decision.reply
    assert "lista completa" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_explicit_iphone_16_catalog_question_does_not_return_complete_list(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        InventoryItem(
            external_id="iphone-16-preto-128",
            name="iPhone 16",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3750,
            search_text="iphone 16 preto 128gb celular seminovo",
        )
    ]
    agent.cache.sealed_cache.items.insert(
        0,
        _sealed_item("iphone-16-lacrado", "iPhone 16", "128 GB", 5200),
    )

    decision = await agent.respond("Vi um iphone 16 disponível no catálogo")

    assert decision.handoff is False
    assert decision.product_references == ["iphone-16-preto-128", "iphone-16-lacrado"]
    assert "iPhone 16" in decision.reply
    assert "lista completa" not in decision.reply.lower()
    assert "iPhone 13" not in decision.reply
    assert "iPhone 17" not in decision.reply


@pytest.mark.asyncio
async def test_reversed_iphone_pro_16_price_question_returns_only_requested_device(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "iphone-16-pro-reversed-word-order.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-128",
            name="iPhone 15",
            category="Celular",
            capacity="128 GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3200,
            battery_health=90,
            search_text="iphone 15 preto 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-128",
            name="iPhone 16 Pro",
            category="Celular",
            capacity="128 GB",
            color="TITANIO PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4800,
            battery_health=94,
            search_text="iphone 16 pro titanio preto 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-256",
            name="iPhone 16 Pro",
            category="Celular",
            capacity="256 GB",
            color="TITANIO NATURAL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5100,
            battery_health=92,
            search_text="iphone 16 pro titanio natural 256 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-17-128",
            name="iPhone 17",
            category="Celular",
            capacity="128 GB",
            color="AZUL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5600,
            battery_health=96,
            search_text="iphone 17 azul 128 gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    customer_message = "Qual\nValor do iPhone pro 16- 128 gb"

    decision = await agent.respond(customer_message)

    assert decision.handoff is False
    assert decision.product_references == ["iphone-16-pro-128"]
    assert "iPhone 16 Pro" in decision.reply
    assert "128 GB" in decision.reply
    assert "R$ 4.800,00" in decision.reply
    assert "lista completa" not in decision.reply.lower()
    assert "iPhone 15" not in decision.reply
    assert "iPhone 17" not in decision.reply
    assert "256 GB" not in decision.reply


@pytest.mark.asyncio
async def test_cheapest_iphone_question_returns_lowest_available_price(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-3750",
            name="iPhone 16",
            category="Celular",
            capacity="128 GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3750,
            battery_health=90,
            search_text="iphone 16 128 gb preto celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-12-1300",
            name="iPhone 12",
            category="Celular",
            capacity="64 GB",
            color="BRANCO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=1300,
            battery_health=86,
            search_text="iphone 12 64 gb branco celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Quanto tá o iPhone mais barato de vocês")

    assert decision.handoff is False
    assert decision.product_references == ["iphone-12-1300"]
    assert "iPhone 12" in decision.reply
    assert "1.300,00" in decision.reply
    assert "iPhone 16" not in decision.reply
    assert "3.750,00" not in decision.reply


@pytest.mark.asyncio
async def test_cheapest_iphone_question_requires_a_confirmed_price(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-11-no-price",
            name="iPhone 11",
            category="Celular",
            capacity="64 GB",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=None,
            search_text="iphone 11 64 gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Quanto tá o iPhone mais barato de vocês")

    assert decision.handoff is False
    assert decision.product_references == []
    assert "preço confirmado" in decision.reply


@pytest.mark.asyncio
async def test_cheapest_iphone_question_uses_stable_tie_breaking(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-12-preto",
            name="iPhone 12",
            category="Celular",
            capacity="64 GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=1300,
            search_text="iphone 12 64 gb preto celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-12-branco",
            name="iPhone 12",
            category="Celular",
            capacity="64 GB",
            color="BRANCO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=1300,
            search_text="iphone 12 64 gb branco celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Quanto tá o iPhone mais barato de vocês")

    assert decision.handoff is False
    assert decision.product_references == ["iphone-12-branco"]
    assert "BRANCO" in decision.reply
    assert "PRETO" not in decision.reply


@pytest.mark.asyncio
async def test_cheapest_iphone_question_keeps_an_explicit_model_filter(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-3750",
            name="iPhone 16",
            category="Celular",
            capacity="128 GB",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3750,
            search_text="iphone 16 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-12-1300",
            name="iPhone 12",
            category="Celular",
            capacity="64 GB",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=1300,
            search_text="iphone 12 64 gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Quanto tá o iPhone 16 mais barato de vocês")

    assert decision.handoff is False
    assert decision.product_references == ["iphone-16-3750"]
    assert "iPhone 16" in decision.reply
    assert "iPhone 12" not in decision.reply


@pytest.mark.asyncio
async def test_all_iphone_17_line_request_returns_every_generation_17_option(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.sealed_cache.items = [
        _sealed_item("iphone-17e", "iPhone 17e", "256 GB", 4500),
        _sealed_item("iphone-17", "iPhone 17", "256 GB", 5600),
        _sealed_item("iphone-17-air", "iPhone 17 Air", "256 GB", 5800),
        _sealed_item("iphone-17-pro", "iPhone 17 Pro", "256 GB", 6900),
        _sealed_item("iphone-17-pro-max", "iPhone 17 Pro Max", "256 GB", 7900),
    ]

    decision = await agent.respond("Qual valor dos iPhones 17 todos eles pode passar pra mim")

    assert decision.handoff is False
    for model in ("iPhone 17e", "iPhone 17", "iPhone 17 Air", "iPhone 17 Pro", "iPhone 17 Pro Max"):
        assert model in decision.reply
    assert "lista completa de produtos disponíveis" in decision.reply.lower()


@pytest.mark.asyncio
async def test_pronta_entrega_question_after_greeting_lists_only_ready_iphones(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        InventoryItem(
            external_id="iphone-16-ready-seminovo",
            name="iPhone 16",
            category="Celular",
            capacity="128 GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3590,
            battery_health=91,
            search_text="iphone 16 128 gb preto celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-17-pro-max-ready-sealed",
            name="iPhone 17 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="PRETO",
            source="mercado_phone",
            condition="NOVO LACRADO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=7900,
            search_text="iphone 17 pro max 256 gb preto celular novo lacrado",
        ),
        InventoryItem(
            external_id="macbook-ready",
            name="MacBook Air",
            category="Notebook",
            capacity="256 GB",
            source="mercado_phone",
            condition="NOVO LACRADO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=7000,
            search_text="macbook air 256 gb novo lacrado",
        ),
    ]
    agent.cache.last_refresh = time.time()

    decision = await agent.respond(
        "Que iPhones tu tem a pronta entrega?",
        history=[
            {
                "role": "assistant",
                "content": (
                    "Luisa, sua satisfação é nossa prioridade! Ao pensar em atualizar seu "
                    "dispositivo, lembre-se de nossa loja, onde teremos o prazer de ajudá-lo "
                    "a encontrar o próximo modelo ideal. Contamos com condições especiais "
                    "para você. Até logo!"
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "Luisa, seis meses se passaram desde que adquiriu seu dispositivo Apple! "
                    "Estamos aqui para garantir que ele continue funcionando perfeitamente. "
                    "E lembre-se, como cliente fiel, você tem acesso a ofertas exclusivas para "
                    "troca ou upgrade!"
                ),
            },
            {"role": "user", "content": "Oiii"},
            {
                "role": "assistant",
                "content": "Cwb. iphones agradece seu contato. Como podemos ajudar?",
            },
            {"role": "user", "content": "tudo bem?"},
        ],
    )

    assert decision.handoff is False
    assert "lista completa de produtos disponíveis" in decision.reply.lower()
    assert "iPhone 16" in decision.reply
    assert "iPhone 17 Pro Max" in decision.reply
    assert "MacBook Air" not in decision.reply
    assert "iPhone 13 Pro" not in decision.reply
    assert "Novos lacrados por encomenda" not in decision.reply
    assert "Enviamos para Curitiba" not in decision.reply
    assert decision.image_urls == []


@pytest.mark.asyncio
async def test_specific_pronta_entrega_request_excludes_sheet_only_options(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        InventoryItem(
            external_id="iphone-17-pro-max-ready",
            name="iPhone 17 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="PRETO",
            source="mercado_phone",
            condition="NOVO LACRADO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=7900,
            search_text="iphone 17 pro max 256 gb preto celular novo lacrado",
        )
    ]
    agent.cache.last_refresh = time.time()

    decision = await agent.respond("Tem o iPhone 17 Pro Max a pronta entrega?")

    assert decision.handoff is False
    assert decision.product_references == ["iphone-17-pro-max-ready"]
    assert "R$ 7.900,00" in decision.reply
    assert "R$ 7.000,00" not in decision.reply
    assert decision.image_urls == []


@pytest.mark.asyncio
async def test_generic_model_request_lists_seminovo_and_sealed_options(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [_sealed_item("16", "iPhone 16", "128 GB", 4600), *sealed.items]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-seminovo",
            name="iPhone 16",
            category="Celular",
            capacity="128 GB",
            color="AZUL ULTRAMARINO",
            source="mercado_phone",
            condition="seminovo",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3500.0,
            search_text="iphone 16 128 gb azul ultramarino celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("iPhone 16 valores")

    assert decision.handoff is False
    assert set(decision.product_references) == {"iphone-16-seminovo", "16"}
    assert "SEMINOVO" in decision.reply.upper()
    assert "NOVO LACRADO" in decision.reply.upper()
    assert "3.500,00" in decision.reply
    assert "4.600,00" in decision.reply


@pytest.mark.asyncio
async def test_model_information_request_lists_seminovo_and_sealed_options(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [_sealed_item("iphone-15-lacrado", "iPhone 15", "128 GB", 4100)]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-seminovo",
            name="iPhone 15",
            category="Celular",
            capacity="128 GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3500,
            search_text="iphone 15 128 gb preto celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Gostaria de informações sobre o iPhone 15",
        history=[
            {"role": "user", "content": "Boa tarde"},
            {"role": "assistant", "content": "Boa tarde! 😊 Como posso ajudar?"},
        ],
    )

    assert decision.handoff is False
    assert set(decision.product_references) == {"iphone-15-seminovo", "iphone-15-lacrado"}
    assert "SEMINOVO" in decision.reply.upper()
    assert "NOVO LACRADO" in decision.reply.upper()
    assert "3.500,00" in decision.reply
    assert "4.100,00" in decision.reply


@pytest.mark.asyncio
async def test_generic_iphone_models_request_lists_every_iphone_without_other_categories(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [
        _sealed_item("iphone-15-lacrado", "iPhone 15", "128 GB", 4100),
        _sealed_item("iphone-16-lacrado", "iPhone 16", "128 GB", 4700),
        _sealed_item("iphone-16e-lacrado", "iPhone 16e", "128 GB", 4200),
        _sealed_item("macbook-air", "MacBook Air", "512 GB", 8500),
    ]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-13-pro-max-seminovo",
            name="iPhone 13 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="VERDE ALPINO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3160,
            battery_health=90,
            search_text="iphone 13 pro max verde alpino 256 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-14-plus-seminovo",
            name="iPhone 14 Plus",
            category="Celular",
            capacity="128 GB",
            color="AZUL",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2250,
            battery_health=97,
            search_text="iphone 14 plus azul 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-seminovo",
            name="iPhone 15",
            category="Celular",
            capacity="128 GB",
            color="ROSA",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2900,
            battery_health=92,
            search_text="iphone 15 rosa 128 gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Gostaria de ver os modelos de IPhone e preços.\n"
        "O meu colega Diogo Mitsuiki me passou o contato"
    )

    assert decision.handoff is False
    for expected in (
        "IPHONE 13 PRO MAX",
        "IPHONE 14 PLUS",
        "IPHONE 15",
        "IPHONE 16",
        "IPHONE 16E",
    ):
        assert expected in decision.reply.upper()
    assert "MACBOOK" not in decision.reply.upper()


@pytest.mark.asyncio
async def test_generic_availability_followup_does_not_inherit_prior_sealed_offer(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [_sealed_item("iphone-16-lacrado", "iPhone 16", "128 GB", 4700)]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-seminovo",
            name="iPhone 16",
            category="Celular",
            capacity="128 GB",
            color="AZUL ULTRAMARINO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3820,
            search_text="iphone 16 128 gb azul ultramarino celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Eu vi um no Instagram por 3820\nEle está disponível?",
        history=[
            {"role": "user", "content": "16"},
            {
                "role": "assistant",
                "content": (
                    "Encontramos o iPhone 16 novo lacrado, 128 GB, por R$ 4.700. "
                    "Trabalhamos por encomenda, com entrega em 1 semana."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert set(decision.product_references) == {"iphone-16-seminovo", "iphone-16-lacrado"}
    assert "SEMINOVO" in decision.reply.upper()
    assert "NOVO LACRADO" in decision.reply.upper()
    assert "3.820,00" in decision.reply
    assert "4.700,00" in decision.reply


@pytest.mark.asyncio
async def test_bare_model_switch_after_15_plus_lists_16_used_and_sealed_options(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [_sealed_item("iphone-16-lacrado", "iPhone 16", "128 GB", 5200)]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-15-plus-16-followup.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-plus-seminovo",
            name="iPhone 15 Plus",
            category="Celular",
            capacity="128 GB",
            color="AZUL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2770,
            battery_health=87,
            search_text="iphone 15 plus azul 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-seminovo",
            name="iPhone 16",
            category="Celular",
            capacity="128 GB",
            color="AZUL ULTRAMARINO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3800,
            battery_health=90,
            search_text="iphone 16 azul ultramarino 128 gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [
        {"role": "user", "content": "Gostaria de saber se tem o 15 plus?"},
        {
            "role": "assistant",
            "content": (
                "Sim 😊 Encontrei estas opções de IPHONE 15 PLUS disponíveis:\n"
                "• IPHONE 15 PLUS — AZUL — 128GB — SEMINOVO — R$ 2.770,00 | Bat: 87%"
            ),
        },
        {"role": "user", "content": "?"},
        {
            "role": "assistant",
            "content": (
                "Tenho sim 😊 iPhone 15 Plus azul, 128GB, seminovo, por R$ 2.770,00, "
                "com 87% de saúde da bateria. Quer fotos ou saber sobre as formas de pagamento?"
            ),
        },
    ]

    decision = await agent.respond("E o 16?", history=history)

    assert decision.handoff is False
    assert set(decision.product_references) == {"iphone-16-seminovo", "iphone-16-lacrado"}
    assert "iPhone 16" in decision.reply
    assert "SEMINOVO" in decision.reply.upper()
    assert "NOVO LACRADO" in decision.reply.upper()
    assert "3.800,00" in decision.reply
    assert "5.200,00" in decision.reply
    assert "iPhone 15 Plus" not in decision.reply


@pytest.mark.asyncio
async def test_bare_model_switch_does_not_reuse_previous_pro_max_context(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [_sealed_item("iphone-16-lacrado", "iPhone 16", "128 GB", 4600)]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-pro-max-seminovo",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="TITÂNIO NATURAL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4130,
            battery_health=83,
            search_text="iphone 15 pro max titanio natural 256 gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "16 lacrado",
        history=[
            {"role": "user", "content": "Qual é o valor do iPhone 15 pro Max?"},
            {
                "role": "assistant",
                "content": "iPhone 15 Pro Max — Titânio Natural — 256GB — seminovo — R$ 4.130,00.",
            },
            {"role": "user", "content": "novo não tem?"},
            {
                "role": "assistant",
                "content": (
                    "Novo iPhone 15 Pro Max não temos disponível na lista de lacrados "
                    "por encomenda."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-16-lacrado"]
    assert "iPhone 16" in decision.reply
    assert "NOVO LACRADO" in decision.reply.upper()
    assert "iPhone 15 Pro Max" not in decision.reply

    followup_history = [
        {"role": "user", "content": "Qual é o valor do iPhone 15 pro Max?"},
        {
            "role": "assistant",
            "content": "iPhone 15 Pro Max — Titânio Natural — 256GB — seminovo — R$ 4.130,00.",
        },
        {"role": "user", "content": "novo não tem?"},
        {
            "role": "assistant",
            "content": (
                "Novo iPhone 15 Pro Max não temos disponível na lista de lacrados "
                "por encomenda."
            ),
        },
        {"role": "user", "content": "16 lacrado"},
        {"role": "assistant", "content": decision.reply},
    ]
    color_followup = await agent.respond("tem no branco?", history=followup_history)

    assert color_followup.product_references == ["iphone-16-lacrado"]
    assert "iPhone 16" in color_followup.reply
    assert "iPhone 15 Pro Max" not in color_followup.reply


@pytest.mark.asyncio
async def test_generic_pro_max_seminovo_followup_lists_requested_variant_options(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [
        _sealed_item("iphone-15-lacrado", "iPhone 15", "128 GB", 4400),
    ]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-pro-max-followup.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-seminovo",
            name="iPhone 15",
            category="Celular",
            capacity="256GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2860,
            search_text="iphone 15 preto 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-max-seminovo",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITANIO NATURAL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4130,
            search_text="iphone 15 pro max titanio natural 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-seminovo",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5200,
            search_text="iphone 16 pro max preto 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-seminovo",
            name="iPhone 15 Pro",
            category="Celular",
            capacity="256GB",
            color="TITANIO AZUL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3700,
            search_text="iphone 15 pro titanio azul 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    first = await agent.respond("Qual seria o valor do 15 novo")
    decision = await agent.respond(
        "E de pro max vcs tem quais? Seminovo",
        history=[
            {"role": "user", "content": "Boa tarde"},
            {
                "role": "assistant",
                "content": "Cwb.iphones agradece seu contato. Como podemos ajudar?",
            },
            {"role": "user", "content": "Qual seria o valor do 15 novo"},
            {"role": "assistant", "content": first.reply},
        ],
    )

    assert decision.handoff is False
    assert "iPhone 15 Pro Max" in decision.reply
    assert "iPhone 16 Pro Max" in decision.reply
    assert "iPhone 15 —" not in decision.reply
    assert "iPhone 15 Pro —" not in decision.reply


@pytest.mark.asyncio
async def test_bare_model_after_generic_intro_lists_both_conditions(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [_sealed_item("iphone-16-lacrado", "iPhone 16", "128 GB", 4700)]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-seminovo",
            name="iPhone 16",
            category="Celular",
            capacity="128 GB",
            color="AZUL ULTRAMARINO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3820,
            search_text="iphone 16 128 gb azul ultramarino celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "16",
        history=[
            {
                "role": "assistant",
                "content": (
                    "Olá! 😊 Temos vários iPhones seminovos disponíveis e modelos novos "
                    "lacrados por encomenda. Você procura algum modelo específico?"
                ),
            }
        ],
    )

    assert decision.handoff is False
    assert "SEMINOVO" in decision.reply.upper()
    assert "NOVO LACRADO" in decision.reply.upper()
    assert "3.820,00" in decision.reply
    assert "4.700,00" in decision.reply


@pytest.mark.asyncio
async def test_queria_ver_iphone_16_lists_used_and_sealed_options(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [_sealed_item("iphone-16-lacrado", "iPhone 16", "128 GB", 5200)]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-seminovo",
            name="iPhone 16",
            category="Celular",
            capacity="128 GB",
            color="AZUL ULTRAMARINO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3800,
            battery_health=90,
            search_text="iphone 16 azul ultramarino 128 gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Queria ver o iPhone 16",
        history=[
            {"role": "user", "content": "Olá"},
            {"role": "user", "content": "Boa noite"},
            {"role": "assistant", "content": "Olá! Boa noite 😊 Como posso te ajudar?"},
        ],
    )

    assert decision.handoff is False
    assert decision.confidence == "high"
    assert set(decision.product_references) == {"iphone-16-seminovo", "iphone-16-lacrado"}
    assert "SEMINOVO" in decision.reply.upper()
    assert "NOVO LACRADO" in decision.reply.upper()
    assert "3.800,00" in decision.reply
    assert "5.200,00" in decision.reply


@pytest.mark.asyncio
async def test_battery_origin_followup_reuses_iphone_15_from_previous_list(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [_sealed_item("iphone-15-lacrado", "iPhone 15", "128 GB", 4000)]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-preto-128",
            name="iPhone 15",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2920,
            battery_health=83,
            search_text="iphone 15 128gb preto celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16e-preto-128",
            name="iPhone 16e",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2660,
            battery_health=92,
            search_text="iphone 16e 128gb preto celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "A bateria do 15 é original?",
        history=[
            {"role": "user", "content": "Quais vc tem no preto?"},
            {
                "role": "assistant",
                "content": (
                    "No preto, temos estas opções: "
                    "iPhone 15 128GB — seminovo — R$ 2.920 — bateria 83%; "
                    "iPhone 15 128GB — novo lacrado — R$ 4.000 (por encomenda); "
                    "iPhone 16e 128GB — seminovo — R$ 2.660 — bateria 92%; "
                    "iPhone 16 128GB — novo lacrado — R$ 4.600 (por encomenda); "
                    "iPhone 17 256GB — novo lacrado — R$ 5.500 (por encomenda); "
                    "iPhone 17e 256GB — novo lacrado — R$ 4.500 (por encomenda)."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-15-preto-128"]
    assert "83%" in decision.reply
    assert "original" in decision.reply.lower()
    assert "não localizei" not in decision.reply.lower()
    assert "iPhone 16e" not in decision.reply


def test_availability_lines_identify_each_model_when_candidates_differ():
    reply = _format_product_availability(
        [
            _sealed_item("17-pro-max", "iPhone 17 Pro Max", "256 GB", 7800),
            _sealed_item("17-pro", "iPhone 17 Pro", "512 GB", 8000),
        ]
    )

    bullet_lines = [line for line in reply.splitlines() if "\u2014" in line]
    assert any("iPhone 17 Pro Max" in line for line in bullet_lines)
    assert any("iPhone 17 Pro" in line and "Max" not in line for line in bullet_lines)


def test_price_table_request_is_treated_as_complete_available_list():
    assert _is_available_list_request("Tem uma tabela de pre\u00e7o dos iPhone") is True


def test_plural_cheapest_iphone_request_is_not_treated_as_single_item():
    assert _is_cheapest_catalog_request("Qual o iPhone mais em conta?") is True
    assert _is_cheapest_catalog_request("Quais iPhones mais em conta vocês têm?") is False


def test_availability_header_does_not_name_only_first_model_when_candidates_differ():
    reply = _format_product_availability(
        [
            _sealed_item("16", "iPhone 16", "128 GB", 4600),
            _sealed_item("15", "iPhone 15", "128 GB", 4000),
        ]
    )

    first_line = _normalize(reply.splitlines()[0])
    assert first_line.endswith("opcoes de iphone disponiveis:")
    assert "opcoes de iphone 16 disponiveis" not in first_line



@pytest.mark.asyncio
async def test_budgeted_multi_device_request_lists_all_matching_options_without_handoff(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-rosa",
            name="iPhone 15",
            category="Celular",
            capacity="128GB",
            color="ROSA",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            price_brl=2650,
            search_text="iphone 15 rosa 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-branco",
            name="iPhone 15 Pro",
            category="Celular",
            capacity="128GB",
            color="TITANIO BRANCO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            price_brl=3410,
            search_text="iphone 15 pro titanio branco 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-ultramarino",
            name="iPhone 16",
            category="Celular",
            capacity="128GB",
            color="ULTRAMARINO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            price_brl=3660,
            search_text="iphone 16 ultramarino 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-verde-256",
            name="iPhone 16",
            category="Celular",
            capacity="256GB",
            color="VERDE",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            price_brl=3820,
            search_text="iphone 16 verde 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-max",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITANIO NATURAL",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            price_brl=4190,
            search_text="iphone 15 pro max titanio natural 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Gostaria de ver iphones na faixa de 4mil para comprar e retirar amanha, preciso de 2 aparelhos"
    )

    assert decision.handoff is False
    for expected in ("iPhone 15", "iPhone 15 Pro", "iPhone 16"):
        assert expected in decision.reply
    assert "R$ 3.820,00" in decision.reply
    assert "iPhone 15 Pro Max" not in decision.reply
    assert "2 aparelhos" in decision.reply


@pytest.mark.asyncio
async def test_generic_cellphone_budget_returns_every_available_option_under_1000(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        InventoryItem(
            external_id="iphone-11-128-white",
            name="iPhone 11",
            category="Celular",
            capacity="128GB",
            color="BRANCO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=800,
            battery_health=73,
            search_text="iphone 11 128gb branco seminovo celular disponivel para venda",
        ),
        InventoryItem(
            external_id="iphone-12-64-white",
            name="iPhone 12",
            category="Celular",
            capacity="64GB",
            color="BRANCO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=990,
            battery_health=77,
            search_text="iphone 12 64gb branco seminovo celular disponivel para venda",
        ),
        InventoryItem(
            external_id="iphone-13-128-black",
            name="iPhone 13",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1200,
            battery_health=80,
            search_text="iphone 13 128gb preto seminovo celular disponivel para venda",
        ),
    ]
    agent.cache.last_refresh = time.time()

    decision = await agent.respond(
        "Olá, gostaria de saber quais celulares vc tem na faixa de 1.000 reais"
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-11-128-white", "iphone-12-64-white"]
    assert "iPhone 11" in decision.reply
    assert "R$ 800,00" in decision.reply
    assert "iPhone 12" in decision.reply
    assert "R$ 990,00" in decision.reply
    assert "iPhone 13" not in decision.reply


@pytest.mark.asyncio
async def test_comma_thousands_budget_keeps_iphone_15_within_r3200(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        InventoryItem(
            external_id="iphone-14-base-128",
            name="iPhone 14",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2400,
            battery_health=86,
            search_text="iphone 14 azul 128gb seminovo celular disponivel para venda",
        ),
        InventoryItem(
            external_id="iphone-15-base-128",
            name="iPhone 15",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2820,
            battery_health=88,
            search_text="iphone 15 azul 128gb seminovo celular disponivel para venda",
        ),
        InventoryItem(
            external_id="iphone-15-base-256-over-budget",
            name="iPhone 15",
            category="Celular",
            capacity="256GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3360,
            battery_health=89,
            search_text="iphone 15 preto 256gb seminovo celular disponivel para venda",
        ),
        InventoryItem(
            external_id="iphone-15-pro-128",
            name="iPhone 15 Pro",
            category="Celular",
            capacity="128GB",
            color="TITÂNIO PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3530,
            battery_health=87,
            search_text="iphone 15 pro titanio preto 128gb seminovo celular disponivel para venda",
        ),
    ]
    agent.cache.last_refresh = time.time()
    request = "Quais iPhones vocês tem disponíveis de até uns 3,200? Por favor"

    decision = await agent.respond(
        "O 15 tem algum?",
        history=[{"role": "user", "content": request}],
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-15-base-128"], decision.reply
    assert _extract_budget_limit(request) == 3200
    assert _extract_budget_limit("iPhone 15 até R$ 3,20") == 3.2
    assert "R$ 2.820,00" in decision.reply
    assert "R$ 3.360,00" not in decision.reply
    assert "R$ 3.530,00" not in decision.reply


@pytest.mark.asyncio
async def test_latest_customer_budget_applies_after_ceiling_revision(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        InventoryItem(
            external_id="iphone-15-base-128-under-3200",
            name="iPhone 15",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2820,
            battery_health=88,
            search_text="iphone 15 azul 128gb seminovo celular disponivel para venda",
        ),
        InventoryItem(
            external_id="iphone-15-base-256-under-4000",
            name="iPhone 15",
            category="Celular",
            capacity="256GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3680,
            battery_health=89,
            search_text="iphone 15 preto 256gb seminovo celular disponivel para venda",
        ),
        InventoryItem(
            external_id="iphone-15-base-512-over-4000",
            name="iPhone 15",
            category="Celular",
            capacity="512GB",
            color="ROSA",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=4250,
            battery_health=90,
            search_text="iphone 15 rosa 512gb seminovo celular disponivel para venda",
        ),
    ]
    agent.cache.last_refresh = time.time()
    history = [
        {
            "role": "user",
            "content": "Quais iPhones vocês têm até uns 3,200?",
        },
        {
            "role": "assistant",
            "content": "Posso procurar dentro desse valor.",
        },
        {
            "role": "user",
            "content": "Na verdade, pode considerar até uns 4,000.",
        },
    ]

    decision = await agent.respond("O 15 tem algum?", history=history)

    assert decision.handoff is False
    assert decision.product_references == [
        "iphone-15-base-128-under-3200",
        "iphone-15-base-256-under-4000",
    ], decision.reply
    assert "R$ 3.680,00" in decision.reply
    assert "R$ 4.250,00" not in decision.reply


@pytest.mark.asyncio
async def test_explicit_iphone_pro_max_with_abbreviated_battery_matches_first_request(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-pro-max-95",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITANIO NATURAL",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4190,
            battery_health=95,
            search_text="iphone 15 pro max titanio natural 256gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Vi que voces postaram um 15 pro Max titanio natural bat 95% Esta disponivel?"
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-15-pro-max-95"]
    assert "iPhone 15 Pro Max" in decision.reply
    assert "95%" in decision.reply


@pytest.mark.asyncio
async def test_13_pro_max_availability_with_battery_and_warranty_details_returns_the_unit(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-13-pro-max-battery-availability-regression.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-13-pro-max-128-88",
            name="iPhone 13 Pro Max",
            category="Celular",
            capacity="128 GB",
            color="GRAFITE",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2390,
            battery_health=88,
            source="mercado_phone",
            search_text="iphone 13 pro max grafite 128 gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Vocês têm iPhone 13 Pro Max 128 GB seminovo? Procuro um com bateria acima de 85%, "
        "sem peças não originais, Face ID funcionando, nota fiscal e garantia. "
        "Qual está disponível hoje e qual a saúde da bateria?"
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-13-pro-max-128-88"]
    assert "iPhone 13 Pro Max" in decision.reply
    assert "128 GB" in decision.reply
    assert "R$ 2.390,00" in decision.reply
    assert "88%" in decision.reply
    assert "iPhone 85" not in decision.reply
    assert "Não localizei" not in decision.reply


@pytest.mark.asyncio
async def test_explicit_ipad_does_not_return_iphone_or_macbook(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Voce tem algum iPad que esteja bem em conta?")

    assert decision.product_references == ["ipad-air-128"]
    assert "IPAD AIR" in decision.reply.upper()
    assert "IPHONE" not in decision.reply.upper()
    assert "MACBOOK" not in decision.reply.upper()


@pytest.mark.asyncio
async def test_ipad_price_alternatives_return_each_requested_model(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.sealed_cache.items = [
        _sealed_item("ipad-11-128", "iPad 11 A16", "128 GB", 3300),
        _sealed_item("ipad-air-128", "iPad Air M3", "128 GB", 4100),
        _sealed_item("ipad-pro-11-256", "iPad Pro 11", "256 GB", 5900),
    ]
    history = [
        {
            "role": "user",
            "content": "Boa tarde tudo bem? Gostaria de saber preço de modelos de ipad",
        },
        {
            "role": "assistant",
            "content": "Cwb.iphones agradece seu contato. Como podemos ajudar?",
        },
    ]

    assert not _is_product_availability_request("O ipad 11 ou ipad air")
    decision = await agent.respond("O ipad 11 ou ipad air", history=history)

    assert decision.handoff is False
    assert set(decision.product_references) == {"ipad-11-128", "ipad-air-128"}
    assert "iPad 11 A16" in decision.reply
    assert "iPad Air M3" in decision.reply
    assert "iPad Pro 11" not in decision.reply
    assert decision.image_urls == []


@pytest.mark.asyncio
async def test_ipad_price_alternatives_name_a_model_missing_from_catalog(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.sealed_cache.items = [
        _sealed_item("ipad-11-128", "iPad 11 A16", "128 GB", 3300),
    ]
    history = [
        {
            "role": "user",
            "content": "Boa tarde tudo bem? Gostaria de saber preço de modelos de ipad",
        },
        {
            "role": "assistant",
            "content": "Cwb.iphones agradece seu contato. Como podemos ajudar?",
        },
    ]

    decision = await agent.respond("O ipad 11 ou ipad air", history=history)

    assert decision.handoff is False
    assert decision.product_references == ["ipad-11-128"]
    assert "iPad 11 A16" in decision.reply
    assert "Não localizei opção disponível para iPad Air" in decision.reply


@pytest.mark.asyncio
async def test_ipad_used_followup_keeps_both_requested_alternatives(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        InventoryItem(
            external_id="ipad-air-used",
            name="iPad Air 5",
            category="Celular",
            capacity="64 GB",
            price_brl=2100,
            source="mercado_phone",
            condition="seminovo",
            availability="Disponível",
            search_text="iPad Air 5 64 GB seminovo disponível",
        ),
        InventoryItem(
            external_id="ipad-11-used",
            name="iPad 11",
            category="Celular",
            capacity="128 GB",
            price_brl=2800,
            source="mercado_phone",
            condition="seminovo",
            availability="Disponível",
            search_text="iPad 11 128 GB seminovo disponível",
        ),
    ]
    history = [
        {
            "role": "user",
            "content": "Boa tarde tudo bem? Gostaria de saber preço de modelos de ipad",
        },
        {
            "role": "assistant",
            "content": "Cwb.iphones agradece seu contato. Como podemos ajudar?",
        },
        {"role": "user", "content": "O ipad 11 ou ipad air"},
        {
            "role": "assistant",
            "content": (
                "Sim 😊 Encontrei estas opções de iPad 11 disponíveis: "
                "Novos lacrados por encomenda: iPad 11 — 128 GB — R$ 3.300,00"
            ),
        },
    ]

    decision = await agent.respond("Tem usado?", history=history)

    assert decision.handoff is False
    assert set(decision.product_references) == {"ipad-air-used", "ipad-11-used"}
    assert "iPad Air 5" in decision.reply
    assert "iPad 11" in decision.reply


@pytest.mark.asyncio
async def test_ipad_11_price_followups_ignore_unrelated_old_photo_and_condition_history(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)

    class IpadCatalog(SealedCatalog):
        def __init__(self):
            self.items = [_sealed_item("ipad-11-128", "iPad 11", "128 GB", 3200)]

    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=IpadCatalog(),
    )
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [
        {"role": "user", "content": "Pode mandar a foto do iPhone 15 Pro Max seminovo?"},
        {"role": "assistant", "content": "Claro, seguem as fotos do iPhone 15 Pro Max seminovo."},
    ]

    family_offer = await agent.respond(
        "Olá tudo bem? vocês teriam ipad no precinho", history=history
    )
    history.extend(
        [
            {"role": "user", "content": "Olá tudo bem? vocês teriam ipad no precinho"},
            {"role": "assistant", "content": family_offer.reply},
        ]
    )
    assert "iPad 11" in family_offer.reply
    assert "3.200,00" in family_offer.reply

    selected_ipad = await agent.respond("ipad 11 128gb", history=history)
    assert selected_ipad.handoff is False
    assert selected_ipad.product_references == ["ipad-11-128"]
    assert selected_ipad.image_urls == []
    assert "3.200,00" in selected_ipad.reply
    assert "não temos fotos" not in selected_ipad.reply.lower()
    history.extend(
        [
            {"role": "user", "content": "ipad 11 128gb"},
            {"role": "assistant", "content": selected_ipad.reply},
        ]
    )

    price_followup = await agent.respond("Qual o valor que esta?", history=history)
    assert price_followup.handoff is False
    assert price_followup.product_references == ["ipad-11-128"]
    assert "3.200,00" in price_followup.reply
    assert "não localizei" not in price_followup.reply.lower()
    history.extend(
        [
            {"role": "user", "content": "Qual o valor que esta?"},
            {"role": "assistant", "content": price_followup.reply},
        ]
    )

    condition_followup = await agent.respond(
        "Tem previsão de vir seminovo ou novo?", history=history
    )
    assert condition_followup.handoff is False
    assert condition_followup.product_references == ["ipad-11-128"]
    assert "iPad 11" in condition_followup.reply
    assert "3.200,00" in condition_followup.reply
    assert "não localizei esse modelo novo/lacrado" not in condition_followup.reply.lower()


@pytest.mark.asyncio
async def test_mac_or_ipad_availability_question_lists_both_requested_families(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Oi, vocês tem mac ou iPad?")

    assert decision.handoff is False
    assert "iPad Air" in decision.reply
    assert "MacBook Neo 2026 13" in decision.reply
    assert "MacBook Air" in decision.reply


@pytest.mark.asyncio
async def test_availability_query_with_two_models_keeps_available_second_model(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-12-128",
            name="IPHONE 12",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2200,
            source="mercado_phone",
            search_text="iphone 12 preto 128gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("iphone 11 ou 12 tem disponivel 128gb?")

    assert decision.handoff is False
    assert decision.product_references == ["iphone-12-128"]
    assert "IPHONE 12" in decision.reply.upper()
    assert "128GB" in decision.reply.upper()


@pytest.mark.asyncio
async def test_searching_for_iphone_13_or_14_returns_available_14_at_requested_capacity(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-iphone-13-or-14-128.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-14-128",
            name="iPhone 14",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1490,
            battery_health=85,
            search_text="iphone 14 azul 128gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    text = "Por favor estou procurando um iPhone 13 o 14 128 GB"

    decision = await agent.respond(text)

    assert _requested_iphone_model_keys(text) == ((13, ""), (14, ""))
    assert decision.handoff is False
    assert decision.product_references == ["iphone-14-128"]
    assert "IPHONE 14" in decision.reply.upper()
    assert "128GB" in decision.reply.upper()
    assert "No momento, não localizei esse produto seminovo" not in decision.reply


@pytest.mark.asyncio
async def test_normal_iphone_and_shared_pro_max_requests_keep_all_three_models(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "iphone-16-normal-and-shared-pro-max.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-normal-128",
            name="iPhone 16",
            category="Celular",
            capacity="128 GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3750,
            battery_health=90,
            source="mercado_phone",
            search_text="iphone 16 preto 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-256",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="TITÂNIO PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=6200,
            battery_health=88,
            source="mercado_phone",
            search_text="iphone 16 pro max titanio preto 256 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-max-256",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="TITÂNIO NATURAL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=5400,
            battery_health=87,
            source="mercado_phone",
            search_text="iphone 15 pro max titanio natural 256 gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    text = (
        "Boa noite! Tudo certo? Gostaria de ver quanto está o iPhone 16 normal "
        "e o Pró Max. O 15 Pro Max também."
    )

    decision = await agent.respond(text)

    assert _requested_iphone_model_keys(text) == (
        (16, ""),
        (16, "pro max"),
        (15, "pro max"),
    )
    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-16-normal-128",
        "iphone-16-pro-max-256",
        "iphone-15-pro-max-256",
    }
    assert "iPhone 16" in decision.reply
    assert "iPhone 16 Pro Max" in decision.reply
    assert "iPhone 15 Pro Max" in decision.reply
    assert "R$ 3.750,00" in decision.reply
    assert "R$ 6.200,00" in decision.reply
    assert "R$ 5.400,00" in decision.reply
    assert _requested_iphone_model_keys("Queria saber do iPhone 16 normal") == ((16, ""),)


@pytest.mark.asyncio
async def test_catalog_price_recall_returns_available_iphone_instead_of_evaluation_form(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-12-blue-128",
            name="IPHONE 12",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=1210,
            battery_health=80,
            search_text="iphone 12 azul 128gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Minha irmã estava vendo um celular contigo, não lembro o número, era 1200",
        history=[
            {"role": "user", "content": "Boa tarde! Estão abertos?"},
            {
                "role": "assistant",
                "content": "Boa tarde! Funcionamos de segunda a sexta, das 9h às 18h.",
            },
            {"role": "user", "content": "Beleza, obrigada!"},
            {"role": "assistant", "content": "Por nada! 😊"},
            {
                "role": "assistant",
                "content": "Bom dia, tudo bem? Teria interesse em algum produto específico?",
            },
        ],
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-12-blue-128"]
    assert "IPHONE 12" in decision.reply.upper()
    assert "1.210,00" in decision.reply
    assert "lista de avaliacao" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_catalog_purchase_advice_about_xr_does_not_send_trade_in_form(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-xr-branco-64",
            name="IPHONE XR",
            category="Celular",
            capacity="64GB",
            color="BRANCO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=400,
            battery_health=74,
            source="mercado_phone",
            search_text="iphone xr branco 64gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [
        {"role": "user", "content": "Tem o xr"},
        {
            "role": "assistant",
            "content": (
                "Temos sim 😊 iPhone XR branco, 64GB, seminovo, com 74% de saúde "
                "da bateria, por R$ 400,00. Temos 1 aparelho disponível."
            ),
        },
    ]
    text = "Compensa pega ele em 2026"

    decision = await agent.respond(text, history=history)

    assert is_trade_in_request(text) is False
    assert is_trade_in_context_request(text, history) is False
    assert decision.handoff is True
    assert decision.reply == CATALOG_BUYER_DETAILS_REPLY
    assert "lista de avaliacao" not in decision.reply.lower()
    assert is_trade_in_request("Compensa pegar meu iPhone na troca?") is True


@pytest.mark.asyncio
async def test_batched_availability_query_keeps_all_three_requested_models(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id=f"iphone-{number}-128",
            name=f"IPHONE {number}",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2000 + number * 10,
            source="mercado_phone",
            search_text=f"iphone {number} preto 128gb celular seminovo",
        )
        for number in (13, 14, 15)
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Gostaria de saber se tem iphone 13/14 disponível\nOu o iphone 15"
    )

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-13-128",
        "iphone-14-128",
        "iphone-15-128",
    }
    for expected in ("IPHONE 13", "IPHONE 14", "IPHONE 15"):
        assert expected in decision.reply.upper()


@pytest.mark.asyncio
async def test_literal_iphone_14_to_15_conversation_keeps_both_models_and_15_conditions(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        InventoryItem(
            external_id="iphone-14-256-roxo",
            name="iPhone 14",
            category="Celular",
            capacity="256GB",
            color="ROXO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2080,
            battery_health=85,
            source="mercado_phone",
            search_text="iphone 14 roxo 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-128-azul",
            name="iPhone 15",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2820,
            battery_health=93,
            source="mercado_phone",
            search_text="iphone 15 azul 128gb celular seminovo",
        ),
    ]
    agent.cache.last_refresh = time.time()
    agent.cache.sealed_cache.items.append(
        _sealed_item("iphone-15-lacrado", "iPhone 15", "128 GB", 4400)
    )

    first_text = "Olá gostaria de valores do iPhone 14 a 15"
    first = await agent.respond(first_text)

    assert first.handoff is False
    assert set(first.product_references) == {
        "iphone-14-256-roxo",
        "iphone-15-128-azul",
        "iphone-15-lacrado",
    }
    assert "IPHONE 14" in first.reply.upper()
    assert "IPHONE 15" in first.reply.upper()
    assert "2.820,00" in first.reply
    assert "4.400,00" in first.reply

    second = await agent.respond(
        "E no iPhone 15",
        history=[
            {"role": "user", "content": first_text},
            {"role": "assistant", "content": first.reply},
        ],
    )

    assert second.handoff is False
    assert set(second.product_references) == {
        "iphone-15-128-azul",
        "iphone-15-lacrado",
    }
    assert "SEMINOVO" in second.reply.upper()
    assert "NOVO LACRADO" in second.reply.upper()
    assert "2.820,00" in second.reply
    assert "4.400,00" in second.reply


@pytest.mark.asyncio
async def test_batched_product_cards_keep_all_requested_models_and_capacities(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-product-cards.json",
    )
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
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "iPhone 14 Pro Max 128GB — Roxo profundo — R$ 3.140 | Bateria 81%\n"
        "iPhone 14 Pro Max 256GB — Preto espacial — R$ 3.590 | Bateria 86%\n"
        "iPhone 15 Plus 128GB — Preto — R$ 2.950 | Bateria 87%"
    )

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-14-pro-max-128-roxo",
        "iphone-14-pro-max-256-preto",
        "iphone-15-plus-128-preto",
    }
    assert decision.reply.count("R$") == 3
    assert "iPhone 14 Pro Max" in decision.reply
    assert "iPhone 15 Plus" in decision.reply


@pytest.mark.asyncio
async def test_exact_question_about_iphone_15_and_15_pro_returns_both_models(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-128",
            name="iPhone 15",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2920,
            battery_health=88,
            source="mercado_phone",
            search_text="iphone 15 preto 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-128",
            name="iPhone 15 Pro",
            category="Celular",
            capacity="128GB",
            color="TITANIO AZUL",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3460,
            battery_health=93,
            source="mercado_phone",
            search_text="iphone 15 pro titanio azul 128gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "oii bom dia, gostaria de saber mais sobre os iPhones 15 e 15 pro, "
        "se são novos ou seminovos"
    )

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-15-128",
        "iphone-15-pro-128",
    }
    assert "iPhone 15" in decision.reply
    assert "iPhone 15 Pro" in decision.reply


@pytest.mark.asyncio
async def test_or_joined_iphone_15_and_15_pro_request_returns_all_matching_stock(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [
        _sealed_item("iphone-15-lacrado-128", "iPhone 15", "128 GB", 4400),
    ]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-or-joined-models.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-256-seminovo",
            name="iPhone 15",
            category="Celular",
            capacity="256GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2860,
            battery_health=90,
            source="mercado_phone",
            search_text="iphone 15 preto 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-256-seminovo",
            name="iPhone 15 Pro",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO AZUL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3700,
            battery_health=92,
            source="mercado_phone",
            search_text="iphone 15 pro titanio azul 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-512-seminovo",
            name="iPhone 15 Pro",
            category="Celular",
            capacity="512GB",
            color="TITÂNIO NATURAL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3990,
            battery_health=89,
            source="mercado_phone",
            search_text="iphone 15 pro titanio natural 512gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Tenho interesse em iphone 15 ou o 15 pro, vcs tem?",
        history=[
            {"role": "user", "content": "olá"},
            {"role": "assistant", "content": "Olá! Tudo bem? 😊 Como posso ajudar?"},
            {"role": "user", "content": "tudo bem?"},
            {"role": "assistant", "content": "Olá! Tudo bem? 😊 Como posso ajudar?"},
        ],
    )

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-15-256-seminovo",
        "iphone-15-pro-256-seminovo",
        "iphone-15-pro-512-seminovo",
        "iphone-15-lacrado-128",
    }
    assert decision.reply.count("R$") == 4
    assert decision.reply.upper().count("IPHONE 15 PRO") == 2
    assert "iPhone 15" in decision.reply


@pytest.mark.asyncio
async def test_iphone_15_plus_or_normal_request_keeps_base_model_in_stock(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "iphone-15-plus-or-normal.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-128-seminovo",
            name="iPhone 15",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2920,
            battery_health=88,
            source="mercado_phone",
            search_text="iphone 15 preto 128gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "eu gostaria de saber se voces tem disponivel algum modelo de iphone 15 plus ou normal"
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-15-128-seminovo"]
    assert "iPhone 15" in decision.reply
    assert "não localizei" not in _normalize(decision.reply)


@pytest.mark.asyncio
async def test_customer_with_model_in_mind_is_asked_before_any_iphone_is_selected(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.sealed_cache.items = [
        _sealed_item("iphone-16-lacrado-128", "iPhone 16", "128 GB", 5200),
        _sealed_item("iphone-17-air-lacrado-256", "iPhone 17 Air", "256 GB", 5800),
    ]
    initial_message = (
        "Olá, boa noite!! Gostaria de saber mais sobre as compras de iPhone, "
        "por favor 😊\nEu tenho um modelo em mente que eu quero comprar lacrado."
    )

    first_decision = await agent.respond(initial_message)

    assert first_decision.handoff is False
    assert first_decision.product_references == []
    assert first_decision.image_urls == []
    assert "qual modelo" in _normalize(first_decision.reply)
    assert "iphone 17 air" not in _normalize(first_decision.reply)
    assert "r$" not in _normalize(first_decision.reply)

    second_decision = await agent.respond(
        "Eu gostaria do iPhone 16 lacrado.",
        history=[
            {"role": "user", "content": initial_message},
            {"role": "assistant", "content": first_decision.reply},
        ],
    )

    assert second_decision.handoff is False
    assert second_decision.product_references == ["iphone-16-lacrado-128"]
    assert "iphone 16" in _normalize(second_decision.reply)
    assert "iphone 17 air" not in _normalize(second_decision.reply)


def _watch_seminovo() -> InventoryItem:
    return InventoryItem(
        external_id="watch-se2-seminovo",
        name="Apple Watch SE 2",
        category="Celular",
        capacity="40MM",
        color="ESTELAR",
        source="mercado_phone",
        condition="SEMINOVO",
        availability="Disponivel para venda",
        quantity=1,
        price_brl=1800,
        search_text="apple watch se 2 40mm estelar celular seminovo",
        photo_urls=["https://photos.example/apple-watch-se2.jpg"],
    )


def _watch_sealed_catalog() -> SealedCatalog:
    catalog = SealedCatalog()
    catalog.items = [
        _sealed_item("watch-series-11", "Apple Watch Series 11", "46MM", 2700),
        _sealed_item("watch-se3", "Apple Watch SE 3", "40MM", 2000),
    ]
    return catalog


def _build_watch_agent(tmp_path, *, seminovos: list[InventoryItem], sealed: SealedCatalog) -> AgentService:
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = seminovos
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


@pytest.mark.asyncio
async def test_ready_stock_followup_after_watch_order_offer_checks_physical_stock(tmp_path):
    ready_watch = InventoryItem(
        external_id="watch-series-11-ready",
        name="Apple Watch Series 11",
        category="Celular",
        capacity="42MM",
        color="OURO ROSA",
        source="mercado_phone",
        condition="NOVO LACRADO",
        availability="Disponível para venda",
        quantity=1,
        price_brl=2600,
        search_text="apple watch series 11 42mm ouro rosa novo lacrado",
    )
    agent = _build_watch_agent(
        tmp_path,
        seminovos=[ready_watch],
        sealed=_watch_sealed_catalog(),
    )

    decision = await agent.respond(
        "Nenhum a pronta entrega?",
        history=[
            {"role": "user", "content": "Ouro Rosa"},
            {
                "role": "assistant",
                "content": (
                    "Ouro-rosa está cadastrado para o Apple Watch Series 11 nos dois tamanhos: "
                    "42 mm por R$ 2.500 ou 46 mm por R$ 2.700. Trabalhamos por encomenda, "
                    "com prazo de 1 semana. Qual tamanho você prefere?"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "Enviamos para Curitiba" not in decision.reply
    assert "Sedex" not in decision.reply
    assert "Apple Watch Series 11" in decision.reply
    assert "disponíveis" in decision.reply
    assert "R$ 2.600,00" in decision.reply
    assert "Apple Watch SE 3" not in decision.reply
    assert decision.product_references == ["watch-series-11-ready"]
    assert decision.image_urls == []


@pytest.mark.asyncio
async def test_real_sedex_question_for_ready_stock_stays_in_delivery_faq(tmp_path):
    agent = _build_watch_agent(
        tmp_path,
        seminovos=[],
        sealed=_watch_sealed_catalog(),
    )

    decision = await agent.respond(
        "Vocês enviam um Apple Watch Series 11 que está a pronta entrega por Sedex?"
    )

    assert decision.handoff is False
    assert "Enviamos para Curitiba" in decision.reply
    assert "Sedex" in decision.reply
    assert decision.product_references == []


@pytest.mark.asyncio
async def test_seminovo_request_does_not_fallback_to_sealed_catalog(tmp_path):
    agent = _build_watch_agent(
        tmp_path,
        seminovos=[],
        sealed=_watch_sealed_catalog(),
    )

    decision = await agent.respond("Voces tem algum Apple Watch semi novo disponivel?")

    assert decision.product_references == []
    assert "NOVO LACRADO" not in decision.reply.upper()


@pytest.mark.asyncio
async def test_seminovo_request_excludes_sealed_matches_when_both_conditions_exist(tmp_path):
    agent = _build_watch_agent(
        tmp_path,
        seminovos=[_watch_seminovo()],
        sealed=_watch_sealed_catalog(),
    )

    decision = await agent.respond("Voces tem algum Apple Watch semi novo disponivel?")

    assert decision.product_references == ["watch-se2-seminovo"]
    assert "APPLE WATCH SE 2" in decision.reply.upper()
    assert "SEMINOVO" in decision.reply.upper()
    assert "NOVO LACRADO" not in decision.reply.upper()

@pytest.mark.asyncio
async def test_multi_category_availability_request_lists_each_requested_category(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)

    class MultiCategoryCatalog(SealedCatalog):
        def __init__(self):
            self.items = [
                _sealed_item("watch-series-11", "Apple Watch Series 11", "46MM", 2700),
                _sealed_item("ipad-11", "iPad 11", "128 GB", 3000),
                _sealed_item("macbook-air", "MacBook Air", "512 GB", 8500),
            ]

    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=MultiCategoryCatalog(),
    )
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Bom dia!!! Vc poderia passar o que vc tem disponivel Apple Watch, iPad e MacBook"
    )

    assert "Apple Watch Series 11" in decision.reply
    assert "iPad 11" in decision.reply
    assert "MacBook Air" in decision.reply

@pytest.mark.asyncio
async def test_compact_plural_pro_max_query_does_not_select_base_model(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)

    class ProMaxCatalog(SealedCatalog):
        def __init__(self):
            self.items = [
                _sealed_item("iphone-16", "iPhone 16", "128 GB", 4600),
                _sealed_item("iphone-16-pro-max", "iPhone 16 Pro Max", "256 GB", 5500),
            ]

    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=ProMaxCatalog(),
    )
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Quais iPhones 16ProMax vocês tem disponíveis e valores"
    )

    assert decision.product_references == ["iphone-16-pro-max"]
    assert "iPhone 16 Pro Max" in decision.reply
    assert "4.600,00" not in decision.reply


@pytest.mark.asyncio
async def test_two_standalone_pro_models_with_capacity_are_both_matched(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-pro-128",
            name="iPhone 16 Pro",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4500,
            search_text="iphone 16 pro preto 128 gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-128",
            name="iPhone 15 Pro",
            category="Celular",
            capacity="128GB",
            color="PRATA",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3410,
            search_text="iphone 15 pro prata 128 gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("O 16 pro e o 15 pro so teria 128gb")

    assert set(decision.product_references) == {
        "iphone-16-pro-128",
        "iphone-15-pro-128",
    }
    assert "iPhone 16 Pro" in decision.reply
    assert "iPhone 15 Pro" in decision.reply
    assert "nao localizei" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_iphone_16_pro_capacity_and_iphone_17_tambem_returns_both_models(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "iphone-16-pro-256-and-17.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-pro-256",
            name="iPhone 16 Pro",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO DESERTO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=4560,
            battery_health=90,
            search_text="iphone 16 pro titanio deserto 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-17-256",
            name="iPhone 17",
            category="Celular",
            capacity="256GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=5200,
            battery_health=96,
            search_text="iphone 17 preto 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    text = "Gostaria de ver os modelos disponíveis de iPhone 16 pro 256 gb e 17 também"
    assert _requested_iphone_model_keys(text) == ((16, "pro"), (17, ""))

    decision = await agent.respond(text)

    assert decision.handoff is False
    assert set(decision.product_references) == {"iphone-16-pro-256", "iphone-17-256"}
    assert "iPhone 16 Pro" in decision.reply
    assert "iPhone 17" in decision.reply


@pytest.mark.asyncio
async def test_capacity_followup_ignores_previous_negative_catalog_answer(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-15-pro-256-followup.json",
        sealed_cache=None,
    )
    cache.items = [
        InventoryItem(
            external_id="10840852",
            name="IPHONE 15 PRO",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO AZUL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3700,
            battery_health=92,
            search_text="iphone 15 pro titanio azul 256gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Tb tem de 256gb",
        history=[
            {"role": "user", "content": "Boa tarde"},
            {
                "role": "assistant",
                "content": "Cwb.iphones agradece seu contato. Como podemos ajudar?",
            },
            {
                "role": "user",
                "content": "Estou procurando 14 pro max ou o 15 pro max",
            },
            {"role": "user", "content": "256gb"},
            {
                "role": "assistant",
                "content": (
                    "Boa tarde! No momento, tenho estas opções disponíveis de 256GB:\n"
                    "📱 iPhone 15 Pro Max seminovo — Titânio Azul\n"
                    "• Bateria 86% — R$ 4.130\n"
                    "• Bateria 87% — R$ 4.070\n"
                    "Não encontrei o iPhone 14 Pro Max 256GB disponível no catálogo."
                ),
            },
            {"role": "user", "content": "Em 18 fica quanto o 87%"},
            {
                "role": "assistant",
                "content": (
                    "Parcelamento do iPhone 15 Pro Max 256GB, bateria 87% — "
                    "R$ 4.070 à vista: 18x de R$ 279,84."
                ),
            },
            {"role": "user", "content": "E o q 15 pro ?"},
            {
                "role": "assistant",
                "content": (
                    "Você quer o iPhone 15 Pro de 256GB também? Não encontrei essa "
                    "opção disponível no catálogo no momento. Posso verificar outras "
                    "capacidades, se quiser."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert decision.product_references == ["10840852"]
    assert "IPHONE 15 PRO" in decision.reply
    assert "256GB" in decision.reply
    assert "R$ 3.700,00" in decision.reply
    assert "não localizei" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_two_pro_max_alternatives_are_both_returned(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-pro-max-256",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO NATURAL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4130,
            search_text="iphone 15 pro max titanio natural 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-256",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5200,
            search_text="iphone 16 pro max preto 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Teria iphone 15 pro max ou 16 pro max ?",
        history=[
            {"role": "user", "content": "Ola boa tarde"},
            {"role": "assistant", "content": "Olá, boa tarde! Como posso ajudar? 😊"},
        ],
    )

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-15-pro-max-256",
        "iphone-16-pro-max-256",
    }
    assert "iPhone 15 Pro Max" in decision.reply
    assert "iPhone 16 Pro Max" in decision.reply


@pytest.mark.asyncio
async def test_exact_two_pro_max_color_request_returns_available_used_units(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-exact-14-15-pro-max.json",
    )
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
            search_text="iphone 14 pro max roxo profundo 128gb celular seminovo",
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
            search_text="iphone 14 pro max preto espacial 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-max-256-titanio",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO NATURAL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=4130,
            battery_health=83,
            search_text="iphone 15 pro max titanio natural 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Gostaria de saber quais cores do iPhone 14 Pro Max e do 15 pro Max "
        "tem disponível para encomenda"
    )
    normalized = _normalize(decision.reply)

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-14-pro-max-128-roxo",
        "iphone-14-pro-max-256-preto",
        "iphone-15-pro-max-256-titanio",
    }
    assert "não localizei" not in normalized
    assert "novo lacrado" not in normalized
    assert "SEMINOVO" in decision.reply
    for color in ("ROXO PROFUNDO", "PRETO ESPACIAL", "TITÂNIO NATURAL"):
        assert color in decision.reply
    assert "iPhone 14 Pro Max" in decision.reply
    assert "iPhone 15 Pro Max" in decision.reply


@pytest.mark.asyncio
async def test_line_separated_pro_max_prices_return_only_requested_models(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-11-pro-max-256",
            name="iPhone 11 Pro Max",
            category="Celular",
            capacity="256GB",
            color="VERDE MEIA NOITE",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=1000,
            search_text="iphone 11 pro max verde meia noite 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-12-64",
            name="iPhone 12",
            category="Celular",
            capacity="64GB",
            color="BRANCO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=1360,
            search_text="iphone 12 branco 64gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-max-256",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO NATURAL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4130,
            search_text="iphone 15 pro max titanio natural 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-256",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5200,
            search_text="iphone 16 pro max preto 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Bom tarde, td bem?\n\n"
        "Gostaria de saber, valores dos iphones disponíveis, para essa semana no:\n\n"
        "15 pro max\n16 pro max\n\nPor favor 😊"
    )

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-15-pro-max-256",
        "iphone-16-pro-max-256",
    }
    assert "iPhone 15 Pro Max" in decision.reply
    assert "iPhone 16 Pro Max" in decision.reply
    assert "iPhone 11 Pro Max" not in decision.reply
    assert "iPhone 12" not in decision.reply
    assert "lista completa de produtos disponíveis" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_batched_three_pro_max_models_returns_all_units_and_separates_delivery_sources(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [
        _sealed_item("sheet:17-pro-max-256", "iPhone 17 Pro Max", "256 GB", 7900),
        _sealed_item("sheet:17-pro-max-512", "iPhone 17 Pro Max", "512 GB", 8600),
        _sealed_item("sheet:17-pro-max-1tb", "iPhone 17 Pro Max", "1 TB", 10300),
    ]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-three-pro-max.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="mp:15-pro-max-256-ready",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="TITÂNIO NATURAL",
            source="mercado_phone",
            condition="LACRADO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=4200,
            search_text="iphone 15 pro max 256 gb titanio natural celular lacrado",
        ),
        InventoryItem(
            external_id="mp:16-pro-max-256-a",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=5500,
            battery_health=100,
            search_text="iphone 16 pro max 256 gb preto celular seminovo",
        ),
        InventoryItem(
            external_id="mp:16-pro-max-256-b",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="BRANCO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=5300,
            battery_health=90,
            search_text="iphone 16 pro max 256 gb branco celular seminovo",
        ),
        InventoryItem(
            external_id="mp:16-pro-max-512",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="512 GB",
            color="TITÂNIO DESERTO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=5480,
            battery_health=100,
            search_text="iphone 16 pro max 512 gb titanio deserto celular seminovo",
        ),
        InventoryItem(
            external_id="mp:17-pro-max-256-ready",
            name="iPhone 17 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="PRATEADO",
            source="mercado_phone",
            condition="LACRADO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=7460,
            search_text="iphone 17 pro max 256 gb prateado celular lacrado",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Queria saber sobre valores 15 16 17 pro Max\n"
        "E a saúde\n"
        "Já comprei 2 celular com vc"
    )
    assert decision.handoff is False
    assert set(decision.product_references) == {
        "mp:15-pro-max-256-ready",
        "mp:16-pro-max-256-a",
        "mp:16-pro-max-256-b",
        "mp:16-pro-max-512",
        "mp:17-pro-max-256-ready",
        "sheet:17-pro-max-256",
        "sheet:17-pro-max-512",
        "sheet:17-pro-max-1tb",
    }
    assert decision.reply.count("iPhone 16 Pro Max") == 3
    assert decision.reply.count("R$") == 8
    assert "iPhone 15 Pro Max" in decision.reply
    assert "iPhone 17 Pro Max" in decision.reply
    assert "Seminovos disponíveis para pronta entrega" in decision.reply
    assert "Lacrados disponíveis para pronta entrega" in decision.reply
    assert "Novos lacrados por encomenda" in decision.reply
    assert decision.reply.index("Lacrados disponíveis para pronta entrega") < decision.reply.index(
        "Novos lacrados por encomenda"
    )


@pytest.mark.asyncio
async def test_multi_model_pro_max_request_returns_all_units_of_available_model(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-pro-max-512",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="512GB",
            color="TITÂNIO DESERTO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5480,
            battery_health=100,
            search_text="iphone 16 pro max titanio deserto 512gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-256-a",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO DESERTO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5500,
            battery_health=100,
            search_text="iphone 16 pro max titanio deserto 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-256-b",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5290,
            battery_health=90,
            search_text="iphone 16 preto 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Oi teria iPhone 15 ou 16 pro max ?")

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-16-pro-max-512",
        "iphone-16-pro-max-256-a",
        "iphone-16-pro-max-256-b",
    }
    assert "iPhone 15 Pro Max" not in decision.reply
    assert decision.reply.count("R$") == 3


@pytest.mark.asyncio
async def test_single_pro_max_value_question_returns_all_available_units(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-pro-max-512",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="512GB",
            color="TITÂNIO DESERTO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5480,
            battery_health=100,
            search_text="iphone 16 pro max titanio deserto 512gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-256-a",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO DESERTO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5500,
            battery_health=100,
            search_text="iphone 16 pro max titanio deserto 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-256-b",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5290,
            battery_health=90,
            # A valid unit must not disappear only because its searchable
            # description has fewer lexical matches than another unit.
            search_text="iphone 16 pro max preto 256gb celular",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Qual o valor do 16 pro max? Pode ser lacrado e semi novo",
        history=[
            {"role": "user", "content": "Olá, boa tarde"},
            {
                "role": "assistant",
                "content": (
                    "Olá, boa tarde! Claro 😊 Qual modelo de iPhone você procura? "
                    "Se puder, me informe também a capacidade e se prefere seminovo ou novo lacrado."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-16-pro-max-512",
        "iphone-16-pro-max-256-a",
        "iphone-16-pro-max-256-b",
    }
    assert decision.reply.count("256GB") == 2
    assert decision.reply.count("512GB") == 1


@pytest.mark.asyncio
async def test_bare_model_value_question_returns_every_matching_stock_unit(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-bare-model-value.json",
    )
    variants = [
        ("iphone-16-pro-max-128", "iPhone 16 Pro Max", "128GB", "AZUL"),
        ("iphone-16-pro-max-256-a", "iPhone 16 Pro Max 256GB", "256GB", "PRETO"),
        ("iphone-16-pro-max-256-b", "iPhone 16 Pro Max 256GB", "256GB", "BRANCO"),
        ("iphone-16-pro-max-512-a", "iPhone 16 Pro Max 512GB", "512GB", "NATURAL"),
        ("iphone-16-pro-max-512-b", "iPhone 16 Pro Max 512GB", "512GB", "DESERTO"),
        ("iphone-16-pro-max-1tb", "iPhone 16 Pro Max 1TB", "1TB", "PRETO"),
    ]
    cache.items = [
        InventoryItem(
            external_id=external_id,
            name=name,
            category="Celular",
            capacity=capacity,
            color=color,
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=5000 + index * 100,
            search_text=f"{name} {color} celular seminovo",
        )
        for index, (external_id, name, capacity, color) in enumerate(variants)
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Gostaria de saber o valor do seu 16 promax")

    assert set(decision.product_references) == {variant[0] for variant in variants}
    assert decision.reply.count("R$") == 6


@pytest.mark.asyncio
async def test_color_followup_after_256_pro_max_value_question_lists_all_units(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-pro-max-256-a",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO DESERTO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5500,
            battery_health=100,
            search_text="iphone 16 pro max titanio deserto 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-256-b",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5290,
            battery_health=90,
            search_text="iphone 16 pro max preto 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Qual o valor do iPhone 16 pro max de 256g?\nQuais cores teria?",
        history=[
            {"role": "user", "content": "Td bem ?"},
            {"role": "assistant", "content": "Bom dia! Tudo bem? 😊 Como posso ajudar?"},
        ],
    )

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-16-pro-max-256-a",
        "iphone-16-pro-max-256-b",
    }
    assert "TITÂNIO DESERTO" in decision.reply
    assert "PRETO" in decision.reply
    assert decision.reply.count("256GB") == 2

    followup = await agent.respond(
        "Quais cores teria?",
        history=[
            {"role": "user", "content": "Qual o valor do iPhone 16 pro max de 256g?"},
        ],
    )

    assert set(followup.product_references) == {
        "iphone-16-pro-max-256-a",
        "iphone-16-pro-max-256-b",
    }
    assert "TITÂNIO DESERTO" in followup.reply
    assert "PRETO" in followup.reply
    assert followup.reply.count("256GB") == 2

    color_specific = await agent.respond("Qual o valor do iPhone 16 pro max 256GB preto?")

    assert color_specific.product_references == ["iphone-16-pro-max-256-b"]
    assert "PRETO" in color_specific.reply
    assert "TITÂNIO DESERTO" not in color_specific.reply


@pytest.mark.asyncio
async def test_unavailable_requested_color_is_disclosed_before_other_available_colors(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "iphone-14-unavailable-purple.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-14-starlight-128",
            name="IPHONE 14",
            category="Celular",
            capacity="128GB",
            color="ESTELAR",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1870,
            source="mercado_phone",
            search_text="iphone 14 estelar 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-14-midnight-128",
            name="IPHONE 14",
            category="Celular",
            capacity="128GB",
            color="MEIA NOITE",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1850,
            source="mercado_phone",
            search_text="iphone 14 meia noite 128gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [{"role": "user", "content": "Seria o iPhone 14 roxo 128 GB"}]

    unavailable = await agent.respond("Ele está disponível?", history=history)

    assert unavailable.handoff is False
    assert unavailable.product_references == [
        "iphone-14-starlight-128",
        "iphone-14-midnight-128",
    ]
    assert unavailable.reply == (
        "No momento, o iPhone 14 roxo de 128 GB não está disponível. "
        "Encontrei estas opções em outras cores:\n"
        "• IPHONE 14 — ESTELAR — 128GB — SEMINOVO — R$ 1.870,00 | "
        "Bat: não informada no cadastro\n"
        "• IPHONE 14 — MEIA NOITE — 128GB — SEMINOVO — R$ 1.850,00 | "
        "Bat: não informada no cadastro"
    )
    assert not unavailable.reply.lower().startswith("sim")

    available_color_list = await agent.respond(
        "Quais são as cores?",
        history=[
            *history,
            {"role": "assistant", "content": unavailable.reply},
        ],
    )

    assert available_color_list.handoff is False
    assert "roxo de 128 gb não está disponível" not in available_color_list.reply.lower()
    assert "ESTELAR" in available_color_list.reply
    assert "MEIA NOITE" in available_color_list.reply

    cache.items.append(
        InventoryItem(
            external_id="iphone-14-blue-128",
            name="IPHONE 14",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1890,
            source="mercado_phone",
            search_text="iphone 14 azul 128gb celular seminovo",
        )
    )
    image_color_conflict = await agent._try_product_availability(
        "iPhone 14 roxo 128 GB está disponível?",
        image_description="iPhone 14 azul 128GB",
    )

    assert image_color_conflict is not None
    assert image_color_conflict.handoff is False
    assert "roxo" in image_color_conflict.reply.lower()
    assert "não está disponível" in image_color_conflict.reply.lower()
    assert not image_color_conflict.reply.lower().startswith("sim")
    assert set(image_color_conflict.product_references) == {
        "iphone-14-starlight-128",
        "iphone-14-midnight-128",
        "iphone-14-blue-128",
    }

    corrected_color_history = [
        {"role": "user", "content": "Seria o iPhone 14 azul 128 GB"},
        {
            "role": "user",
            "content": "Na verdade, eu queria o iPhone 14 roxo 128 GB, não azul",
        },
    ]

    corrected_unavailable = await agent.respond(
        "Ele está disponível?",
        history=corrected_color_history,
    )

    assert corrected_unavailable.handoff is False
    assert "roxo" in corrected_unavailable.reply.lower()
    assert "não está disponível" in corrected_unavailable.reply.lower()
    assert not corrected_unavailable.reply.lower().startswith("sim")
    assert set(corrected_unavailable.product_references) == {
        "iphone-14-starlight-128",
        "iphone-14-midnight-128",
        "iphone-14-blue-128",
    }
    assert "AZUL" in corrected_unavailable.reply

    corrected_before_negation_history = [
        {"role": "user", "content": "Seria o iPhone 14 azul 128 GB"},
        {
            "role": "user",
            "content": "Na verdade, o iPhone 14 azul não, é roxo 128 GB",
        },
    ]
    corrected_before_negation = await agent.respond(
        "Ele está disponível?",
        history=corrected_before_negation_history,
    )

    assert corrected_before_negation.handoff is False
    assert "roxo" in corrected_before_negation.reply.lower()
    assert "não está disponível" in corrected_before_negation.reply.lower()
    assert not corrected_before_negation.reply.lower().startswith("sim")
    assert set(corrected_before_negation.product_references) == {
        "iphone-14-starlight-128",
        "iphone-14-midnight-128",
        "iphone-14-blue-128",
    }

    corrected_comma_history = [
        {"role": "user", "content": "Seria o iPhone 14 azul 128 GB"},
        {
            "role": "user",
            "content": "Na verdade, o iPhone 14 azul, não, é roxo 128 GB",
        },
    ]
    corrected_comma = await agent.respond(
        "Ele está disponível?",
        history=corrected_comma_history,
    )

    assert corrected_comma.handoff is False
    assert "roxo" in corrected_comma.reply.lower()
    assert "não está disponível" in corrected_comma.reply.lower()
    assert not corrected_comma.reply.lower().startswith("sim")
    assert set(corrected_comma.product_references) == {
        "iphone-14-starlight-128",
        "iphone-14-midnight-128",
        "iphone-14-blue-128",
    }

    both_requested_colors_history = [
        {"role": "user", "content": "Seria o iPhone 14 roxo e azul 128 GB"}
    ]
    partial_colors = await agent.respond(
        "Ele está disponível?",
        history=both_requested_colors_history,
    )

    assert partial_colors.handoff is False
    assert partial_colors.reply.startswith(
        "Não localizei o iPhone 14 na cor roxo de 128 GB."
    )
    assert "outras cores solicitadas" in partial_colors.reply
    assert partial_colors.product_references == ["iphone-14-blue-128"]

    either_or_colors_history = [
        {"role": "user", "content": "Seria o iPhone 14 roxo ou azul 128 GB"}
    ]
    either_or_colors = await agent.respond(
        "Ele está disponível?",
        history=either_or_colors_history,
    )

    assert either_or_colors.handoff is False
    assert either_or_colors.reply.lower().startswith("sim")
    assert either_or_colors.product_references == ["iphone-14-blue-128"]

    cache.items.append(
        InventoryItem(
            external_id="iphone-14-natural-128",
            name="IPHONE 14",
            category="Celular",
            capacity="128GB",
            color="TITÂNIO NATURAL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1910,
            source="mercado_phone",
            search_text="iphone 14 titânio natural 128gb celular seminovo",
        )
    )
    both_available_colors = await agent.respond(
        "Ele está disponível?",
        history=[
            {
                "role": "user",
                "content": "Seria o iPhone 14 titânio natural e azul 128 GB",
            }
        ],
    )

    assert both_available_colors.handoff is False
    assert both_available_colors.reply.lower().startswith("sim")
    assert set(both_available_colors.product_references) == {
        "iphone-14-blue-128",
        "iphone-14-natural-128",
    }
    assert "AZUL" in both_available_colors.reply
    assert "TITÂNIO NATURAL" in both_available_colors.reply

    cache.items.append(
        InventoryItem(
            external_id="iphone-14-purple-128",
            name="IPHONE 14",
            category="Celular",
            capacity="128GB",
            color="ROXO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1940,
            source="mercado_phone",
            search_text="iphone 14 roxo 128gb celular seminovo",
        )
    )

    image_color_available = await agent._try_product_availability(
        "iPhone 14 roxo 128 GB está disponível?",
        image_description="iPhone 14 azul 128GB",
    )

    assert image_color_available is not None
    assert image_color_available.product_references == ["iphone-14-purple-128"]
    assert image_color_available.reply.lower().startswith("sim")
    assert "ROXO" in image_color_available.reply
    assert "AZUL" not in image_color_available.reply

    available = await agent.respond(
        "Ele está disponível?",
        history=corrected_color_history,
    )

    assert available.handoff is False
    assert available.product_references == ["iphone-14-purple-128"]
    assert available.reply.lower().startswith("sim")
    assert "ROXO" in available.reply
    assert "ESTELAR" not in available.reply
    assert "MEIA NOITE" not in available.reply


@pytest.mark.asyncio
async def test_catalog_color_aliases_match_canonical_inventory_labels(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "iphone-14-color-aliases.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-14-white-128",
            name="IPHONE 14",
            category="Celular",
            capacity="128GB",
            color="BRANCO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1870,
            source="mercado_phone",
            search_text="iphone 14 branca 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-14-intense-blue-256",
            name="IPHONE 14",
            category="Celular",
            capacity="256GB",
            color="AZUL-INTENSO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2150,
            source="mercado_phone",
            search_text="iphone 14 azul intenso 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    white = await agent.respond("O iPhone 14 na cor branca 128 GB está disponível?")
    intense_blue = await agent.respond("O iPhone 14 azul intenso 256 GB está disponível?")

    assert white.handoff is False
    assert white.reply.lower().startswith("sim")
    assert white.product_references == ["iphone-14-white-128"]
    assert "BRANCO" in white.reply
    assert intense_blue.handoff is False
    assert intense_blue.reply.lower().startswith("sim")
    assert intense_blue.product_references == ["iphone-14-intense-blue-256"]
    assert "AZUL-INTENSO" in intense_blue.reply


@pytest.mark.asyncio
async def test_broad_unavailable_color_reply_keeps_requested_iphone_family(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "iphone-family-color.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-11-blue-128",
            name="IPHONE 11",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1350,
            source="mercado_phone",
            search_text="iphone 11 azul 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-12-black-128",
            name="IPHONE 12",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1650,
            source="mercado_phone",
            search_text="iphone 12 preto 128gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("O iPhone roxo está disponível?")

    assert decision.handoff is False
    assert decision.reply.startswith(
        "No momento, o iPhone roxo não está disponível."
    )
    assert "iPhone 11 roxo" not in decision.reply
    assert "IPHONE 11" in decision.reply
    assert "IPHONE 12" in decision.reply


def test_unavailable_color_header_uses_family_when_model_is_not_specific():
    header = _unavailable_catalog_color_header(
        selected=[SimpleNamespace(name="IPHONE 14")],
        requested_models=(),
        requested_ipad_models=(),
        requested_families={"iphone"},
        requested_catalog_colors=("roxo",),
        requested_capacities={"128gb"},
    )

    assert header == (
        "No momento, o iPhone roxo de 128 GB não está disponível. "
        "Encontrei estas opções em outras cores:"
    )
    ipad_header = _unavailable_catalog_color_header(
        selected=[SimpleNamespace(name="iPad Air")],
        requested_models=(),
        requested_ipad_models=("air",),
        requested_families={"ipad"},
        requested_catalog_colors=("roxo",),
        requested_capacities={"128gb"},
    )
    apple_watch_header = _unavailable_catalog_color_header(
        selected=[SimpleNamespace(name="Apple Watch")],
        requested_models=(),
        requested_ipad_models=(),
        requested_families={"apple_watch"},
        requested_catalog_colors=("roxo",),
        requested_capacities=set(),
    )

    assert ipad_header.startswith("No momento, o iPad Air roxo")
    assert apple_watch_header.startswith("No momento, o Apple Watch roxo")


def test_availability_negation_keeps_the_color_being_asked_about():
    assert _requested_catalog_colors(
        "Não teria o roxo 128 GB?",
        [SimpleNamespace(color="ROXO")],
    ) == ("roxo",)


@pytest.mark.asyncio
async def test_other_color_followup_explains_only_cataloged_color(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-plus-128-ultramarino",
            name="iPhone 16 Plus",
            category="Celular",
            capacity="128GB",
            color="ULTRAMARINO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3860,
            battery_health=100,
            search_text="iphone 16 plus ultramarino 128gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [
        {"role": "user", "content": "Tem iPhone 16 Plus seminovo de 128GB?"},
        {
            "role": "assistant",
            "content": "No momento, só aparece disponível o iPhone 16 Plus seminovo de 128 GB na cor ultramarino 😊",
        },
        {"role": "user", "content": "Não tem o rosa?"},
        {
            "role": "assistant",
            "content": "No momento, só aparece disponível o iPhone 16 Plus seminovo de 128 GB na cor ultramarino 😊",
        },
        {"role": "user", "content": "Teria outra cor?"},
        {
            "role": "assistant",
            "content": (
                "Sim 😊 Encontrei estas opções de IPHONE 16 PLUS disponíveis:\n"
                "• IPHONE 16 PLUS — ULTRAMARINO — 128GB — SEMINOVO — R$ 3.860,00 | Bat: 100%"
            ),
        },
    ]

    decision = await agent.respond("Outra cor", history=history)

    assert decision.handoff is False
    assert decision.product_references == ["iphone-16-plus-128-ultramarino"]
    assert "outra cor" in _normalize(decision.reply)
    assert "ULTRAMARINO" in decision.reply
    assert "Encontrei estas opções" not in decision.reply

    cache.items.append(
        InventoryItem(
            external_id="iphone-16-plus-128-preto",
            name="iPhone 16 Plus",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3900,
            battery_health=100,
            search_text="iphone 16 plus preto 128gb celular seminovo",
        )
    )
    alternatives = await agent.respond("Teria outra cor?", history=history)

    assert alternatives.handoff is False
    assert set(alternatives.product_references) == {
        "iphone-16-plus-128-ultramarino",
        "iphone-16-plus-128-preto",
    }
    assert "ULTRAMARINO" in alternatives.reply
    assert "PRETO" in alternatives.reply


@pytest.mark.asyncio
async def test_specific_pro_max_price_includes_cheaper_ready_sealed_unit(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [
        _sealed_item("sheet:17-pro-max-256", "iPhone 17 Pro Max", "256 GB", 7900),
        _sealed_item("sheet:17-pro-max-512", "iPhone 17 Pro Max", "512 GB", 8600),
        _sealed_item("sheet:17-pro-max-1tb", "iPhone 17 Pro Max", "1 TB", 10300),
    ]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="mp:17-pro-max-256-ready",
            name="iPhone 17 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="PRATEADO",
            source="mercado_phone",
            condition="LACRADO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=7460,
            search_text="iphone 17 pro max 256 gb prateado celular lacrado",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Quanto está o 17pro max")

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "mp:17-pro-max-256-ready",
        "sheet:17-pro-max-256",
        "sheet:17-pro-max-512",
        "sheet:17-pro-max-1tb",
    }
    assert "7.460,00" in decision.reply
    assert "7.900,00" in decision.reply
    assert "8.600,00" in decision.reply
    assert "10.300,00" in decision.reply


@pytest.mark.asyncio
async def test_price_after_pro_max_does_not_replace_requested_model(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="mp:17-pro-max-7040",
            name="iPhone 17 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=7040,
            search_text="iphone 17 pro max 256 gb preto celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Ainda tá disponível o 17 pro Max de 7.040?",
        history=[
            {"role": "user", "content": "Oie"},
            {"role": "user", "content": "Retornaram?"},
            {
                "role": "assistant",
                "content": (
                    "Oie! Sim, retornamos 😊 Podemos dar continuidade e finalizar sua compra. "
                    "Você decidiu pela retirada na loja ou entrega?"
                ),
            },
        ],
    )

    assert _requested_iphone_model_keys("Ainda tá disponível o 17 pro Max de 7.040?") == (
        (17, "pro max"),
    )
    assert decision.handoff is False
    assert decision.product_references == ["mp:17-pro-max-7040"]
    assert "iPhone 17 Pro Max" in decision.reply
    assert "7.040,00" in decision.reply
    assert "não localizei" not in _normalize(decision.reply)
    assert "lista completa" not in _normalize(decision.reply)


@pytest.mark.asyncio
async def test_bare_pro_max_request_includes_ready_stock_and_sealed_order_options(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [
        _sealed_item("sheet:17-pro-max-256", "iPhone 17 Pro Max", "256 GB", 7900),
        _sealed_item("sheet:17-pro-max-512", "iPhone 17 Pro Max", "512 GB", 8600),
        _sealed_item("sheet:17-pro-max-1tb", "iPhone 17 Pro Max", "1 TB", 10300),
    ]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="mp:17-pro-max-256-ready",
            name="iPhone 17 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="PRATEADO",
            source="mercado_phone",
            condition="LACRADO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=7460,
            search_text="iphone 17 pro max 256 gb prateado celular lacrado",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("preciso 17 pro max")

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "mp:17-pro-max-256-ready",
        "sheet:17-pro-max-256",
        "sheet:17-pro-max-512",
        "sheet:17-pro-max-1tb",
    }
    for price in ("7.460,00", "7.900,00", "8.600,00", "10.300,00"):
        assert price in decision.reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "items", "expected_ids", "expected_prices"),
    [
        (
            "E qual o preço do 17 pro Max?",
            [
                ("sheet:17-pro-max-256", "iPhone 17 Pro Max", "256 GB", 8100),
                ("sheet:17-pro-max-512", "iPhone 17 Pro Max", "512 GB", 9400),
                ("sheet:17-pro-max-1tb", "iPhone 17 Pro Max", "1 TB", 10600),
            ],
            {
                "sheet:17-pro-max-256",
                "sheet:17-pro-max-512",
                "sheet:17-pro-max-1tb",
            },
            ("8.100,00", "9.400,00", "10.600,00"),
        ),
        (
            "E o iPhone 17 pro?",
            [
                ("sheet:17-pro-256", "iPhone 17 Pro", "256 GB", 7400),
                ("sheet:17-pro-512", "iPhone 17 Pro", "512 GB", 8600),
            ],
            {"sheet:17-pro-256", "sheet:17-pro-512"},
            ("7.400,00", "8.600,00"),
        ),
    ],
)
async def test_iphone_17_pro_prices_survive_mercado_phone_search_failure(
    tmp_path,
    text,
    items,
    expected_ids,
    expected_prices,
):
    class UnavailableMercadoClient:
        async def fetch_all_inventory(self):
            raise RuntimeError("Mercado Phone temporarily unavailable")

    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [_sealed_item(*item) for item in items]
    cache = StoreCatalogCache(
        UnavailableMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [
        {"role": "user", "content": "Queria saber o preço do iPhone 17"},
        {
            "role": "assistant",
            "content": (
                "Sim 😊 Encontrei estas opções de iPhone 17 disponíveis: "
                "iPhone 17 — 256 GB — NOVO LACRADO — R$ 5.600,00"
            ),
        },
    ]
    if text == "E o iPhone 17 pro?":
        history.extend(
            [
                {"role": "user", "content": "E qual o preço do 17 pro Max?"},
                {
                    "role": "assistant",
                    "content": (
                        "Não consegui confirmar o preço do iPhone 17 Pro Max agora. "
                        "Você procura alguma capacidade específica?"
                    ),
                },
            ]
        )

    decision = await agent.respond(text, history=history)

    assert decision.handoff is False
    assert set(decision.product_references) == expected_ids
    for price in expected_prices:
        assert price in decision.reply
    assert "capacidade específica" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_shared_variant_question_returns_base_pro_and_pro_max(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-128",
            name="iPhone 15",
            category="Celular",
            capacity="128GB",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2920,
            battery_health=83,
            search_text="iphone 15 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-128",
            name="iPhone 15 Pro",
            category="Celular",
            capacity="128GB",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3460,
            battery_health=93,
            search_text="iphone 15 pro 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-max-256",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256GB",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4130,
            battery_health=90,
            search_text="iphone 15 pro max 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Voce teria o iphone 15 ou 15 pro ou pro max?")

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-15-128",
        "iphone-15-pro-128",
        "iphone-15-pro-max-256",
    }
    assert "iPhone 15" in decision.reply
    assert "iPhone 15 Pro" in decision.reply
    assert "iPhone 15 Pro Max" in decision.reply


@pytest.mark.asyncio
async def test_shared_pro_and_pro_max_suffix_returns_both_requested_models(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Pode me passar o valor do iPhone 17 pro e do pro max")

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "17-pro-512",
        "17-pro-max-128",
        "17-pro-max-256",
    }
    assert "iPhone 17 Pro" in decision.reply
    assert "iPhone 17 Pro Max" in decision.reply


@pytest.mark.asyncio
async def test_iphone_17_pro_and_up_lists_all_newer_sealed_models(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.sealed_cache.items = [
        _sealed_item("iphone-16-pro-max-256", "iPhone 16 Pro Max", "256 GB", 6500),
        _sealed_item("iphone-17-128", "iPhone 17", "128 GB", 5000),
        _sealed_item("iphone-17-air-256", "iPhone 17 Air", "256 GB", 6500),
        _sealed_item("iphone-17-pro-512", "iPhone 17 Pro", "512 GB", 7200),
        _sealed_item("iphone-17-pro-max-256", "iPhone 17 Pro Max", "256 GB", 7900),
        _sealed_item("iphone-18-128", "iPhone 18", "128 GB", 8200),
        _sealed_item("iphone-18-pro-256", "iPhone 18 Pro", "256 GB", 9500),
        _sealed_item("iphone-18-pro-max-256", "iPhone 18 Pro Max", "256 GB", 11000),
    ]
    query = "Gostaria de saber os valores de iPhones novos lacrado iPhone 17 pro pra cima"
    history = [
        {"role": "user", "content": "Bom dia tudo bem"},
        {
            "role": "assistant",
            "content": "Bom dia! Tudo bem por aqui. Como posso te ajudar?",
        },
    ]

    decision = await agent.respond(query, history=history)

    assert decision.handoff is False
    assert decision.image_urls == []
    assert set(decision.product_references) == {
        "iphone-17-pro-512",
        "iphone-17-pro-max-256",
        "iphone-18-128",
        "iphone-18-pro-256",
        "iphone-18-pro-max-256",
    }
    for model in (
        "iPhone 17 Pro",
        "iPhone 17 Pro Max",
        "iPhone 18",
        "iPhone 18 Pro",
        "iPhone 18 Pro Max",
    ):
        assert model in decision.reply
    for price in (
        "R$ 7.200,00",
        "R$ 7.900,00",
        "R$ 8.200,00",
        "R$ 9.500,00",
        "R$ 11.000,00",
    ):
        assert price in decision.reply
    for excluded_model in ("iPhone 16 Pro Max", "iPhone 17 Air"):
        assert excluded_model not in decision.reply

    exact_model = await agent.respond("Qual o valor do iPhone 17 Pro?")

    assert exact_model.product_references == ["iphone-17-pro-512"]
    assert "iPhone 17 Pro Max" not in exact_model.reply


@pytest.mark.asyncio
async def test_slash_separated_pro_and_pro_max_request_returns_both_models(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-16-pro-pro-max-slash.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-pro-128-preto",
            name="iPhone 16 Pro",
            category="Celular",
            capacity="128GB",
            color="TITÂNIO PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4190,
            battery_health=88,
            search_text="iphone 16 pro titanio preto 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-256-preto",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5280,
            battery_health=89,
            search_text="iphone 16 pro max titanio preto 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-256-natural",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO NATURAL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5280,
            battery_health=98,
            search_text="iphone 16 pro max titanio natural 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    text = (
        "Oie, bom dia! Queria mais informações sobre os iPhones 16 pro/pro max "
        "q vcs tem disponível!"
    )

    decision = await agent.respond(text)

    assert _requested_iphone_model_keys(text) == ((16, "pro"), (16, "pro max"))
    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-16-pro-128-preto",
        "iphone-16-pro-max-256-preto",
        "iphone-16-pro-max-256-natural",
    }
    assert "iPhone 16 Pro" in decision.reply
    assert "iPhone 16 Pro Max" in decision.reply


@pytest.mark.asyncio
async def test_shared_pro_max_suffix_matches_bare_first_model_and_keeps_both_units(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)

    class SharedProMaxCatalog:
        items = [_sealed_item("iphone-17-pro-max-256-lacrado", "iPhone 17 Pro Max", "256 GB", 8000)]

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
        sealed_cache=SharedProMaxCatalog(),
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-base",
            name="iPhone 16",
            category="Celular",
            capacity="128GB",
            color="AZUL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4600,
            search_text="iphone 16 azul 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-preto",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5200,
            search_text="iphone 16 pro max preto 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-titanio",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="512GB",
            color="TITANIO NATURAL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5600,
            search_text="iphone 16 pro max titanio natural 512gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Procuro iphone 16 ou 17 pro max")

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-16-pro-max-preto",
        "iphone-16-pro-max-titanio",
        "iphone-17-pro-max-256-lacrado",
    }
    assert decision.reply.count("iPhone 16 Pro Max") == 2
    assert "iPhone 17 Pro Max" in decision.reply
    assert "não aparece" not in decision.reply.lower()


def test_procuro_iphone_models_is_a_catalog_availability_request():
    assert _is_product_availability_request("Procuro iphone 16 ou 17 pro max") is True


@pytest.mark.asyncio
async def test_reversed_airpods_pro_3_model_order_returns_the_matching_available_item(tmp_path):
    assert _requested_iphone_model_keys("Gostaria de saber se vcs tem o air pods 3 pro") == ()

    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=SealedCatalog(),
    )
    cache.items = [
        InventoryItem(
            external_id="airpods-pro-3-seminovo",
            name="AIRPODS PRO 3",
            category="Celular",
            condition="SEMINOVO COM GARANTIA APPLE",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=1290,
            search_text="airpods pro 3 celular seminovo com garantia apple",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Gostaria de saber se vcs tem o air pods 3 pro")

    assert decision.handoff is False
    assert decision.product_references == ["airpods-pro-3-seminovo"], decision.reply
    assert "airpods pro 3" in _normalize(decision.reply)
    assert "R$ 1.290,00" in decision.reply
    assert "não localizei" not in _normalize(decision.reply)
    assert "lista dos seminovos" not in _normalize(decision.reply)


@pytest.mark.asyncio
async def test_generic_airpods_request_lists_all_available_models_and_conditions(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [
        _sealed_item("airpods-4-sem-anc", "AirPods 4 sem ANC", "-", 1100),
        _sealed_item("airpods-4-com-anc", "AirPods 4 com ANC", "-", 1500),
        _sealed_item("airpods-pro-3-lacrado", "AirPods Pro 3", "-", 1800),
    ]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [
        InventoryItem(
            external_id="airpods-pro-3-seminovo",
            name="AIRPODS PRO 3",
            category="Celular",
            condition="SEMINOVO COM GARANTIA APPLE",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=1290,
            search_text="airpods pro 3 celular seminovo com garantia apple",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Veja se o Luan tem AirPod")

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "airpods-4-sem-anc",
        "airpods-4-com-anc",
        "airpods-pro-3-lacrado",
        "airpods-pro-3-seminovo",
    }
    for expected in ("AirPods 4 sem ANC", "AirPods 4 com ANC", "AirPods Pro 3"):
        assert expected in decision.reply
    assert "SEMINOVO" in decision.reply.upper()
    assert "NOVO LACRADO" in decision.reply.upper()


@pytest.mark.asyncio
async def test_explicit_iphone_xr_availability_does_not_append_unrelated_sealed_options(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=SealedCatalog(),
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-xr-white-64",
            name="IPHONE XR",
            category="Celular",
            capacity="64GB",
            color="BRANCO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=500,
            search_text="iphone xr branco 64gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("O iphone xr por 500 ainda está disponível")

    assert decision.product_references == ["iphone-xr-white-64"]
    assert "IPHONE XR" in decision.reply.upper()
    assert "IPHONE 17" not in decision.reply.upper()
    assert "NOVO LACRADO" not in decision.reply.upper()


@pytest.mark.asyncio
async def test_14_pro_price_followup_keeps_the_exact_unit_from_the_previous_list(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=None,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-14-pro-roxo-256",
            name="IPHONE 14 PRO",
            category="Celular",
            capacity="256GB",
            color="ROXO PROFUNDO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3050,
            battery_health=100,
            source="mercado_phone",
            search_text="iphone 14 pro roxo profundo 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-14-pro-max-preto-256",
            name="IPHONE 14 PRO MAX",
            category="Celular",
            capacity="256GB",
            color="PRETO ESPACIAL",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3600,
            battery_health=90,
            source="mercado_phone",
            search_text="iphone 14 pro max preto espacial 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Eu queria saber mais sobre esse 14 pro semi-novo de 3050,00",
        history=[
            {
                "role": "user",
                "content": "Queria saber o preço do 14 pro e o pro Max de 256g e 512g",
            },
            {
                "role": "assistant",
                "content": (
                    "Sim 😊 Encontrei estas opções de iPhone disponíveis:\n"
                    "• IPHONE 14 PRO — ROXO PROFUNDO — 256GB — SEMINOVO — R$ 3.050,00 | Bat: 100%\n"
                    "• IPHONE 14 PRO MAX — PRETO ESPACIAL — 256GB — SEMINOVO — R$ 3.600,00 | Bat: 90%"
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-14-pro-roxo-256"]
    assert "ROXO PROFUNDO — 256GB — SEMINOVO — R$ 3.050,00" in decision.reply
    assert "IPHONE 14 PRO MAX" not in decision.reply
    assert "NÃO LOCALIZEI" not in decision.reply.upper()


@pytest.mark.asyncio
async def test_batched_14_pro_information_request_returns_catalog_instead_of_handoff(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-batched-14-pro.json",
        sealed_cache=None,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-14-pro-256-purple",
            name="IPHONE 14 PRO",
            category="Celular",
            capacity="256GB",
            color="ROXO PROFUNDO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3050,
            battery_health=100,
            source="mercado_phone",
            search_text="iphone 14 pro roxo profundo 256gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Vi no perfil do insta\n"
        "iPhone 14 pro\n"
        "Gostaria de saber mais informações sobre o aparelho\n"
        "Quero um que tenha a câmera boa sabe",
        history=[
            {"role": "user", "content": "Olá boa noite"},
            {
                "role": "assistant",
                "content": "Cwb.iphones agradece seu contato. Como podemos ajudar?",
            },
        ],
    )

    assert decision.handoff is False
    assert decision.product_references == ["iphone-14-pro-256-purple"]
    assert "IPHONE 14 PRO" in decision.reply
    assert "R$ 3.050,00" in decision.reply


@pytest.mark.asyncio
async def test_availability_confirmation_uses_last_product_clarification(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=None,
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-pro-black-128",
            name="IPHONE 16 PRO",
            category="Celular",
            capacity="128GB",
            color="PRETO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4500,
            battery_health=90,
            search_text="iphone 16 pro preto 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-natural-256",
            name="IPHONE 16 PRO",
            category="Celular",
            capacity="256GB",
            color="TITANIO NATURAL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4800,
            battery_health=92,
            search_text="iphone 16 pro titanio natural 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Sim",
        history=[
            {"role": "user", "content": "16"},
            {
                "role": "assistant",
                "content": (
                    "Temos sim o iPhone 16: seminovo, ultramarino, 128 GB, "
                    "bateria 78%, por R$ 3.660,00."
                ),
            },
            {"role": "user", "content": "PRO?"},
            {
                "role": "assistant",
                "content": (
                    "Você quer saber sobre o iPhone 16 Pro, certo? Vou consultar "
                    "a disponibilidade e os valores dessa versão 😊"
                ),
            },
        ],
    )

    assert set(decision.product_references) == {
        "iphone-16-pro-black-128",
        "iphone-16-pro-natural-256",
    }
    assert "IPHONE 16 PRO" in decision.reply.upper()
    assert "NÃO LOCALIZEI" not in decision.reply.upper()


def _price_followup_history():
    return [
        {
            "role": "user",
            "content": "Gostaria de saber o valor do iPhone 17 pro 256gb",
        },
        {
            "role": "assistant",
            "content": (
                "Sim. Encontrei estas opcoes de iPhone 17 Pro disponiveis: "
                "iPhone 17 Pro - 256 GB - NOVO LACRADO - R$ 7.200,00"
            ),
        },
    ]


def _build_17_pro_price_followup_agent(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.sealed_cache.items.insert(
        0,
        _sealed_item("17-pro-256", "iPhone 17 Pro", "256 GB", 7200),
    )
    return agent


@pytest.mark.asyncio
async def test_iphone_17_pro_max_cash_price_on_pix_uses_catalog_on_first_reply(tmp_path):
    agent = build_agent(tmp_path)
    initial_request = (
        "Você tem o iPhone 17 pro max? Qual valor para pagamento à vista no pix?"
    )
    greeting_history = [{"role": "user", "content": "Olá, bom dia!"}]

    first_decision = await agent.respond(initial_request, history=greeting_history)

    assert first_decision.handoff is False
    assert first_decision.product_references == [
        "17-pro-max-128",
        "17-pro-max-256",
    ]
    assert "R$ 7.000,00" in first_decision.reply
    assert "R$ 7.800,00" in first_decision.reply
    assert "juros da máquina" not in _normalize(first_decision.reply)

    followup_decision = await agent.respond(
        "Certo e qual valor do iPhone 17 pro max?",
        history=[
            *greeting_history,
            {"role": "user", "content": initial_request},
            {"role": "assistant", "content": first_decision.reply},
        ],
    )

    assert followup_decision.handoff is False
    assert followup_decision.product_references == first_decision.product_references
    assert "R$ 7.000,00" in followup_decision.reply
    assert "R$ 7.800,00" in followup_decision.reply


@pytest.mark.asyncio
async def test_explicit_catalog_price_and_payment_methods_request_answers_both(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Qual o preço do iPhone 17 Pro Max e quais formas de pagamento vocês aceitam?"
    )
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert decision.product_references == [
        "17-pro-max-128",
        "17-pro-max-256",
    ]
    assert "r$ 7.000,00" in reply
    assert "r$ 7.800,00" in reply
    assert "pix" in reply
    assert "cartao de credito" in reply


@pytest.mark.asyncio
async def test_combined_availability_pix_and_installment_answers_every_question(tmp_path):
    agent = build_agent(tmp_path)
    item = _sealed_item("17-pro-256-blue", "iPhone 17 Pro", "256 GB", 7300)
    item.color = "AZUL-INTENSO"
    item.search_text = "iphone 17 pro azul intenso 256 gb novo lacrado"
    agent.cache.sealed_cache.items.insert(0, item)
    agent.cache.items = [
        InventoryItem(
            external_id="inventory-cable",
            name="Cabo USB",
            category="Acessório",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=50,
            source="mercado_phone",
            search_text="cabo usb",
        )
    ]
    agent.cache.last_refresh = time.time()

    text = (
        "Oii, tudo bem? 17 pro 256 no azul, ainda tem? R$ 7.300,00 é o valor no pix né? "
        "Se for em 10x, quanto fica?"
    )
    decision = await agent.respond(text)
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert decision.product_references == ["17-pro-256-blue"]
    assert "IPHONE 17 PRO" in decision.reply.upper()
    assert "AZUL-INTENSO" in decision.reply.upper()
    assert "no pix" in reply
    assert "R$ 7.300,00" in decision.reply
    assert "10x de" in decision.reply
    assert "1x de" in decision.reply
    assert "18x de" in decision.reply


@pytest.mark.asyncio
async def test_pix_discount_followup_answers_payment_policy_instead_of_repeating_catalog(tmp_path):
    agent = _build_17_pro_price_followup_agent(tmp_path)

    decision = await agent.respond(
        "Esse valor no pix tem desconto?",
        history=_price_followup_history(),
    )
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "nao ha desconto no pix" in reply
    assert "encontrei estas opcoes" not in reply


@pytest.mark.asyncio
async def test_catalog_price_negotiation_does_not_send_trade_in_form(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-14-pro-roxo-128",
            name="IPHONE 14 PRO",
            category="Celular",
            capacity="128GB",
            color="ROXO PROFUNDO",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=2930,
            battery_health=85,
            source="mercado_phone",
            search_text="iphone 14 pro roxo profundo 128gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    history = [
        {"role": "user", "content": "Estou interessado no Iphone 14 Pro"},
        {
            "role": "assistant",
            "content": (
                "Temos o iPhone 14 Pro seminovo 128GB, na cor Roxo Profundo, "
                "com 85% de saúde da bateria, por R$ 2.930."
            ),
        },
        {"role": "user", "content": "Gostaria de saber quais cores vocês tem no modelo"},
        {"role": "assistant", "content": "No momento, temos disponível apenas na cor Roxo Profundo."},
        {"role": "user", "content": "Sim"},
        {"role": "user", "content": "Por gentileza"},
        {
            "role": "assistant",
            "content": "Claro! Seguem as fotos do iPhone 14 Pro 128GB Roxo Profundo:",
        },
    ]

    decision = await agent.respond("Você consegue fazer por 2.900$?", history=history)
    reply = _normalize(decision.reply)

    assert decision.handoff is True
    assert "confirmar essa negociacao" in reply
    assert "lista de avaliacao" not in reply


@pytest.mark.asyncio
async def test_pix_same_value_followup_answers_payment_policy_instead_of_repeating_catalog(tmp_path):
    agent = _build_17_pro_price_followup_agent(tmp_path)

    decision = await agent.respond(
        "No pix sai o mesmo valor?",
        history=_price_followup_history(),
    )
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "nao ha desconto no pix" in reply
    assert "encontrei estas opcoes" not in reply


@pytest.mark.asyncio
async def test_price_validity_followup_answers_price_policy_instead_of_repeating_catalog(tmp_path):
    agent = _build_17_pro_price_followup_agent(tmp_path)

    decision = await agent.respond(
        "Esse valor nao vale mais?",
        history=_price_followup_history(),
    )
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "os precos podem ser alterados sem aviso previo" in reply
    assert "confirmacao deve ser feita no momento do atendimento" in reply
    assert "encontrei estas opcoes" not in reply


@pytest.mark.asyncio
async def test_price_increase_followup_does_not_turn_delivery_deadline_into_budget(tmp_path):
    agent = _build_17_pro_price_followup_agent(tmp_path)
    history = _price_followup_history()
    history[1]["content"] += (
        " São aparelhos por encomenda, com entrega em até 1 semana "
        "e pagamento antecipado antes do despacho."
    )

    decision = await agent.respond("Aumentou o valor, né?", history=history)
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "os precos podem ser alterados sem aviso previo" in reply
    assert "ate r$ 1,00" not in reply
    assert "nao localizei aparelhos" not in reply


@pytest.mark.asyncio
async def test_interest_in_product_ad_sends_catalog_details_instead_of_trade_in_form(
    tmp_path, monkeypatch
):
    settings = Settings(
        openai_api_key="test-key",
        google_sheets_enabled=False,
        faq_path=str(tmp_path / "faq.yaml"),
    )
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-product-ad.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-pro-max-256-white-86",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="Titânio Branco",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=4070,
            battery_health=86,
            source="mercado_phone",
            search_text=(
                "iPhone 15 Pro Max 256 GB Titânio Branco seminovo "
                "bateria 86% celular em estoque"
            ),
        ),
        InventoryItem(
            external_id="iphone-15-pro-max-512-white-88",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="512 GB",
            color="Titânio Branco",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=4900,
            battery_health=88,
            source="mercado_phone",
            search_text="iPhone 15 Pro Max 512 GB Titânio Branco seminovo celular em estoque",
        ),
        InventoryItem(
            external_id="iphone-15-pro-max-256-blue-87",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="Titânio Azul",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3990,
            battery_health=87,
            source="mercado_phone",
            search_text="iPhone 15 Pro Max 256 GB Titânio Azul seminovo bateria 87% celular em estoque",
        ),
    ]
    cache.last_refresh = time.time()
    service = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    service.offline = False
    service.agent = object()
    runner_calls = []

    async def fake_model_response(_agent, prompt, *, max_turns):
        runner_calls.append((prompt, max_turns))
        return SimpleNamespace(
            final_output=AgentDecision(
                reply=TRADE_IN_FORM,
                handoff=True,
                handoff_reason=TRADE_IN_REASON,
                confidence="high",
            )
        )

    monkeypatch.setattr(Runner, "run", fake_model_response)
    text = "Fiquei interessada nesse celular"
    image_description = (
        "Imagem do anúncio da loja: iPhone 15 Pro Max, 256 GB, Titânio Branco, "
        "disponível para venda, preço à vista R$ 4.070,00 ou 18x de R$ 512,00, "
        "bateria 87%, 3 meses de garantia da loja."
    )

    decision = await service.respond(
        text,
        history=[
            {"role": "user", "content": "Oii"},
            {"role": "assistant", "content": "Oii! Como posso te ajudar?"},
        ],
        image_description=image_description,
    )

    assert runner_calls == []
    assert decision.handoff is False
    assert decision.product_references == ["iphone-15-pro-max-256-white-86"]
    assert "iPhone 15 Pro Max" in decision.reply
    assert "256 GB" in decision.reply
    assert "R$ 4.070,00" in decision.reply
    assert "lista de avaliação" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_explicit_device_sale_with_catalog_photo_still_sends_trade_in_form(tmp_path):
    service = build_agent(tmp_path)

    decision = await service.respond(
        "Fiquei interessada em vender meu iPhone 13, 128 GB, com bateria 87%",
        image_description=(
            "Anúncio da loja: iPhone 15 Pro Max, 256 GB, Titânio Branco, "
            "disponível para venda, preço R$ 4.070,00."
        ),
    )

    assert decision.reply == TRADE_IN_FORM
    assert decision.handoff is True


@pytest.mark.asyncio
async def test_ambiguous_catalog_photo_interest_hands_off_without_search(tmp_path):
    settings = Settings(google_sheets_enabled=False)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-ambiguous-product-ad.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-15-pro-max-256-white-86",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="Titânio Branco",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=4070,
            battery_health=86,
            source="mercado_phone",
            search_text="iPhone 15 Pro Max 256 GB Titânio Branco seminovo bateria 86%",
        ),
        InventoryItem(
            external_id="iphone-14-pro-max-128-black-88",
            name="iPhone 14 Pro Max",
            category="Celular",
            capacity="128 GB",
            color="Preto",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3200,
            battery_health=88,
            source="mercado_phone",
            search_text="iPhone 14 Pro Max 128 GB Preto seminovo bateria 88%",
        ),
    ]
    cache.last_refresh = time.time()
    service = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await service.respond(
        "Fiquei interessada nesse celular",
        image_description=(
            "Anúncio com dois cartões: iPhone 15 Pro Max 256 GB Titânio Branco, "
            "R$ 4.070,00, bateria 86%; e iPhone 14 Pro Max 128 GB Preto, "
            "R$ 3.200,00, bateria 88%."
        ),
    )

    assert decision.reply == CATALOG_BUYER_DETAILS_REPLY
    assert decision.handoff is True
    assert decision.product_references == []
    assert decision.image_urls == []


@pytest.mark.asyncio
async def test_multi_model_interest_keeps_capacity_on_the_last_requested_model(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-three-model-interest.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-pro-128",
            name="iPhone 16 Pro",
            category="Celular",
            capacity="128GB",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4190,
            battery_health=90,
            search_text="iphone 16 pro 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-16-pro-max-256",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256GB",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=5280,
            battery_health=93,
            search_text="iphone 16 pro max 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-15-pro-max-256",
            name="iPhone 15 Pro Max",
            category="Celular",
            capacity="256GB",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=4130,
            battery_health=90,
            search_text="iphone 15 pro max 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    text = "Olá tudo bem, tenho interesse no iPhone 16 pro e pro Max e no 15 pro Max de 256gb"

    decision = await agent.respond(text)

    assert _requested_iphone_model_keys(text) == (
        (16, "pro"),
        (16, "pro max"),
        (15, "pro max"),
    )
    assert decision.handoff is False
    assert set(decision.product_references) == {
        "iphone-16-pro-128",
        "iphone-16-pro-max-256",
        "iphone-15-pro-max-256",
    }
    assert "iPhone 16 Pro" in decision.reply
    assert "iPhone 16 Pro Max" in decision.reply
    assert "iPhone 15 Pro Max" in decision.reply


@pytest.mark.asyncio
async def test_batched_used_iphone_request_keeps_available_pro_after_missing_model(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory-batched-used-iphone.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-pro-256",
            name="iPhone 16 Pro",
            category="Celular",
            capacity="256GB",
            color="TITÂNIO DESERTO",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=4560,
            battery_health=90,
            search_text="iphone 16 pro titanio deserto 256gb celular seminovo",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    text = "Tem algum iPhone 17 usado? Ou 16 pro usado"

    decision = await agent.respond(text)

    assert decision.handoff is False
    assert decision.product_references == ["iphone-16-pro-256"]
    assert "No momento, não localizei esse produto seminovo" not in decision.reply
    assert "Sim" in decision.reply
    assert "iPhone 16 Pro" in decision.reply
    assert "256GB" in decision.reply
    assert "R$ 4.560,00" in decision.reply
    assert _requested_iphone_model_keys(text) == ((17, ""), (16, "pro"))


@pytest.mark.asyncio
async def test_bare_iphone_followup_with_friendly_suffix_reports_model_unavailable(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "iphone-14-pro-followup.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-14-pro-max-128",
            name="IPHONE 14 PRO MAX",
            category="Celular",
            capacity="128GB",
            color="PRETO ESPACIAL",
            source="mercado_phone",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=3080,
            battery_health=82,
            search_text="iphone 14 pro max preto espacial 128gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [
        {
            "role": "user",
            "content": (
                "Em meu amigo queria ver com vc se vc tem um iPhone 14 Pro Max "
                "ou o 14 pro"
            ),
        },
        {
            "role": "assistant",
            "content": "Cwb.iphones agradece seu contato. Como podemos ajudar?",
        },
        {
            "role": "user",
            "content": (
                "Em meu amigo queria ver com vc se vc tem um iPhone 14 Pro Max "
                "ou o 14 pro"
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Sim 😊 Encontrei estas opções de IPHONE 14 PRO MAX disponíveis:\n"
                "• IPHONE 14 PRO MAX — PRETO ESPACIAL — 128GB — SEMINOVO — "
                "R$ 3.080,00 | Bat: 82%"
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Sim 😊 Encontrei estas opções de IPHONE 14 PRO MAX disponíveis:\n"
                "• IPHONE 14 PRO MAX — PRETO ESPACIAL — 128GB — SEMINOVO — "
                "R$ 3.080,00 | Bat: 82%"
            ),
        },
    ]
    text = "E o 14 pro meu amigo"

    decision = await agent.respond(text, history=history)
    reply = _normalize(decision.reply)

    assert is_trade_in_request(text) is False
    assert is_trade_in_context_request(text, history) is False
    assert decision.handoff is False
    assert decision.product_references == []
    assert decision.image_urls == []
    assert reply == (
        "no momento, nao localizei o iphone 14 pro disponivel no estoque. "
        "posso verificar outro modelo ou capacidade?"
    )
