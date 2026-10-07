from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.agent import AgentService, _normalize
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem
from app.trade_in import is_trade_in_request


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


def _seminovo_item(item_id: str, name: str, capacity: str, price: float) -> InventoryItem:
    return InventoryItem(
        external_id=item_id,
        name=name,
        category="Celular",
        capacity=capacity,
        color="ROSA",
        price_brl=price,
        source="mercado_phone",
        condition="seminovo",
        availability="Disponivel para venda",
        quantity=1,
        battery_health=88,
        search_text=f"{name} {capacity} rosa celular seminovo",
    )


def _ready_sealed_item(
    item_id: str,
    name: str,
    capacity: str,
    price: float,
    color: str = "AZUL-INTENSO",
) -> InventoryItem:
    return InventoryItem(
        external_id=item_id,
        name=name,
        category="Celular",
        capacity=capacity,
        color=color,
        price_brl=price,
        source="mercado_phone",
        condition="LACRADO",
        availability="Disponível para venda",
        quantity=1,
        search_text=f"{name} {capacity} {color} celular lacrado",
    )


class SealedCatalog:
    def __init__(self):
        self.items = [_sealed_item("iphone-16-lacrado", "iPhone 16", "128 GB", 4600)]

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
    cache.items = [_seminovo_item("iphone-15-plus-rosa", "iPhone 15 Plus", "128 GB", 2830)]
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


def build_missing_new_iphone_14_agent(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = [_seminovo_item("iphone-14-roxo", "iPhone 14", "128 GB", 1940)]
    agent.cache.sealed_cache.items = []
    agent.cache.last_refresh = time.time()
    return agent


def build_iphone_18_pro_max_agent(tmp_path, *, include_ready_stock=True):
    agent = build_agent(tmp_path)
    agent.cache.items = (
        [
            _ready_sealed_item(
                "ready-18-pro-max-256",
                "iPhone 18 Pro Max",
                "256 GB",
                10000,
                "BORDO",
            ),
            _ready_sealed_item(
                "ready-18-pro-max-512",
                "iPhone 18 Pro Max",
                "512 GB",
                11000,
                "GLACIAL",
            ),
        ]
        if include_ready_stock
        else []
    )
    agent.cache.sealed_cache.items = [
        _sealed_item("sheet-18-pro-max-256", "iPhone 18 Pro Max", "256 GB", 10100),
        _sealed_item("sheet-18-pro-max-512", "iPhone 18 Pro Max", "512 GB", 11100),
        _sealed_item("sheet-18-pro-max-1tb", "iPhone 18 Pro Max", "1 TB", 14700),
    ]
    agent.cache.last_refresh = time.time()
    return agent


@pytest.mark.asyncio
async def test_missing_new_model_reports_absence_and_offers_seminovo_alternative(tmp_path):
    agent = build_missing_new_iphone_14_agent(tmp_path)

    decision = await agent.respond("quanto está o 14 novo de vcs?")

    normalized = _normalize(decision.reply)
    assert decision.handoff is False
    assert decision.product_references == []
    assert "nao localizei esse modelo novo/lacrado" in normalized
    assert "nem na lista de lacrados por encomenda" in normalized
    assert "nem entre os lacrados disponiveis no estoque" in normalized
    assert "iPhone 14" in decision.reply
    assert "SEMINOVO" in decision.reply
    assert "R$ 1.940,00" in decision.reply


@pytest.mark.asyncio
async def test_missing_ipad_seminovo_offers_new_ipads_instead_of_iphone_seminovos(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        _seminovo_item("iphone-13-pro-used", "iPhone 13 Pro", "128 GB", 2390),
        _ready_sealed_item("ipad-10-ready", "iPad 10", "64 GB", 2200),
    ]
    agent.cache.sealed_cache.items = [
        _sealed_item("ipad-11-new", "iPad 11 A16", "128 GB", 3100),
        _sealed_item("iphone-16-new", "iPhone 16", "128 GB", 4600),
    ]
    agent.cache.last_refresh = time.time()

    history = [
        {"role": "user", "content": "Boa noite"},
        {"role": "assistant", "content": "Boa noite! Como posso te ajudar?"},
        {"role": "user", "content": "Estou a procura de um ipad"},
        {"role": "user", "content": "10 ou 11"},
    ]
    decision = await agent.respond("Semi novo", history=history)

    assert decision.handoff is False
    assert decision.product_references == []
    assert decision.image_urls == []
    assert "iPad 10" in decision.reply
    assert "iPad 11 A16" in decision.reply
    assert "NOVO LACRADO" in decision.reply.upper()
    assert "IPHONE" not in decision.reply.upper()
    assert "SEMINOVOS DISPONÍVEIS PARA VENDA" not in decision.reply.upper()


@pytest.mark.asyncio
async def test_missing_ipad_seminovo_without_new_ipad_does_not_offer_iphone_seminovos(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        _seminovo_item("iphone-13-pro-used", "iPhone 13 Pro", "128 GB", 2390),
    ]
    agent.cache.sealed_cache.items = []
    agent.cache.last_refresh = time.time()

    decision = await agent.respond(
        "Semi novo",
        history=[
            {"role": "user", "content": "Estou a procura de um ipad"},
            {"role": "user", "content": "10 ou 11"},
        ],
    )

    assert decision.handoff is False
    assert decision.product_references == []
    assert decision.image_urls == []
    assert "IPHONE" not in decision.reply.upper()
    assert "iPads novos" in decision.reply


@pytest.mark.asyncio
@pytest.mark.parametrize("include_new_stock", [False, True])
async def test_new_models_followup_does_not_fall_back_to_previous_seminovo_list(
    tmp_path, include_new_stock
):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        _seminovo_item("iphone-16-pro-max-used", "iPhone 16 Pro Max", "256 GB", 5170)
    ]
    agent.cache.sealed_cache.items = (
        [_sealed_item("iphone-18-pro-max-sealed", "iPhone 18 Pro Max", "256 GB", 10100)]
        if include_new_stock
        else []
    )
    history = [
        {"role": "user", "content": "Quais opções de iPhone 16 Pro Max seminovo?"},
        {
            "role": "assistant",
            "content": (
                "IPHONE 16 PRO MAX\n"
                "• TITÂNIO DESERTO - 256GB - SEMINOVO — R$ 5.170,00 | Bat: 89%"
            ),
        },
    ]

    decision = await agent.respond("E os modelos novos disponíveis?", history=history)

    assert decision.handoff is False
    assert decision.product_references == []
    assert decision.image_urls == []
    assert "SEMINOVO" not in decision.reply.upper()
    assert "IPHONE 16 PRO MAX" not in decision.reply.upper()
    if include_new_stock:
        assert "IPHONE 18 PRO MAX" in decision.reply.upper()
        assert "NOVOS LACRADOS POR ENCOMENDA" in decision.reply.upper()
    else:
        assert "modelos novos/lacrados" in _normalize(decision.reply)


