from __future__ import annotations

import pytest

from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore
from app.runtime import build_runtime
from app.trade_in import (
    TRADE_IN_FORM,
    TRADE_IN_NEGOTIATION_REPLY,
    is_trade_in_negotiation,
    is_trade_in_context_request,
    is_trade_in_request,
    is_parts_buyback_request,
    trade_in_em_andamento,
)
from app.adapters.mercado_phone import InventoryCache


def test_trade_in_detector_matches_part_payment_and_avoids_unrelated_exchange():
    assert is_trade_in_request("Vocês aceitam meu iPhone como parte do pagamento?")
    assert is_trade_in_request("Posso dar meu celular de entrada?")
    assert is_trade_in_request("Quero avaliação do meu aparelho usado")
    assert not is_trade_in_request("Quero trocar a película do meu iPhone")


@pytest.mark.parametrize(
    "text",
    [
        "Tenho iPhone 13\nE Samsung S25FE\nPegaria eles na jogada, pelo lacrado?",
        "tem Iphone 15? se sim, pegaria na jogada outros telefones?\nTenho iPhone 13",
    ],
)
@pytest.mark.asyncio
async def test_jogada_device_offer_sends_evaluation_form(tmp_path, text):
    class EmptyMercadoClient:
        async def fetch_all_inventory(self):
            return []

    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(
            EmptyMercadoClient(),
            settings,
            cache_path=tmp_path / "inventory.json",
        ),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(text)

    assert is_trade_in_request(text) is True
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


def test_bare_model_trade_in_guard_does_not_capture_non_apple_device():
    assert is_trade_in_request("Xiaomi 13 Pro para troca, 90% bateria, com caixa") is False


def test_parts_buyback_detector_does_not_capture_customer_purchase_of_a_part():
    assert is_parts_buyback_request("Quero comprar uma tela para meu iPhone") is False


@pytest.mark.parametrize(
    "text",
    [
        "Vocês compram peças de iPhone?",
        "A loja compra tela de iPhone?",
    ],
)
def test_parts_buyback_detector_keeps_part_as_the_buyback_target(text):
    assert is_parts_buyback_request(text) is True


def test_parts_buyback_detector_ignores_part_details_in_upgrade_request():
    text = (
        "Olá, tudo bem? 😊 Gostaria de consultar a possibilidade de fazer um upgrade para o "
        "iPhone 17 Pro Max, 256 GB, na cor laranja-cósmico. Tenho um iPhone 16 Pro Max, "
        "256 GB, branco, em estado impecável, sou a única dona. O aparelho está com 90% "
        "de saúde da bateria e possui película na parte frontal, traseira e nas lentes das "
        "câmeras. Gostaria de saber quanto vocês avaliam o meu aparelho na troca e qual seria "
        "a diferença a pagar no upgrade. Obrigada! 😊"
    )

    assert is_trade_in_request(text) is True
    assert is_parts_buyback_request(text) is False


def test_parts_buyback_detector_ignores_complete_device_details_after_buyback_question():
    text = (
        "Queria saber se vcs pegam iPhone 17\n"
        "17 com caixa 2 meses de uso\n"
        "256gb 100% bateria\n"
        "Impecável\n"
        "Quanto vcs pagam?"
    )

    assert is_trade_in_request(text) is True
    assert is_parts_buyback_request(text) is False


def test_credit_limit_is_not_a_device_sale_offer():
    assert is_trade_in_request("To vendo meu limite") is False


def test_catalog_price_recall_is_not_a_device_sale_offer():
    text = "Minha irmã estava vendo um celular contigo, não lembro o número, era 1200"

    assert is_trade_in_request(text) is False


def test_new_phone_payment_split_is_not_trade_in():
    text = (
        "Tenho interesse no iPhone 17 Pro Max. Fazer uma parte do pagamento "
        "a vista e parcelar o restante no cartao."
    )
    history = [
        {"role": "user", "content": "Quanto esta o iPhone 17 Pro Max?"},
        {"role": "assistant", "content": "iPhone 17 Pro Max 256 GB: R$ 7.800"},
    ]

    assert is_trade_in_request(text) is False
    assert is_trade_in_context_request(text, history) is False


