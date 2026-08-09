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
    async def fetch_all_inventory(self):
        return []


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


def test_faq_allows_payment_with_multiple_cards():
    faq = FAQStore("data/faq.yaml")

    assert "mais de um cartão" in faq.get("pagamento")
    assert "mais de um cartão" in faq.get("cartões")