@pytest.mark.asyncio
async def test_new_or_seminovo_model_list_keeps_both_requested_conditions(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.sealed_cache.items = [
        _sealed_item("iphone-18-pro-max-sealed", "iPhone 18 Pro Max", "256 GB", 10100)
    ]

    decision = await agent.respond("Quais modelos novos ou seminovos disponíveis?")

    assert decision.handoff is False
    assert "IPHONE 15 PLUS" in decision.reply.upper()
    assert "SEMINOVO" in decision.reply.upper()
    assert "IPHONE 18 PRO MAX" in decision.reply.upper()
    assert "NOVOS LACRADOS POR ENCOMENDA" in decision.reply.upper()


def _model_range_seminovo_agent(tmp_path, *, include_sealed: bool):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        _seminovo_item(
            f"iphone-{model}-256-seminovo",
            f"iPhone {model}",
            "256 GB",
            3000 + model * 100,
        )
        for model in (13, 14, 15, 16, 17)
    ]
    agent.cache.sealed_cache.items = (
        [
            _sealed_item(
                f"iphone-{model}-256-lacrado",
                f"iPhone {model}",
                "256 GB",
                5000 + model * 100,
            )
            for model in (13, 14, 15, 16, 17)
        ]
        if include_sealed
        else []
    )
    agent.cache.last_refresh = time.time()
    return agent