@pytest.mark.parametrize(
    "text",
    [
        "quero comprar um iphone",
        "tem iphone usado?",
        "tem usado?",
        "vou dar 2000 de entrada",
        "a entrada vai ser em dinheiro e o restante no pix",
        "voces aceitam cartao?",
        "voces compram da Apple?",
        "voces compram Samsung?",
        "nao quero trocar, so comprar",
        "meu celular foi roubado, preciso comprar um novo",
        "buscar na loja",
    ],
)
def test_trade_in_guard_does_not_capture_purchase_stock_or_payment(text):
    assert is_trade_in_request(text) is False


@pytest.mark.asyncio
async def test_non_apple_exchange_question_returns_policy_reply_without_form(tmp_path):
    class EmptyMercadoClient:
        async def fetch_all_inventory(self):
            return []

    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(
            EmptyMercadoClient(),
            settings,
            cache_path=tmp_path / "inventory.json",
        ),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond("Gostaria de saber se você pega Samsung na troca?")

    assert decision.handoff is False
    assert "somente produtos da apple" in decision.reply.lower()
    assert "lista de avaliação" not in decision.reply.lower()


@pytest.mark.parametrize(
    "text",
    [
        "voces compram algum produto Apple?",
        "a loja compra celular usado?",
        "vc pegaria ele ainda como forma de pagamento?",
        "da pra usar ele de entrada?",
        "quero comprar um 15 novo e dar meu celular como entrada",
        "quero vender meu iphone 13",
        "tenho um iPhone 11 Pro Max para vender",
        "estou vendendo meu iPhone 11 Pro Max",
    ],
)
def test_trade_in_guard_keeps_buyback_and_device_entry_requests(text):
    assert is_trade_in_request(text) is True


def test_trade_in_history_marker_only_counts_assistant_form():
    assert trade_in_em_andamento(
        [{"role": "assistant", "content": TRADE_IN_FORM}]
    ) is True
    assert trade_in_em_andamento(
        [{"role": "user", "content": TRADE_IN_FORM}]
    ) is False
    assert is_trade_in_negotiation("vamos fechar R$ 400") is True
    assert is_trade_in_negotiation("buscar na loja") is False


