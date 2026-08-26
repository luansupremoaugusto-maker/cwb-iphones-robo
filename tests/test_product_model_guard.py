from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.agent import (
    AgentService,
    CATALOG_BUYER_DETAILS_REPLY,
    _extract_budget_limit,
    _format_product_availability,
    _is_available_list_request,
    _is_product_availability_request,
    _normalize,
)
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem
from app.trade_in import is_trade_in_context_request, is_trade_in_request


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
async def test_explicit_ipad_does_not_return_iphone_or_macbook(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Voce tem algum iPad que esteja bem em conta?")

    assert decision.product_references == ["ipad-air-128"]
    assert "IPAD AIR" in decision.reply.upper()
    assert "IPHONE" not in decision.reply.upper()
    assert "MACBOOK" not in decision.reply.upper()


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
