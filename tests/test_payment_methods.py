from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.adapters.mercado_phone import InventoryCache
from app.agent import AgentService, _normalize
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem
from app.trade_in import TRADE_IN_FORM, is_trade_in_context_request, is_trade_in_request


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


class AvailableCatalog:
    async def search(self, query, limit=3):
        return []

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
async def test_payment_question_after_customer_sends_product_image_uses_payment_faq(tmp_path):
    agent = build_agent(tmp_path)
    text = "Fiquei interessada nesse iPhones qual são a forma de pagamento que vcs aceitam"
    image_description = (
        "Imagem enviada pela cliente com opções de iPhone 14 seminovo: "
        "128 GB Meia-noite por R$ 1.870,00, 128 GB Meia-noite por R$ 1.850,00 "
        "e 128 GB Roxo por R$ 1.870,00."
    )
    history = [
        {"role": "user", "content": "Olá boa tarde"},
        {"role": "assistant", "content": "Olá, boa tarde! 😊 Como posso te ajudar?"},
    ]

    decision = await agent.respond(text, history=history, image_description=image_description)

    assert decision.reply == agent.faq.get("pagamento")
    assert decision.handoff is False
    assert decision.product_references == []
    assert decision.image_urls == []


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
async def test_payment_methods_question_after_sealed_catalog_reply_does_not_repeat_product_list():
    agent = build_agent_with_cache(AvailableCatalog())
    history = [
        {
            "role": "assistant",
            "content": (
                "iPhone 17 Pro Max, 256GB — R$ 8.100,00. Os lacrados por encomenda "
                "têm cores disponíveis conforme o modelo. Qual modelo te interessou?"
            ),
        }
    ]

    decision = await agent.respond(
        "Vocês trabalham com quais modelos de pagamento?",
        history=history,
    )
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "pix" in reply
    assert "dinheiro" in reply
    assert "cartao de debito" in reply
    assert "cartao de credito" in reply
    assert "lista completa de produtos" not in reply
    assert "iphone 13 pro max" not in reply
    assert decision.product_references == []
    assert decision.image_urls == []


@pytest.mark.asyncio
async def test_delivery_payment_question_explains_prepayment_and_available_methods():
    agent = build_agent_with_cache(AvailableCatalog())
    history = [
        {
            "role": "assistant",
            "content": (
                "Enviamos para Curitiba e região por motoboy. Para fora de Curitiba, "
                "enviamos por Sedex. O pagamento deve ser antecipado antes do despacho. "
                "O cartão parcelado é na máquina física. A taxa e o prazo da entrega "
                "precisam ser confirmados com um atendente."
            ),
        }
    ]

    decision = await agent.respond("E como pago a entrega e o celular?", history=history)
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "antecipado antes do despacho" in reply
    assert "nao e possivel pagar na entrega" in reply
    assert "pix" in reply
    assert "dinheiro" in reply
    assert "cartao de debito" in reply
    assert "cartao de credito" in reply
    assert "maquina fisica" in reply
    assert "nao aceitamos pagamento por cartao de credito online" in reply
    assert "taxa" in reply and "atendente" in reply
    assert "bairro" in reply or "cep" in reply
    assert decision.product_references == []
    assert decision.image_urls == []


@pytest.mark.asyncio
async def test_installment_delivery_question_explains_machine_and_no_pay_on_arrival():
    agent = build_agent_with_cache(AvailableCatalog())
    history = [
        {
            "role": "assistant",
            "content": (
                "Parcelamento do iPhone 17 128 GB: 1x de R$ 3.500,00. "
                "Valores para pagamento no cartão de crédito."
            ),
        }
    ]

    decision = await agent.respond(
        "Se for pagar parcelado tem que ser na loja ou vocês entregam e faz o pagamento na entrega?",
        history=history,
    )
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "parcelado" in reply
    assert "maquina fisica" in reply
    assert "antes do despacho" in reply
    assert "nao e possivel pagar na entrega" in reply
    assert "motoboy" in reply
    assert "sedex" in reply


@pytest.mark.asyncio
async def test_delivery_payment_question_recognizes_deliveram_verb_form():
    agent = build_agent_with_cache(AvailableCatalog())

    decision = await agent.respond("Vocês entregam e posso pagar parcelado?")
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "motoboy" in reply
    assert "parcelado" in reply
    assert "maquina fisica" in reply
    assert "antecipado antes do despacho" in reply
    assert "nao e possivel pagar na entrega" in reply


@pytest.mark.asyncio
async def test_delivery_fee_and_installment_question_keeps_attendant_handoff():
    agent = build_agent_with_cache(AvailableCatalog())

    decision = await agent.respond("Qual a taxa do motoboy e posso pagar parcelado?")
    reply = _normalize(decision.reply)

    assert decision.handoff is True
    assert "antecipado antes do despacho" in reply
    assert "nao e possivel pagar na entrega" in reply
    assert "maquina fisica" in reply
    assert "taxa" in reply and "atendente" in reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "Bacacheri, Curitiba",
        "Boa Vista",
        "Curitiba?",
        "82520-060?",
        "Não, moro em Pinhais",
        "Rua das Flores, 123?",
    ],
)
async def test_delivery_neighborhood_reply_after_quote_prompt_hands_off_to_attendant(
    address,
):
    agent = build_agent_with_cache(AvailableCatalog())
    question = "E como pago a entrega e o celular?"
    initial = await agent.respond(question)

    decision = await agent.respond(
        address,
        history=[
            {"role": "user", "content": question},
            {"role": "assistant", "content": initial.reply},
        ],
    )
    reply = _normalize(decision.reply)

    assert decision.handoff is True
    assert "atendente" in reply
    assert "taxa" in reply
    assert "prazo" in reply
    assert "pagamento" in reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        "Sim",
        "Sim, pode ser",
        "Qual é a rua?",
        "Vocês entregam em Curitiba?",
        "Qual bairro de Curitiba?",
        "Fica em Curitiba?",
    ],
)
async def test_delivery_quote_prompt_does_not_treat_non_address_reply_as_location(reply):
    agent = build_agent_with_cache(AvailableCatalog())
    initial = await agent.respond("E como pago a entrega e o celular?")

    decision = await agent.respond(
        reply,
        history=[{"role": "assistant", "content": initial.reply}],
    )

    assert decision.handoff is False