@pytest.mark.asyncio
async def test_iphone_range_from_14_through_16_is_not_read_as_a_budget(tmp_path):
    agent = _model_range_seminovo_agent(tmp_path, include_sealed=False)
    greeting_history = [
        {"role": "user", "content": "Olá boa noite\nTudo bemm?"},
        {
            "role": "assistant",
            "content": "Olá, boa noite! Tudo bem por aqui 😊 E com você? Como posso ajudar?",
        },
    ]
    first_message = (
        "Gostaria de ver quais iPhones vocês têm disponível do 14 até o 16, "
        "com 256G e o mais próximo do 100%"
    )
    first = await agent.respond(first_message, history=greeting_history)

    assert first.handoff is False
    assert set(first.product_references) == {
        "iphone-14-256-seminovo",
        "iphone-15-256-seminovo",
        "iphone-16-256-seminovo",
    }, first.reply
    assert "R$ 16,00" not in first.reply
    assert first.image_urls == []


@pytest.mark.asyncio
async def test_hyphenated_seminovo_model_list_stays_in_used_inventory(tmp_path):
    agent = _model_range_seminovo_agent(tmp_path, include_sealed=True)
    greeting_history = [
        {"role": "user", "content": "Olá boa noite\nTudo bemm?"},
        {
            "role": "assistant",
            "content": "Olá, boa noite! Tudo bem por aqui 😊 E com você? Como posso ajudar?",
        },
    ]
    first_message = (
        "Gostaria de ver quais iPhones vocês têm disponível do 14 até o 16, "
        "com 256G e o mais próximo do 100%"
    )
    observed_first_reply = (
        "Não localizei aparelhos disponíveis até R$ 16,00 256GB. "
        "Posso procurar em uma faixa maior ou em outro modelo?"
    )
    second_message = (
        "Quero saber quais modelos vcs tem disponível entre o 14, 15 e 16 "
        "de 256GB semi-novo"
    )
    second = await agent.respond(
        second_message,
        history=[
            *greeting_history,
            {"role": "user", "content": first_message},
            {"role": "assistant", "content": observed_first_reply},
        ],
    )

    assert second.handoff is False
    assert set(second.product_references) == {
        "iphone-14-256-seminovo",
        "iphone-15-256-seminovo",
        "iphone-16-256-seminovo",
    }, second.reply
    assert "SEMINOVO" in second.reply.upper()
    assert "LACRADO" not in second.reply.upper()
    assert second.image_urls == []