@pytest.mark.asyncio
async def test_trade_in_negotiation_after_form_is_handed_off_without_price(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(
        "vamos fechar R$ 400",
        history=[{"role": "assistant", "content": TRADE_IN_FORM}],
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_NEGOTIATION_REPLY
    assert "400" not in decision.reply


@pytest.mark.asyncio
async def test_trade_in_response_is_form_and_handoff(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond("Vocês pegam meu iPhone como parte do pagamento?")

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM
    assert "Qual modelo de iPhone?" in decision.reply
    assert "Saúde da bateria" in decision.reply


@pytest.mark.asyncio
async def test_abbreviated_pegm_trade_in_question_returns_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(
        "Oii qual valor vcs pegM na troca, iPhone 13 pro max, 128gb 83% bateria?"
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_abbreviated_exchange_model_with_condition_details_returns_evaluation_form(tmp_path):
    class EmptyMercadoClient:
        async def fetch_all_inventory(self):
            return []

    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(
            EmptyMercadoClient(),
            settings,
            cache_path=tmp_path / "inventory.json",
        ),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(
        "Olá!\n"
        "17 pro max, qual valor e condições de pagamento?\n"
        "13 Pro max para troca. Comprado novo. Sem manutenção, nunca aberto, "
        "sem defeitos, 128gb, 78% Saúde bateria, com caixa, grafite.",
        image_description="Descrição visual da imagem recebida: iPhone 13 Pro Max seminovo",
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_batched_exchange_battery_detail_returns_evaluation_form(tmp_path):
    class EmptyMercadoClient:
        async def fetch_all_inventory(self):
            return []

    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(
            EmptyMercadoClient(),
            settings,
            cache_path=tmp_path / "inventory.json",
        ),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(
        "iPhone 14\n128 Hb\nNa troca\nBonito\n78 de bateria",
        history=[
            {
                "role": "assistant",
                "content": "Valores para pagamento no cartão de crédito pela máquina física.",
            }
        ],
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_compact_exchange_offer_with_bare_model_and_battery_returns_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )
    text = "Boa tarde, vc aceita na troca um 17 256, 93% de bateria"

    decision = await service.respond(text)

    assert is_trade_in_request(text) is True
    assert is_parts_buyback_request(text) is False
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_implicit_owned_iphone_upgrade_offer_sends_evaluation_form(tmp_path):
    class EmptyMercadoClient:
        async def fetch_all_inventory(self):
            return []

    text = (
        "Olá tudo bem tenho um 12 normal preto 128gb todo original só a bateria trocada "
        "tela original fece id funciona perfeitamente, queria o 15 pró ou o 16 pró ou o 17 pró "
        "teria essas opções pra dia 08"
    )
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(
            EmptyMercadoClient(),
            settings,
            cache_path=tmp_path / "inventory.json",
        ),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    assert is_trade_in_request(text) is True
    assert is_parts_buyback_request(text) is False

    decision = await service.respond(text)

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_batched_16_pro_entry_offer_with_battery_returns_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )
    text = "Você aceitam 16 pro 91% bateria Preto zerado\nEntrada"

    decision = await service.respond(
        text,
        history=[
            {"role": "user", "content": "Qual valor do iPhone 17 pro?"},
            {
                "role": "assistant",
                "content": (
                    "Sim 😊 Encontrei estas opções de iPhone 17 Pro disponíveis:\n"
                    "• iPhone 17 Pro — Laranja-cósmico | Azul-intenso | Prateado — "
                    "512 GB — NOVO LACRADO"
                ),
            },
        ],
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_part_payment_offer_with_bare_iphone_model_and_battery_health_returns_evaluation_form(tmp_path):
    class EmptyMercadoClient:
        async def fetch_all_inventory(self):
            return []

    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(
            EmptyMercadoClient(),
            settings,
            cache_path=tmp_path / "inventory.json",
        ),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )
    text = (
        "Vocês trabalham com iPhone como parte do pagamento? "
        "Tenho um 13 com 85% de saúde de bateria. Original, nunca aberto, comprei novo"
    )

    decision = await service.respond(
        text,
        history=[
            {"role": "user", "content": "Bom dia"},
            {"role": "assistant", "content": "Bom dia! 😊 Como posso ajudar?"},
        ],
    )

    assert is_trade_in_request(text) is True
    assert is_parts_buyback_request(text) is False
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_device_offer_with_no_parts_or_damage_returns_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )
    text = "Aceita um iPhone 14 azul claro, 82% de bateria sem troca de peças nem avaria."

    decision = await service.respond(text)

    assert is_trade_in_request(text) is True
    assert is_parts_buyback_request(text) is False
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM
    assert "assistência técnica" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_upgrade_request_with_battery_and_camera_details_returns_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(
        "Olá, tudo bem? 😊 Gostaria de consultar a possibilidade de fazer um upgrade para o "
        "iPhone 17 Pro Max, 256 GB, na cor laranja-cósmico. Tenho um iPhone 16 Pro Max, "
        "256 GB, branco, em estado impecável, sou a única dona. O aparelho está com 90% "
        "de saúde da bateria e possui película na parte frontal, traseira e nas lentes das "
        "câmeras. Gostaria de saber quanto vocês avaliam o meu aparelho na troca e qual seria "
        "a diferença a pagar no upgrade. Obrigada! 😊"
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_two_iphone_buyback_listing_is_not_classified_as_parts(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(
        "Bom dia estou vendendo 2 iphones, vocês compram para revenda?\n"
        "15 128gb preto - sem nenhum detalhe, bateria 100% original e garantia apple até 31/11/2026 - possui caixinha\n"
        "14 128gb azul - unico detalhe é um pequeno trinco parte inferior esquerda traseira, bateria 79% - não possui caixinha"
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM
    assert "Não compramos peças avulsas" not in decision.reply


@pytest.mark.asyncio
async def test_complete_device_offer_with_photo_returns_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )
    text = (
        "Queria saber se vcs pegam iPhone 17\n"
        "17 com caixa 2 meses de uso\n"
        "256gb 100% bateria\n"
        "Impecável\n"
        "Quanto vcs pagam?"
    )

    decision = await service.respond(
        text,
        image_description="Foto de um iPhone 17 completo dentro da caixa.",
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM
    assert "Não compramos peças avulsas" not in decision.reply


@pytest.mark.asyncio
async def test_grouped_tem_interesse_offer_returns_evaluation_form(tmp_path):
    class EmptyMercadoClient:
        async def fetch_all_inventory(self):
            return []

    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(
            EmptyMercadoClient(),
            settings,
            cache_path=tmp_path / "inventory.json",
        ),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(
        "Boa noite\n"
        "Tem interesse em comprar um iPhone 16 e 256gb\n"
        "Tá na garantia ainda\n"
        "Comprei dia 30 de novembro 2025 está 99% de saúde da bateria\n"
        "Não tem nenhum detalhe não tem nenhum defeito\n"
        "Está com película de vidro e capinha\n"
        "Face ID funciona td top o celular\n"
        "Tem caixa e o negócio de tira o chip\n"
        "Nunca foi trocado peça nada\n"
        "Qual valor paga?",
        image_description="Descrição visual da imagem recebida: fotos de um iPhone 16.",
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_processor_pauses_trade_in_conversation_after_sending_form():
    settings = Settings(
        database_url="sqlite:///:memory:",
        openai_api_key=None,
        outbound_mode="disabled",
    )
    runtime = build_runtime(settings, offline=True)
    try:
        await runtime.processor.process_payload(
            {
                "messageId": "trade-1",
                "phone": "5511999999999",
                "text": {"message": "Aceita meu celular como parte do pagamento?"},
            }
        )
        conversation = runtime.repository.get_conversation("5511999999999")
        messages = runtime.repository.recent_messages("5511999999999")
    finally:
        await runtime.aclose()

    assert conversation is not None and conversation.status == "human_pending"
    assert any(item["role"] == "assistant" and "Qual modelo de iPhone?" in item["content"] for item in messages)


def test_trade_in_context_detector_matches_abbreviated_model_followup():
    history = [
        {"role": "user", "content": "Quanto esta o iPhone 17 Pro Max?"},
        {
            "role": "assistant",
            "content": "iPhone 17 Pro Max por R$ 7.800. Qual capacidade voce deseja?",
        },
    ]

    assert is_trade_in_context_request("Tenho 14 quantos sera ficaria dai?", history)
    assert not is_trade_in_context_request("Tenho 14 anos, quanto falta?", history)


@pytest.mark.asyncio
async def test_trade_in_context_followup_returns_form_before_handoff(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(
        "Tenho 14 quantos sera ficaria dai?",
        history=[
            {"role": "user", "content": "Quanto esta o iPhone 17 Pro Max?"},
            {
                "role": "assistant",
                "content": "iPhone 17 Pro Max por R$ 7.800. Qual capacidade voce deseja?",
            },
        ],
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_trade_in_confirmation_returns_form_before_handoff(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(
        "Ok",
        history=[
            {
                "role": "assistant",
                "content": (
                    "Voce quer dar seu iPhone 14 como parte do pagamento? "
                    "A avaliacao depende do estado. Vou encaminhar para um atendente."
                ),
            }
        ],
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_owned_iphone_for_sale_returns_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond("Tenho um iPhone 11 Pro Max para vender.")

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM
    assert "Qual modelo de iPhone?" in decision.reply


@pytest.mark.asyncio
async def test_discount_for_delivering_old_phone_sends_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )
    text = "Vocês dão desconto se entregar o celular antigo?"
    history = [
        {
            "role": "assistant",
            "content": (
                "iPhone 17 - 256 GB - NOVO LACRADO - R$ 5.600,00\n"
                "iPhone 17 Air - 256 GB - NOVO LACRADO - R$ 5.800,00\n"
                "iPhone 17 Pro - 256 GB - NOVO LACRADO - R$ 6.900,00\n"
                "iPhone 17 Pro Max - 1 TB - NOVO LACRADO - R$ 10.300,00"
            ),
        }
    ]

    assert is_trade_in_request(text) is True
    assert is_parts_buyback_request(text) is False

    decision = await service.respond(text, history=history)

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_trade_in_offer_with_screen_condition_returns_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(
        "Bom dia, tudo bem? Eu gostaria de saber se vocês tem: "
        "Iphone 14 ou 15 PRO; Saúde da bateria + de 95%. "
        "E também gostaria de saber se vocês aceitam iphone na volta: "
        "Iphone 11, 128gb, Saúde da bateria 68%, Cor preta. "
        "Obs: tela com pequenos riscos, e parte de trás com um leve trincado."
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_parts_buyback_question_declines_parts_without_evaluation_form(tmp_path):
    class EmptyMercadoClient:
        async def fetch_all_inventory(self):
            return []

    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(EmptyMercadoClient(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond("Compram peças?")

    assert decision.handoff is False
    assert decision.reply == (
        "Não compramos peças avulsas. Compramos somente produtos completos da Apple, "
        "mediante avaliação."
    )
    assert "forms.gle" not in decision.reply
    assert "lista de avaliação" not in decision.reply.lower()
