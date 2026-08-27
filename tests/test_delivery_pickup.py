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
    assert "não aceitamos mais link de pagamento" in reply.lower()
    assert "simulação do parcelamento pelo link" not in reply.lower()
    assert "%" not in reply
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
                "content": "Não aceitamos mais link de pagamento; aceitamos cartão na máquina física.",
            },
        ],
    )

    reply = decision.reply
    assert decision.handoff is False
    assert "Enviamos para Curitiba e região por motoboy" in reply
    assert "Fazemos retirada na loja com horário marcado" not in reply


@pytest.mark.asyncio
async def test_delivery_and_pickup_question_after_product_context_uses_faq(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Gostaria de saber como funciona para retirada ou entrega",
        history=[
            {
                "role": "user",
                "content": "Gostaria de saber se esse ainda está disponível",
            },
            {
                "role": "assistant",
                "content": (
                    "Oii! Sim, ainda está disponível 😊 É um iPhone 12 seminovo, 64GB, "
                    "branco, com 86% de saúde da bateria, por R$ 1.300 à vista. "
                    "Acompanha cabo e fonte novos, homologados pela Anatel."
                ),
            },
            {"role": "user", "content": "Que bom, bem também"},
            {
                "role": "assistant",
                "content": (
                    "Que bom 😊 Se quiser, posso te enviar mais detalhes ou simular o "
                    "parcelamento desse iPhone 12 64GB."
                ),
            },
            {"role": "user", "content": "Seria a vista mesmo"},
            {
                "role": "assistant",
                "content": (
                    "Perfeito 😊 À vista fica R$ 1.300 no iPhone 12 64GB seminovo. "
                    "Aceitamos PIX, dinheiro ou cartão de débito, sem taxas."
                ),
            },
        ],
    )

    reply = decision.reply
    assert decision.handoff is False
    assert "Enviamos para Curitiba e região por motoboy" in reply
    assert "Fazemos retirada na loja com horário marcado" in reply


@pytest.mark.asyncio
async def test_sealed_shipping_followup_requires_advance_payment(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Vocês conseguem me mandar?",
        history=[
            {
                "role": "user",
                "content": "Vocês vendem iPhone novo? Pronta entrega? Quais os valores?",
            },
            {
                "role": "assistant",
                "content": (
                    "Enviamos para Curitiba e região por motoboy. Para fora de Curitiba, "
                    "enviamos por Sedex. O pagamento deve ser antecipado antes do despacho."
                ),
            },
        ],
    )

    reply = decision.reply.lower()
    assert decision.handoff is False
    assert "enviamos para curitiba e região por motoboy" in reply
    assert "para fora de curitiba, enviamos por sedex" in reply
    assert "pagamento deve ser antecipado antes do despacho" in reply
    assert "hora da entrega" not in reply