@pytest.mark.asyncio
async def test_singular_new_model_followup_keeps_previous_model_scope(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.items = [
        _seminovo_item("iphone-16-pro-max-used", "iPhone 16 Pro Max", "256 GB", 5170)
    ]
    agent.cache.sealed_cache.items = []
    history = [
        {"role": "user", "content": "Quais opções de iPhone 16 Pro Max seminovo?"},
        {
            "role": "assistant",
            "content": "iPhone 16 Pro Max, 256 GB, seminovo por R$ 5.170,00.",
        },
    ]

    decision = await agent.respond("E o modelo novo disponível?", history=history)

    assert decision.handoff is False
    assert decision.product_references == []
    assert "nao localizei esse modelo novo/lacrado" in _normalize(decision.reply)
    assert "IPHONE 16 PRO MAX" in decision.reply.upper()
    assert "SEMINOVO" in decision.reply.upper()


@pytest.mark.asyncio
async def test_iphone_17_mixed_condition_request_includes_sealed_by_order_option(tmp_path):
    agent = build_agent(tmp_path)
    agent.cache.sealed_cache.items = [
        _sealed_item("sheet:iphone-17", "iPhone 17", "256 GB", 5900),
    ]

    decision = await agent.respond(
        "Bom dia gostaria de saber o valor do iPhone 17 normal, lacrado e semi novo se tiver tb"
    )

    assert decision.handoff is False
    assert decision.product_references == ["sheet:iphone-17"]
    assert "iPhone 17" in decision.reply
    assert "256 GB" in decision.reply
    assert "R$ 5.900,00" in decision.reply
    assert "Novos lacrados por encomenda" in decision.reply
    assert "iPhone 15 Plus" not in decision.reply
    assert "nao localizei esse modelo novo/lacrado" not in _normalize(decision.reply)


@pytest.mark.parametrize(
    ("message", "history"),
    [
        ("Gostaria de cotar o iPhone 18 Pro Max", []),
        (
            "Na verdade queria o 18 Pro Max",
            [
                {
                    "role": "user",
                    "content": "Vou ver pra comprar o iPhone 17 Pro Max ou o iPad Pro",
                },
                {
                    "role": "assistant",
                    "content": (
                        "Para o iPhone 17 Pro Max, temos 256 GB por R$ 8.100. "
                        "O iPad Pro não aparece entre os modelos disponíveis. "
                        "Quer que eu veja uma capacidade ou cor específica?"
                    ),
                },
            ],
        ),
        (
            "18 mesmo",
            [
                {"role": "user", "content": "Gostaria de cotar o iPhone 18 Pro Max"},
                {
                    "role": "assistant",
                    "content": (
                        "Não encontrei o iPhone 18 Pro Max cadastrado para cotação. "
                        "Você quis dizer outro modelo, como o iPhone 17 Pro Max?"
                    ),
                },
            ],
        ),
    ],
)
async def test_iphone_18_pro_max_quote_includes_ready_stock_before_order_options(
    tmp_path,
    message,
    history,
):
    agent = build_iphone_18_pro_max_agent(tmp_path)

    decision = await agent.respond(message, history=history)

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "ready-18-pro-max-256",
        "ready-18-pro-max-512",
        "sheet-18-pro-max-256",
        "sheet-18-pro-max-512",
        "sheet-18-pro-max-1tb",
    }
    ready_heading = "Lacrados disponíveis para pronta entrega:"
    order_heading = "Novos lacrados por encomenda:"
    assert ready_heading in decision.reply
    assert order_heading in decision.reply
    assert decision.reply.index(ready_heading) < decision.reply.index(order_heading)


@pytest.mark.asyncio
async def test_iphone_18_pro_max_quote_does_not_claim_ready_stock_when_none_exists(tmp_path):
    agent = build_iphone_18_pro_max_agent(tmp_path, include_ready_stock=False)

    decision = await agent.respond("Gostaria de cotar o iPhone 18 Pro Max")

    assert decision.handoff is False
    assert set(decision.product_references) == {
        "sheet-18-pro-max-256",
        "sheet-18-pro-max-512",
        "sheet-18-pro-max-1tb",
    }
    assert "Lacrados disponíveis para pronta entrega:" not in decision.reply
    assert "Novos lacrados por encomenda:" in decision.reply


@pytest.mark.asyncio
async def test_new_condition_followup_excludes_seminovo_when_customer_rejects_it(tmp_path):
    agent = build_missing_new_iphone_14_agent(tmp_path)
    history = []
    decisions = []

    for message in ("quanto está o 14 novo de vcs?", "novo vcs tem?", "novo", "sem ser seminovo"):
        prior_history = list(history)
        history.extend(
            [
                {"role": "user", "content": message},
            ]
        )
        decision = await agent.respond(message, history=prior_history)
        history.append({"role": "assistant", "content": decision.reply})
        decisions.append(decision)

    normalized = _normalize(decision.reply)
    assert decision.handoff is False
    assert decision.product_references == []
    assert "nao localizei esse modelo novo/lacrado" in normalized
    assert "nem na lista de lacrados por encomenda" in normalized
    assert "nem entre os lacrados disponiveis no estoque" in normalized
    assert "SEMINOVO" in decision.reply
    assert all(item.handoff is False for item in decisions)
    assert all(item.product_references == [] for item in decisions)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_ready_sealed_stock", [True, False])
