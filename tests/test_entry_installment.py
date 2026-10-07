from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.agent import AgentService
from app.faq import FAQStore
from app.installments import (
    format_installment_table,
    simulate_installment_table_with_entry,
    simulate_installment_with_entry,
)
from app.config import Settings
from app.schemas import InventoryItem


def product() -> InventoryItem:
    return InventoryItem(
        external_id="sheet:iphone-17",
        name="iPhone 17",
        capacity="256 GB",
        price_brl=5700.0,
        condition="novo lacrado",
        source="google_sheets",
        search_text="iphone 17 256 gb novo lacrado",
    )


def test_entry_is_subtracted_before_all_installments_are_calculated():
    result = simulate_installment_table_with_entry(product(), 1000.0)

    assert result["encontrado"] is True
    assert result["preco_total_brl"] == 5700.0
    assert result["entrada_avista_brl"] == 1000.0
    assert result["saldo_restante_brl"] == 4700.0
    assert len(result["parcelas"]) == 18
    assert result["parcelas"][-1]["valor_parcela_brl"] == 323.16

    message = format_installment_table(result)
    assert "Preço total: R$ 5.700,00" in message
    assert "Entrada à vista: R$ 1.000,00" in message
    assert "Saldo restante para parcelar: R$ 4.700,00" in message


def test_entry_equal_to_or_above_product_price_is_rejected():
    assert simulate_installment_table_with_entry(product(), 5700.0)["encontrado"] is False
    assert simulate_installment_with_entry(product(), 5800.0, 12)["encontrado"] is False


class FakeMercadoClient:
    def __init__(self, items=None):
        self.items = list(items) if items is not None else []

    async def fetch_all_inventory(self):
        return self.items


class FakeSealedCache:
    def __init__(self):
        self.items = [product()]

    async def search(self, query: str, limit: int = 5):
        return self.items[:limit]

    async def get(self, product_id: str):
        return next((item for item in self.items if item.external_id == product_id), None)


@pytest.mark.asyncio
async def test_agent_returns_remaining_balance_simulation(tmp_path):
    settings = Settings(
        google_sheets_enabled=True,
        mercado_cache_ttl_seconds=60,
    )
    cache = StoreCatalogCache(
        FakeMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=FakeSealedCache(),
    )
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Quero dar uma entrada à vista de R$ 1.000 e parcelar o restante",
        history=[
            {
                "role": "assistant",
                "content": "O iPhone 17 256 GB novo lacrado custa R$ 5.700.",
            }
        ],
    )

    assert decision.handoff is False
    assert "Preço total: R$ 5.700,00" in decision.reply
    assert "Entrada à vista: R$ 1.000,00" in decision.reply
    assert "Saldo restante para parcelar: R$ 4.700,00" in decision.reply
    assert "18x de R$ 323,16" in decision.reply


@pytest.mark.asyncio
async def test_agent_returns_full_entry_comparison_table_when_quantity_is_requested(tmp_path):
    settings = Settings(
        google_sheets_enabled=True,
        mercado_cache_ttl_seconds=60,
    )
    cache = StoreCatalogCache(
        FakeMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=FakeSealedCache(),
    )
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Quero dar uma entrada à vista de R$ 1.000 e parcelar o restante em 12x",
        history=[
            {
                "role": "assistant",
                "content": "O iPhone 17 256 GB novo lacrado custa R$ 5.700.",
            }
        ],
    )

    assert decision.handoff is False
    assert "1x de" in decision.reply
    assert "12x de" in decision.reply
    assert "18x de" in decision.reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("price_brl", "price_label", "message", "entry_label", "balance_label", "expected_installments"),
    [
        (
            2840.0,
            "2.840",
            "Eu dando mil de entrada, parcelando o restante no cartão ficaria quanto de 4 ou 5x?",
            "1.000",
            "1.840",
            ("4x de R$ 497,62", "5x de R$ 401,40"),
        ),
        (
            6000.0,
            "6.000",
            "O preço fica seis mil e eu quero pagar dois mil de entrada, "
            "parcelando o restante no cartão em 4x.",
            "2.000",
            "4.000",
            ("4x de R$ 1.081,78",),
        ),
    ],
)
async def test_spoken_entry_uses_amount_adjacent_to_entry_marker(
    tmp_path,
    price_brl,
    price_label,
    message,
    entry_label,
    balance_label,
    expected_installments,
):
    item = InventoryItem(
        external_id="mercado:iphone-13-pro-max-blue-sierra",
        name="iPhone 13 Pro Max",
        capacity="128 GB",
        price_brl=price_brl,
        condition="seminovo",
        availability="Disponível para venda",
        quantity=1,
        source="mercado_phone",
        category="Celular",
        search_text="iphone 13 pro max 128 gb azul sierra seminovo",
    )
    settings = Settings(
        google_sheets_enabled=True,
        mercado_cache_ttl_seconds=60,
    )
    cache = StoreCatalogCache(
        FakeMercadoClient([item]),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    history = [
        {
            "role": "user",
            "content": "O valor seria esse certo? Mas está disponível?",
        },
        {
            "role": "assistant",
            "content": (
                "No catálogo, o iPhone 13 Pro Max 128 GB Azul Sierra seminovo está "
                f"por R$ {price_label} e consta como disponível para venda. 😊"
            ),
        },
    ]

    decision = await agent.respond(
        message,
        history=history,
    )

    assert decision.handoff is False
    assert decision.product_references == []
    assert decision.image_urls == []
    assert f"Preço total: R$ {price_label},00" in decision.reply
    assert f"Entrada à vista: R$ {entry_label},00" in decision.reply
    assert f"Saldo restante para parcelar: R$ {balance_label},00" in decision.reply
    for expected in expected_installments:
        assert expected in decision.reply
    if price_brl == 2840.0:
        assert "4x de R$ 768,07" not in decision.reply


def test_faq_allows_payment_with_multiple_cards():
    faq = FAQStore("data/faq.yaml")

    assert "mais de um cartão" in faq.get("pagamento")
    assert "mais de um cartão" in faq.get("cartões")
