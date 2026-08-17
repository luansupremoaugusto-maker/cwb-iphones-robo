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


def build_agent(tmp_path):
    settings = Settings(
        mercado_cache_ttl_seconds=60,
        faq_path="data/faq.yaml",
    )
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-13-pro-max-256",
            name="iPhone 13 Pro Max",
            category="Celular",
            capacity="256GB",
            color="Verde Alpino",
            condition="SEMINOVO",
            availability="Disponivel para venda",
            quantity=1,
            price_brl=3160.0,
            battery_health=90,
            search_text="iphone 13 pro max 256gb verde alpino seminovo",
        )
    ]
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


PRODUCT_CONTEXT = (
    "No momento, temos somente essa opção disponível: iPhone 13 Pro Max 256GB "
    "Verde Alpino, seminovo, com 90% de bateria, por R$ 3.160,00. 😊"
)


@pytest.mark.asyncio
async def test_payment_and_delivery_batch_answers_both_questions(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Fazem em qnts x?\n"
        "Fazem link de pagamento?\n"
        "E vocês entregam tbm? Ou pra retirada do na loja?",
        history=[{"role": "assistant", "content": PRODUCT_CONTEXT}],
    )

    reply = decision.reply
    assert decision.handoff is False
    assert "simulação do parcelamento pelo link" in reply.lower()
    assert "Enviamos para Curitiba e região por motoboy" in reply
    assert "Fazemos retirada na loja com horário marcado" in reply


@pytest.mark.asyncio
async def test_delivery_followup_answers_without_handoff_after_payment_batch(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Com quem eu falo?\nVcs entregam?",
        history=[
            {"role": "user", "content": "Fazem link de pagamento?"},
            {
                "role": "assistant",
                "content": "Para pagamento online, o cartão é passado pelo link de pagamento.",
            },
        ],
    )

    reply = decision.reply
    assert decision.handoff is False
    assert "Enviamos para Curitiba e região por motoboy" in reply
    assert "Fazemos retirada na loja com horário marcado" not in reply