async def test_new_followup_after_mixed_ready_request_keeps_condition_and_delivery_scope(
    tmp_path, has_ready_sealed_stock
):
    agent = build_agent(tmp_path)
    seminovos = [
        _seminovo_item("iphone-15-128-used", "iPhone 15", "128 GB", 2820),
        _seminovo_item("iphone-15-256-used", "iPhone 15", "256 GB", 2890),
        _seminovo_item("iphone-16-128-used", "iPhone 16", "128 GB", 3750),
        _seminovo_item("iphone-16-256-used", "iPhone 16", "256 GB", 3850),
    ]
    for item, color, battery in zip(
        seminovos,
        ("AZUL", "VERDE", "PRETO", "PRETO"),
        (86, 87, 90, 88),
    ):
        item.color = color
        item.battery_health = battery
        item.search_text = f"{item.name} {color} {item.capacity} celular seminovo"
    agent.cache.items = seminovos
    if has_ready_sealed_stock:
        agent.cache.items.extend(
            [
                _ready_sealed_item("iphone-16-128-ready", "iPhone 16", "128 GB", 4600),
                _ready_sealed_item("iphone-16-256-ready", "iPhone 16", "256 GB", 4800),
            ]
        )
    agent.cache.sealed_cache.items = (
        [
            _sealed_item("iphone-16-128-by-order", "iPhone 16", "128 GB", 4500),
            _sealed_item("iphone-16-256-by-order", "iPhone 16", "256 GB", 4700),
        ]
        if has_ready_sealed_stock
        else []
    )
    agent.cache.last_refresh = time.time()
    history = [
        {
            "role": "user",
            "content": (
                "Estou procurando um iPhone novo ou semi para compra. Voltei de viagem hoje "
                "e acabei perdendo o meu... Não consegui rastrear. Quais modelos você tem "
                "a pronta entrega? 15/16/17. Novo ou semi."
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Sim 😊 Encontrei estas opções de iPhone disponíveis:\n"
                "• IPHONE 15 — AZUL — 128GB — SEMINOVO — R$ 2.820,00 | Bat: 86%\n"
                "• IPHONE 15 — VERDE — 256GB — SEMINOVO — R$ 2.890,00 | Bat: 87%\n"
                "• IPHONE 16 — PRETO — 128GB — SEMINOVO — R$ 3.750,00 | Bat: 90%\n"
                "• IPHONE 16 — PRETO — 256GB — SEMINOVO — R$ 3.850,00 | Bat: 88%"
            ),
        },
    ]

    decision = await agent.respond("E novo?", history=history)

    assert decision.handoff is False
    assert decision.image_urls == []
    assert "SEMINOVO" not in decision.reply.upper()
    if has_ready_sealed_stock:
        assert set(decision.product_references) == {
            "iphone-16-128-ready",
            "iphone-16-256-ready",
        }
        assert "LACRADO" in decision.reply.upper()
        assert "4.600,00" in decision.reply
        assert "4.800,00" in decision.reply
    else:
        assert decision.product_references == []
        assert "Não localizei esse modelo a pronta entrega" in decision.reply


