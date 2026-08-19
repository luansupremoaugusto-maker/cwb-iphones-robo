from __future__ import annotations

import pytest

from app.adapters.mercado_phone import InventoryCache
from app.agent import AgentService, _normalize
from app.config import Settings
from app.faq import FAQStore


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


class AvailableCatalog:
    async def list_available_products(self):
        return {
            "encontrado": True,
            "seminovos": [
                {
                    "nome": "iPhone 13 Pro Max",
                    "capacidade": "128 GB",
                    "cor": "GRAFITE",
                    "condicao": "SEMINOVO",
                    "precos_brl": [2830.0],
                    "saude_bateria": 91,
                }
            ],
            "lacrados": [],
        }


def build_agent(tmp_path):
    settings = Settings(
        openai_api_key=None,
        mercado_cache_ttl_seconds=60,
        faq_path="data/faq.yaml",
    )
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


def build_agent_with_cache(cache):
    settings = Settings(
        openai_api_key=None,
        mercado_cache_ttl_seconds=60,
        faq_path="data/faq.yaml",
    )
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


def test_payment_faq_declares_debit_as_cash_without_fees():
    faq = FAQStore("data/faq.yaml")
    payment = _normalize(faq.get("pagamento"))
    cards = _normalize(faq.get("cartões"))

    assert "cartao de debito" in payment
    assert "pix" in payment
    assert "dinheiro" in payment
    assert "pagamento integral a vista" in payment
    assert "sem taxas" in payment
    assert "cartao de debito" in cards


@pytest.mark.asyncio
async def test_payment_question_confirms_debit_with_two_credit_cards_and_pix(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond(
        "Vocês aceitam dois cartões de crédito pra dar o valor? E mais um valor no pix ou débito?"
    )
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "dois cartoes de credito" in reply
    assert "pix" in reply
    assert "cartao de debito" in reply
    assert "dinheiro" in reply
    assert "sem taxas" in reply
    assert "debito nao foi confirmado" not in reply


@pytest.mark.asyncio
async def test_generic_payment_method_question_about_exchange_does_not_list_products():
    agent = build_agent_with_cache(AvailableCatalog())

    decision = await agent.respond("Qual o método de pagamento que vcs tem na troca de iPhone")
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "pix" in reply
    assert "cartao de debito" in reply
    assert "cartao de credito" in reply
    assert "lista completa de produtos" not in reply
    assert "iphone 13 pro max" not in reply


@pytest.mark.asyncio
async def test_debit_fee_question_confirms_no_fee(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("O cartão de débito tem taxa?")
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "cartao de debito" in reply
    assert "sem taxas" in reply


@pytest.mark.asyncio
async def test_credit_only_installment_question_confirms_credit_is_the_only_installment_method(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Vocês parcelam apenas no cartão de crédito?")
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert reply.startswith("sim")
    assert "unica forma de parcelamento" in reply
    assert "cartao de credito" in reply
    assert "18 vezes" in reply