@pytest.mark.asyncio
async def test_neighborhood_without_delivery_quote_prompt_does_not_handoff():
    agent = build_agent_with_cache(AvailableCatalog())

    decision = await agent.respond("Bacacheri, Curitiba")

    assert decision.handoff is False


@pytest.mark.asyncio
async def test_iphone_model_list_question_still_returns_products():
    agent = build_agent_with_cache(AvailableCatalog())

    decision = await agent.respond("Quais modelos de iPhone vocês têm?")
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "lista completa de produtos" in reply
    assert "iphone 13 pro max" in reply


@pytest.mark.asyncio
async def test_short_payment_how_it_works_question_does_not_handoff_as_device_doubt():
    agent = build_agent_with_cache(AvailableCatalog())

    decision = await agent.respond("Pagamento\nComo funciona")
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "pix" in reply
    assert "cartao de debito" in reply
    assert "cartao de credito" in reply
    assert "duvidas sobre esse aparelho" not in reply
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
async def test_payment_methods_answer_explains_variable_credit_card_machine_fee(tmp_path):
    agent = build_agent(tmp_path)

    decision = await agent.respond("Quais são as formas de pagamento?")
    reply = _normalize(decision.reply)

    assert decision.handoff is False
    assert "juros da maquina" in reply
    assert "valor passado no cartao" in reply
    assert "quantidade de parcelas" in reply


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


@pytest.mark.asyncio
async def test_boleto_installment_followup_refuses_simulation_and_explains_policy(tmp_path):
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
            capacity="256 GB",
            category="Celular",
            price_brl=3160.0,
            quantity=1,
            availability="Disponivel para venda",
            condition="seminovo",
            battery_health=90,
            search_text="iphone 13 pro max 256 gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "18x no boleto ?",
        history=[
            {
                "role": "assistant",
                "content": (
                    "Parcelamento do IPHONE 13 PRO MAX 256GB\n"
                    "Preço à vista: R$ 3.160,00\n"
                    "1x de R$ 3.324,57 (total R$ 3.324,57)\n"
                    "18x de R$ 217,27 (total R$ 3.910,89)\n"
                    "Valores calculados para pagamento no cartão de crédito."
                ),
            }
        ],
    )

    reply = _normalize(decision.reply)
    assert decision.handoff is False
    assert "nao parcelamos no boleto" in reply
    assert "cartao de credito" in reply
    assert "maquina fisica" in reply
    assert "1x de" not in decision.reply
    assert "18x de" not in decision.reply


@pytest.mark.asyncio
async def test_literal_boleto_sales_question_after_greeting_returns_payment_policy_without_catalog(
    tmp_path,
):
    agent = build_agent_with_cache(AvailableCatalog())
    history = [
        {"role": "user", "content": "Olá"},
        {"role": "assistant", "content": "Olá! 😊 Como posso te ajudar?"},
    ]

    decision = await agent.respond("Você vende iPhone no boleto?", history=history)

    assert decision.reply == agent.faq.get("pagamento")
    assert decision.handoff is False
    assert decision.product_references == []
    assert decision.image_urls == []
    assert "iphone 13 pro max" not in _normalize(decision.reply)
    assert "nao parcelamos no boleto" in _normalize(decision.reply)


@pytest.mark.asyncio
async def test_boleto_purchase_question_routes_to_payment_policy_not_evaluation(tmp_path):
    agent = build_agent(tmp_path)
    history = [
        {"role": "user", "content": "Opa"},
        {"role": "user", "content": "Boa tarde"},
        {
            "role": "assistant",
            "content": "Opa, boa tarde! 😊 Como posso te ajudar?",
        },
    ]
    text = "Queria ver se aprovava uma compra de um iPhone no boleto"

    decision = await agent.respond(text, history=history)

    reply = _normalize(decision.reply)
    assert decision.reply != TRADE_IN_FORM
    assert decision.handoff is False
    assert decision.product_references == []
    assert decision.image_urls == []
    assert "nao parcelamos no boleto" in reply
    assert "cartao de credito" in reply
    assert is_trade_in_request(text) is False
    assert is_trade_in_context_request(text, history) is False


@pytest.mark.asyncio
async def test_real_iphone_trade_in_remains_evaluation_when_purchase_mentions_boleto(tmp_path):
    agent = build_agent(tmp_path)
    text = (
        "Tenho um iPhone 13 para dar como entrada na compra de um iPhone novo; "
        "posso pagar a diferença no boleto?"
    )

    decision = await agent.respond(text)

    assert decision.reply == TRADE_IN_FORM
    assert decision.handoff is True
    assert decision.product_references == []
    assert decision.image_urls == []


@pytest.mark.asyncio
async def test_store_buyback_question_with_compra_de_still_gets_evaluation_form(tmp_path):
    agent = build_agent(tmp_path)
    text = "Vocês fazem compra de iPhone usado?"

    decision = await agent.respond(text)

    assert is_trade_in_request(text) is True
    assert decision.reply == TRADE_IN_FORM
    assert decision.handoff is True
    assert decision.product_references == []
    assert decision.image_urls == []