@pytest.mark.asyncio
async def test_missing_lacrado_followup_offers_catalog_alternatives_without_handoff(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Qual e o valor do lacrado?",
        history=[
            {
                "role": "user",
                "content": "Ola boa tarde ... qual o valor do iPhone 15 plus Rosa?",
            },
            {
                "role": "assistant",
                "content": (
                    "Temos o iPhone 15 Plus Rosa, 128GB, seminovo por R$ 2.830,00. "
                    "Esta disponivel e com 88% de saude da bateria."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert "atendente" not in _normalize(decision.reply)
    assert "nao localizei" in _normalize(decision.reply)
    assert "iPhone 15 Plus" in decision.reply
    assert "SEMINOVO" in decision.reply
    assert "iPhone 16" in decision.reply
    assert decision.reply.count("NOVO LACRADO") == 1


@pytest.mark.asyncio
async def test_battery_followup_for_unavailable_model_reports_stock_and_alternatives(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=None,
    )
    cache.items = [
        _seminovo_item("iphone-15-rosa", "iPhone 15", "128 GB", 2920),
        _seminovo_item("iphone-16e-rosa", "iPhone 16e", "128 GB", 2660),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Esse 15 plus, está quanto de bateria?",
        history=[
            {
                "role": "assistant",
                "content": (
                    "• iPhone 15 128gb: R$ 2.920,00 (18x de R$ 200,77)\n"
                    "• iPhone 15 Plus 128gb: R$ 2.830,00 (18x de R$ 194,58)\n"
                    "Qual você prefere?"
                ),
            }
        ],
    )

    normalized = _normalize(decision.reply)
    assert decision.handoff is False
    assert "vou confirmar" not in normalized
    assert "nao localizei" in normalized
    assert "iphone 15 plus" in normalized
    assert "iphone 16 e" in normalized


@pytest.mark.asyncio
async def test_missing_explicit_lacrado_offers_other_catalog_models_without_handoff(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Tem iPhone 15 Plus lacrado?")

    assert decision.handoff is False
    assert "atendente" not in _normalize(decision.reply)
    assert "iPhone 16" in decision.reply
    assert "NOVO LACRADO" in decision.reply
    assert decision.reply.count("NOVO LACRADO") == 1


@pytest.mark.asyncio
async def test_500gb_request_matches_512gb_ready_sealed_phone(tmp_path):
    settings = Settings(google_sheets_enabled=False, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=None,
    )
    cache.items = [
        _ready_sealed_item(
            "mp:iphone-17-pro-max-512-azul",
            "iPhone 17 Pro Max",
            "512 GB",
            8700,
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Ainda tem disponível o 17 pro max 500GB na cor azul intenso? Lacrado"
    )

    normalized = _normalize(decision.reply)
    assert decision.handoff is False
    assert decision.product_references == ["mp:iphone-17-pro-max-512-azul"]
    assert "não localizei" not in normalized
    assert "iPhone 17 Pro Max" in decision.reply
    assert "512 GB" in decision.reply
    assert "LACRADO" in decision.reply


@pytest.mark.asyncio
async def test_lacrado_alternatives_keep_ready_and_ordered_same_model_sections(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [
        _sealed_item(
            "sheet:iphone-17-pro-max-256",
            "iPhone 17 Pro Max",
            "256 GB",
            8000,
        ),
        _sealed_item(
            "sheet:iphone-17-pro-max-512",
            "iPhone 17 Pro Max",
            "512 GB",
            8700,
        ),
        _sealed_item("sheet:iphone-17", "iPhone 17", "256 GB", 5600),
    ]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [
        _ready_sealed_item(
            "mp:iphone-17-pro-max-256-prata",
            "iPhone 17 Pro Max",
            "256 GB",
            7460,
            "PRATEADO",
        ),
        _ready_sealed_item(
            "mp:iphone-17-pro-max-512-laranja",
            "iPhone 17 Pro Max",
            "512 GB",
            8700,
            "LARANJA-CÓSMICO",
        ),
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Tem iPhone 17 Pro Max 1 TB lacrado?")

    assert decision.handoff is False
    assert "Seminovos disponíveis para venda" not in decision.reply
    ready_heading = "Lacrados disponíveis para pronta entrega"
    order_heading = "Novos lacrados por encomenda"
    assert ready_heading in decision.reply
    assert order_heading in decision.reply
    assert decision.reply.count("iPhone 17 Pro Max") == 2
    assert decision.reply.index(ready_heading) < decision.reply.index(order_heading)
    ready_section = decision.reply.split(ready_heading, 1)[1].split(order_heading, 1)[0]
    order_section = decision.reply.split(order_heading, 1)[1]
    assert "256 GB" in ready_section and "512 GB" in ready_section
    assert "256 GB" in order_section and "512 GB" in order_section


@pytest.mark.asyncio
async def test_plural_lacrado_price_followup_lists_complete_sealed_catalog(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [
        _sealed_item("iphone-15-lacrado", "iPhone 15", "128 GB", 4000),
        _sealed_item("iphone-16e-lacrado", "iPhone 16e", "128 GB", 4200),
        _sealed_item("iphone-16-plus-lacrado", "iPhone 16 Plus", "256 GB", 4800),
        _sealed_item("iphone-16-pro-max-lacrado", "iPhone 16 Pro Max", "256 GB", 5500),
    ]
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = []
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Quanto estão os lacrados?",
        history=[
            {"role": "user", "content": "Qual o valor do iPhone 15?"},
            {
                "role": "assistant",
                "content": "Temos o iPhone 15, 128 GB, seminovo por R$ 2.830,00.",
            },
            {"role": "user", "content": "Do treze pra cima"},
            {
                "role": "assistant",
                "content": (
                    "Do iPhone 13 pra cima, com bateria de 90% ou mais, temos: "
                    "iPhone 13 Pro Max; iPhone 16e; iPhone 16 Plus; iPhone 16 Pro Max. "
                    "Também temos modelos lacrados do iPhone 15 em diante, mas neles a bateria não se aplica."
                ),
            },
        ],
    )

    assert decision.handoff is False
    for model in ("iPhone 15", "iPhone 16e", "iPhone 16 Plus", "iPhone 16 Pro Max"):
        assert model in decision.reply
    assert decision.reply.count("NOVO LACRADO") == 4
    assert "SEMINOVO" not in decision.reply.upper()


def build_accessory_agent(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [
        _sealed_item("sheet:bot:8", "Fonte Tipo-C 20W original", "-", 150),
    ]
    sealed.items[0].search_text = "fonte tipo c 20w original carregador novo lacrado"
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = []
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


def build_phone_and_accessory_agent(tmp_path):
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    sealed = SealedCatalog()
    sealed.items = [
        _sealed_item("sheet:bot:8", "Fonte Tipo-C 20W original", "-", 150),
    ]
    sealed.items[0].search_text = "fonte tipo c 20w original carregador novo lacrado"
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=sealed,
    )
    cache.items = [_seminovo_item("phone:15-pro", "iPhone 15 Pro", "128 GB", 3460)]
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


@pytest.mark.asyncio
async def test_source_question_uses_the_sealed_catalog_without_handoff(tmp_path):
    agent = build_accessory_agent(tmp_path)

    decision = await agent.respond("Vc vende apenas a fonte original do iPhone?")

    assert decision.handoff is False
    assert decision.product_references == ["sheet:bot:8"]
    assert "Fonte Tipo-C 20W original" in decision.reply
    assert "NOVO LACRADO" in decision.reply
    assert "R$ 150,00" in decision.reply


@pytest.mark.asyncio
async def test_plural_type_c_charger_price_uses_the_sealed_catalog(tmp_path):
    agent = build_accessory_agent(tmp_path)

    decision = await agent.respond("gostaria de saber o valor de carregadores tipo C")

    assert decision.handoff is False
    assert decision.product_references == ["sheet:bot:8"]
    assert "Fonte Tipo-C 20W original" in decision.reply
    assert "NOVO LACRADO" in decision.reply
    assert "R$ 150,00" in decision.reply
    assert "SEMINOVO" not in decision.reply.upper()


@pytest.mark.asyncio
async def test_plural_source_value_question_does_not_return_full_iphone_list(tmp_path):
    agent = build_accessory_agent(tmp_path)

    decision = await agent.respond("Gostaria de saber sobre valores de fonte para iPhones")

    assert decision.handoff is False
    assert decision.product_references == ["sheet:bot:8"]
    assert "Fonte Tipo-C 20W original" in decision.reply
    assert "R$ 150,00" in decision.reply
    assert "lista completa" not in _normalize(decision.reply)


@pytest.mark.asyncio
async def test_carregador_followup_uses_the_sealed_catalog(tmp_path):
    agent = build_accessory_agent(tmp_path)

    decision = await agent.respond(
        "Carregador",
        history=[
            {"role": "user", "content": "Vc vende apenas a fonte original do iPhone?"},
        ],
    )

    assert decision.handoff is False
    assert decision.product_references == ["sheet:bot:8"]
    assert "Fonte Tipo-C 20W original" in decision.reply


@pytest.mark.asyncio
async def test_charger_inclusion_question_uses_accessories_policy_without_listing_catalog(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Vem com carregador?",
        history=[
            {"role": "user", "content": "Queria saber se tem o iPhone 13 de 128 GB"},
            {
                "role": "assistant",
                "content": (
                    "No momento, não localizei esse produto seminovo disponível no sistema. "
                    "Segue a lista dos seminovos disponíveis para você escolher: iPhone 15 Plus."
                ),
            },
        ],
    )

    assert decision.handoff is False
    assert decision.product_references == []
    assert "lista completa" not in _normalize(decision.reply)
    assert "cabo e fonte novos" in decision.reply
    assert "apenas o cabo original" in decision.reply


@pytest.mark.asyncio
async def test_purchase_accessory_inclusion_question_does_not_open_trade_in_form(tmp_path):
    agent = build_agent(tmp_path)
    query = "na compra de um iphone, acompanha alguma coisa? como capa, película e carregador?"

    assert is_trade_in_request(query) is False

    decision = await agent.respond(
        query,
        history=[
            {"role": "user", "content": "ola, boa tarde"},
            {"role": "assistant", "content": "Olá, boa tarde! 😊 Como posso ajudar?"},
        ],
    )

    assert decision.handoff is False
    assert decision.product_references == []
    assert "lista de avaliação" not in _normalize(decision.reply)
    assert "capinha, película e protetor de câmera por R$ 10,00 cada" in decision.reply
    assert "cabo e fonte novos, homologados pela Anatel" in decision.reply
    assert "apenas o cabo original" in decision.reply


@pytest.mark.asyncio
async def test_type_c_purchase_reason_keeps_the_iphone_context(tmp_path):
    agent = build_phone_and_accessory_agent(tmp_path)

    decision = await agent.respond(
        "Acho melhor o iPhone 15 Pro porque o carregador já é tipo C, né?",
        history=[
            {
                "role": "assistant",
                "content": "iPhone 15 Pro, 128 GB, seminovo por R$ 3.460,00.",
            }
        ],
    )

    assert decision.handoff is False
    assert "iPhone 15 Pro" in decision.reply
    assert "Fonte Tipo-C 20W original" not in decision.reply
    assert "NOVO LACRADO" not in decision.reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "Vocês vendem carregador?",
        "Quanto custa a fonte?",
        "Quanto custa o carregador tipo C do iPhone 15 Pro?",
    ],
)
async def test_explicit_accessory_request_overrides_phone_history(tmp_path, query):
    agent = build_phone_and_accessory_agent(tmp_path)

    decision = await agent.respond(
        query,
        history=[
            {
                "role": "assistant",
                "content": "iPhone 15 Pro, 128 GB, seminovo por R$ 3.460,00.",
            }
        ],
    )

    assert decision.handoff is False
    assert decision.product_references == ["sheet:bot:8"]
    assert "Fonte Tipo-C 20W original" in decision.reply
    assert "R$ 150,00" in decision.reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "E aquele carregador por indução tem também?",
        "Aquele carregador magnético tem?",
    ],
)
async def test_specific_charger_type_does_not_match_type_c_source(tmp_path, query):
    agent = build_accessory_agent(tmp_path)

    decision = await agent.respond(query)

    assert decision.handoff is False
    assert decision.product_references == []
    assert "Fonte Tipo-C 20W original" not in decision.reply
    assert "nao localizei" in _normalize(decision.reply)


@pytest.mark.asyncio
async def test_capinha_price_question_does_not_reuse_source_history(tmp_path):
    agent = build_accessory_agent(tmp_path)

    decision = await agent.respond(
        "E capinha a partir de qual valor tem?",
        history=[
            {"role": "user", "content": "Vocês vendem a fonte tipo C?"},
            {
                "role": "assistant",
                "content": "Fonte Tipo-C 20W original — NOVO LACRADO — R$ 150,00",
            },
        ],
    )

    assert decision.handoff is False
    assert "R$ 10,00" in decision.reply
    assert "Fonte Tipo-C 20W original" not in decision.reply


@pytest.mark.asyncio
async def test_phone_only_and_separate_charger_question_uses_recent_seminovo_context(tmp_path):
    agent = build_agent(tmp_path)
    history = [
        {"role": "user", "content": "Poderiam me falar os valores das parcelas?"},
        {
            "role": "assistant",
            "content": (
                "Bom dia! O iPhone 14 Pro 256GB roxo profundo seminovo está disponível, "
                "com saúde da bateria de 96%. À vista: R$ 3.010,00."
            ),
        },
    ]

    decision = await agent.respond(
        "E vem somente o celular certo o carregador a parte vcs tem tbm",
        history=history,
    )

    reply = _normalize(decision.reply)
    assert decision.handoff is False
    assert decision.product_references == []
    assert "aparelhos seminovos acompanham cabo e fonte novos" in reply
    assert "homologados pela anatel" in reply
    assert "apenas o cabo original" not in reply
    assert "nao localizei uma opcao" not in reply
