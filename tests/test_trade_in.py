from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore
from app.runtime import build_runtime
from app.schemas import InventoryItem
from app.trade_in import (
    TRADE_IN_FORM,
    TRADE_IN_NEGOTIATION_REPLY,
    is_completed_trade_in_form,
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


def test_purchase_context_keeps_owned_iphone_buyback_offer():
    assert is_trade_in_request("Vocês compram meu iPhone pra compra de um novo?")


@pytest.mark.asyncio
async def test_owned_iphone_buyback_purchase_context_sends_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond("Vocês compram meu iPhone pra compra de um novo?")

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


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
        "A loja compra a bateria do telefone antigo?",
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


@pytest.mark.asyncio
async def test_owned_iphone_battery_replacement_context_sends_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )
    text = (
        "Luan tenho o meu celular iPhone 14 de 256gb e gostaria de saber se você compra, "
        "a bateria tem que trocar pq usei muuuuito pra trabalhar e sabe como é, acho que tá em 82% só. "
        "Mas dependendo do valor que tu me pagar vale a pena trocar pra vender"
    )

    decision = await service.respond(text)

    assert is_parts_buyback_request(text) is False
    assert is_trade_in_request(text) is True
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM
    assert "assistência técnica" not in decision.reply.lower()


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


@pytest.mark.asyncio
async def test_literal_semi_purchase_does_not_open_trade_in_form(tmp_path):
    settings = Settings(
        openai_api_key=None,
        google_sheets_enabled=False,
        faq_path=str(tmp_path / "faq.yaml"),
    )
    cache = StoreCatalogCache(
        object(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="used:iphone-15-semi",
            name="iPhone 15",
            category="Celular",
            capacity="128 GB",
            color="PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=2830,
            battery_health=90,
            search_text="iphone 15 128 gb preto celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    service = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    text = "O Vinicius me indicou vocês pra compra de um iPhone semi"
    history = [
        {"role": "user", "content": "Olá boa tarde"},
        {"role": "assistant", "content": "Olá, boa tarde! 😊 Como posso ajudar?"},
    ]

    assert is_trade_in_request(text) is False
    assert is_trade_in_context_request(text, history) is False

    decision = await service.respond(text, history=history)

    assert decision.handoff is False
    assert decision.product_references == ["used:iphone-15-semi"]
    assert "lista de avaliação" not in decision.reply.lower()
    assert "iPhone 15" in decision.reply
    assert "SEMINOVO" in decision.reply


@pytest.mark.asyncio
async def test_voice_catalog_purchase_observation_does_not_open_trade_in_form(tmp_path):
    class EmptyMercadoClient:
        async def fetch_all_inventory(self):
            return []

    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-13-branco-128",
            name="iPhone 13",
            category="Celular",
            capacity="128 GB",
            color="BRANCO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=1740,
            source="mercado_phone",
            search_text="iphone 13 branco 128gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    service = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    text = (
        "Oi, boa tarde. Então, eu gostei do iPhone, sim, o iPhone 13, mas eu não vou poder "
        "comprar ele agora, esse mês, só pra mês que vem, que daí eu vou presentear ele pra "
        "minha irmã. Mas daí, se por acaso, né, até lá vender esse iPhone 13 e eu ver que, né, "
        "teve um 12 Pro disponível ainda, ou, né, se repor outro 13, eu compro. Mas eu tava "
        "dando uma olhadinha só."
    )
    history = [
        {"role": "user", "content": "Boa tarde! o Iphone 13 - Branco de 1740 ainda está disponível ?"},
        {
            "role": "assistant",
            "content": "Sim, o iPhone 13 Branco por R$ 1.740,00 ainda está disponível.",
        },
    ]

    assert is_trade_in_request(text) is False
    assert is_trade_in_context_request(text, history) is False

    decision = await service.respond(text, history=history)

    assert decision.handoff is False
    assert decision.product_references == ["iphone-13-branco-128"]
    assert "lista de avaliação" not in decision.reply.lower()
    assert "iPhone 13" in decision.reply
    assert "R$ 1.740,00" in decision.reply


@pytest.mark.asyncio
async def test_story_interest_in_iphone_16_pro_max_uses_catalog_not_trade_in_form(tmp_path):
    class EmptyMercadoClient:
        async def fetch_all_inventory(self):
            return []

    settings = Settings(
        openai_api_key=None,
        google_sheets_enabled=False,
        faq_path=str(tmp_path / "faq.yaml"),
    )
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    cache.items = [
        InventoryItem(
            external_id="iphone-16-pro-max-256",
            name="iPhone 16 Pro Max",
            category="Celular",
            capacity="256 GB",
            color="TITÂNIO PRETO",
            condition="SEMINOVO",
            availability="Disponível para venda",
            quantity=1,
            price_brl=5200,
            battery_health=100,
            search_text="iphone 16 pro max 256 gb titanio preto celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    service = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    text = "estava vendo o story, me interessei no iphone 16 pro max 256gb"
    history = [
        {"role": "user", "content": "boa tarde, tudo bem?"},
        {
            "role": "assistant",
            "content": "Boa tarde! Tudo bem, e com você? Como posso ajudar? 😊",
        },
        {"role": "user", "content": "estou bem também"},
        {"role": "assistant", "content": "Que bom! 😊 Como posso ajudar você hoje?"},
    ]

    decision = await service.respond(text, history=history)

    assert is_trade_in_request(text) is False
    assert is_trade_in_context_request(text, history) is False
    assert decision.handoff is False
    assert decision.product_references == ["iphone-16-pro-max-256"]
    assert "lista de avaliação" not in decision.reply.lower()
    assert "iPhone 16 Pro Max" in decision.reply
    assert "256 GB" in decision.reply
    assert "R$ 5.200,00" in decision.reply


@pytest.mark.parametrize(
    "text",
    [
        "quero comprar um iphone",
        "quero comprar um iphone usado",
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
        "vendo meu iphone 11 pro max",
        "tenho um iPhone 11 Pro Max para vender",
        "estou vendendo meu iPhone 11 Pro Max",
    ],
)
def test_trade_in_guard_keeps_buyback_and_device_entry_requests(text):
    assert is_trade_in_request(text) is True


@pytest.mark.asyncio
async def test_malformed_store_buyback_question_sends_evaluation_form(tmp_path):
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
    text = "Vocês comprar iPhone usados aí ?"
    history = [
        {"role": "user", "content": "Bom dia"},
        {
            "role": "assistant",
            "content": (
                "Cwb.iphones agradece seu contato. Como podemos ajudar?\n\n"
                "Estamos em recesso até o dia 09/09, retornamos ao normal dia 10/09. "
                "Agradecemos a compreensão."
            ),
        },
    ]

    decision = await service.respond(text, history=history)

    assert is_trade_in_request(text) is True
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


def test_trade_in_history_marker_only_counts_assistant_form():
    assert trade_in_em_andamento(
        [{"role": "assistant", "content": TRADE_IN_FORM}]
    ) is True
    assert trade_in_em_andamento(
        [{"role": "user", "content": TRADE_IN_FORM}]
    ) is False
    assert is_trade_in_negotiation("vamos fechar R$ 400") is True
    assert is_trade_in_negotiation("buscar na loja") is False


def test_completed_trade_in_form_requires_non_empty_answers():
    assert is_completed_trade_in_form(TRADE_IN_FORM) is False
    assert is_completed_trade_in_form(
        "Marcas de uso?\nR: nao\n"
        "Riscos na tela?\nR: nao\n"
        "Tem algo quebrado/defeituoso?\nR: nao\n"
        "Qual a % da Saúde da bateria?\nR: 71%"
    ) is True


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
async def test_filled_evaluation_form_after_bot_form_is_forwarded_to_attendant(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    filled_form = (
        "R: nao\n"
        "Marcas de uso?\n"
        "R: nao\n"
        "Riscos na tela?\n"
        "R: nao\n"
        "Tem algo quebrado/defeituoso?\n"
        "(Mesmo que simples informar qualquer anormalidade)\n"
        "R: nao\n"
        "Possui algo que já foi trocado ou feito algum reparo?\n"
        "R: nao\n"
        "É desbloqueado chip todas operadoras?\n"
        "R: sim\n"
        "Qual a % da Saúde da bateria? (Veja em Ajustes > Bateria > Saúde da bateria)\n"
        "R: 71%\n"
        "Valor que pretende no seu usado? (Lembrando que precisamos de margem para revenda)\n"
        "R: R$ 3.000,00\n"
        "Ainda tem garantia Apple? Se sim, quanto?\n"
        "R:\n"
        "Se puder mandar fotos agradecemos.\n"
        "Com essas informações já podemos fazer uma avaliação prévia!!\n"
        "Só avaliamos produtos da marca Apple. Após preencher, vou encaminhar o atendimento para um atendente concluir a avaliação."
    )

    decision = await service.respond(
        filled_form,
        history=[{"role": "assistant", "content": TRADE_IN_FORM}],
    )

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_NEGOTIATION_REPLY
    assert "assistência técnica" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_repair_after_evaluation_form_remains_technical_assistance(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )

    decision = await service.respond(
        "Quero trocar a bateria do meu iPhone.",
        history=[{"role": "assistant", "content": TRADE_IN_FORM}],
    )

    assert decision.handoff is True
    assert "assistência técnica" in decision.reply.lower()


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
async def test_plural_used_phone_buyback_after_catalog_reply_sends_evaluation_form(tmp_path):
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
    text = "Vocês compram celulares usados?"

    decision = await service.respond(
        text,
        history=[
            {"role": "user", "content": "Gostaria de saber o valor do iPhone 17 roxo 256gb"},
            {
                "role": "assistant",
                "content": (
                    "Sim 😊 Encontrei estas opções de iPhone 17 disponíveis: "
                    "iPhone 17 Lavanda 256 GB NOVO LACRADO por R$ 5.600,00."
                ),
            },
        ],
    )

    assert is_trade_in_request(text) is True
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_owned_iphone_entry_difference_after_catalog_reply_sends_evaluation_form(tmp_path):
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
    text = (
        "Dando o meu 13 pro max de 128gb dourado, bateria 78%, sem marcas de uso, "
        "sem peças trocadas. Quanto eu teria que dar na volta?"
    )
    history = [
        {
            "role": "user",
            "content": "Boa noite, você tem o iPhone 16 Plus rosa de 256gb disponível?",
        },
        {
            "role": "assistant",
            "content": "16 plus 256gb rosa seminovo: 4.100,00\nO que acha?",
        },
    ]

    decision = await service.respond(text, history=history)

    assert is_parts_buyback_request(text) is False
    assert is_trade_in_request(text) is True
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_progressive_used_iphone_buyback_question_sends_evaluation_form(tmp_path):
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
    text = "Qual é a média que vcs estão pegando os iPhones 15 pro usados?"

    decision = await service.respond(text)

    assert is_parts_buyback_request(text) is False
    assert is_trade_in_request(text) is True
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


def test_progressive_parts_buyback_question_stays_out_of_evaluation_form():
    text = "Vocês estão pegando bateria usada?"

    assert is_parts_buyback_request(text) is True
    assert is_trade_in_request(text) is False


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
async def test_batched_buyback_price_and_upgrade_request_sends_evaluation_form(tmp_path):
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
        "Gostaria de verificar o preço que vcs pagam em um\n"
        "iPhone 12\n"
        "Cor branca\n"
        "64 gb\n"
        "76% de bateria\n"
        "Queria trocar por iPhone 13 com 128gb"
    )

    assert is_trade_in_request(text) is True
    assert is_parts_buyback_request(text) is False

    decision = await service.respond(text)

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM
    assert "assistência técnica" not in decision.reply.lower()


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
async def test_detailed_owned_iphone_profile_without_exchange_words_sends_evaluation_form(tmp_path):
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
        "Tenho um iPhone 13 promax, 128gb, 88 de saúde de bateria, na cor dourada "
        "e sem avarias ou marcas de uso."
    )

    decision = await service.respond(
        text,
        history=[
            {"role": "user", "content": "Olá, tudo bem?"},
            {
                "role": "assistant",
                "content": "Cwb.iphones agradece seu contato. Como podemos ajudar?",
            },
            {"role": "assistant", "content": "Olá! Tudo bem? 😊 Como posso ajudar?"},
        ],
    )

    assert is_trade_in_request(text) is True
    assert is_parts_buyback_request(text) is False
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_owned_iphone_price_question_sends_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )
    text = (
        "Deixa eu te perguntar, quanto vocês estão pagando pelo IPhone 15 pro 128gb "
        "com a saúde da bateria 86%, tudo original sem nenhum defeito?"
    )
    history = [
        {"role": "user", "content": "Oi! Boa tarde, tudo bem?"},
        {
            "role": "assistant",
            "content": "Oi! Boa tarde 😊 Tudo bem, e com você? Como posso ajudar?",
        },
        {"role": "user", "content": "Tudo bem também!"},
        {"role": "assistant", "content": "Que bom! 😊 Como posso te ajudar hoje?"},
    ]

    decision = await service.respond(text, history=history)

    assert is_trade_in_request(text) is True
    assert is_parts_buyback_request(text) is False
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM
    assert "assistência técnica" not in decision.reply.lower()


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
async def test_part_payment_offer_with_replaced_battery_returns_evaluation_form(tmp_path):
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
        "Estou pensando em trocar de telefone e gostaria de saber se vocês pegam o telefone antigo "
        "como parte de pagamento, vou te mandar as especificações\n"
        "iPhone 12 Pro, 128Gb\n"
        "Trocado somente a bateria esse ano"
    )

    decision = await service.respond(
        text,
        history=[
            {"role": "user", "content": "Olá, bom dia"},
            {"role": "assistant", "content": "Olá, bom dia! 😊 Como posso ajudar?"},
        ],
    )

    assert is_parts_buyback_request(text) is False
    assert is_trade_in_request(text) is True
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_contextual_device_offer_after_catalog_reply_sends_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    service = AgentService(
        InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json"),
        FAQStore(settings.faq_file),
        settings,
        offline=True,
    )
    device_details = (
        "Então eu tenho um iPhone 16 128 nunca aberto sem estrico de peça "
        "só a tampa traseira quebrada em t caixa tudo"
    )
    current_message = "Queria ver se n pegava ele e me dava uma volta"
    history = [
        {
            "role": "assistant",
            "content": (
                "Temos iPhone 14 Pro Max seminovos disponíveis:\n"
                "• 128GB, preto espacial, bateria 85% — R$ 3.450\n"
                "• 256GB, preto espacial, bateria 90% — R$ 3.600"
            ),
        },
        {"role": "user", "content": device_details},
    ]
    image_description = (
        "Descrição visual da imagem recebida: fotos de um iPhone 16 azul completo, "
        "com a tampa traseira quebrada."
    )

    decision = await service.respond(
        current_message,
        history=history,
        image_description=image_description,
    )

    assert is_trade_in_context_request(
        f"{current_message} {image_description}", history
    ) is True
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM
    assert "assistência técnica" not in decision.reply.lower()


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
async def test_repassar_complete_iphone_with_battery_photo_sends_evaluation_form(tmp_path):
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
        "Quero repassar meu iPhone 15\n"
        "Tá zerado original nunca troquei nenhuma peça! Sem arranhão ou quebrado.\n"
        "Ele é verde 126g"
    )
    image_description = (
        "Descrição visual da imagem recebida: tela de saúde da bateria do iPhone com "
        "capacidade máxima de 87%, 864 ciclos, fabricação em outubro de 2023 e "
        "primeiro uso em julho de 2024."
    )
    request_context = f"{text} {image_description}"

    assert is_trade_in_request(request_context) is True
    assert is_parts_buyback_request(request_context) is False

    decision = await service.respond(text, image_description=image_description)

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM
    assert "assistência técnica" not in decision.reply.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("separate_messages", [True, False])
async def test_owned_iphone_trade_down_to_smaller_model_sends_evaluation_form(
    tmp_path, separate_messages
):
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
    if separate_messages:
        history = [
            {"role": "user", "content": "Olá"},
            {"role": "user", "content": "Gostaria de saber uma informação"},
            {"role": "user", "content": "Tenho um 16 pro max"},
            {"role": "user", "content": "Novo praticamente"},
            {"role": "user", "content": "Gostaria de troca em um menor"},
        ]
        text = "Faz isso?"
    else:
        history = None
        text = (
            "Olá\nGostaria de saber uma informação\nTenho um 16 pro max\n"
            "Novo praticamente\nGostaria de troca em um menor\nFaz isso?"
        )

    decision = await service.respond(text, history=history)

    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM
    assert decision.product_references == []


def test_smaller_model_catalog_question_without_owned_device_stays_out_of_trade_in():
    assert is_trade_in_request("Gostaria de trocar por um modelo menor") is False


@pytest.mark.asyncio
async def test_owned_iphone_exchange_for_newer_model_returns_evaluation_form(tmp_path):
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
    text = "Olá, tudo bem? Me chamo Raweny, tenho um iPhone 13 e queria trocar por um mais novo"

    decision = await service.respond(text)

    assert is_trade_in_request(text) is True
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


@pytest.mark.asyncio
async def test_owned_iphone_12_orcamento_for_16_pro_returns_evaluation_form(tmp_path):
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
    text = (
        "Bom dia, tudo bem?\n"
        "Estou fazendo alguns orçamentos para trocar meu celular\n"
        "Tenho um iPhone 12 hoje, penso em trocar para um 16 Pro"
    )

    decision = await service.respond(text)

    assert is_trade_in_request(text) is True
    assert is_parts_buyback_request(text) is False
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM
    assert decision.product_references == []


@pytest.mark.asyncio
async def test_owned_iphone_15_pro_upgrade_to_17_pro_max_sends_evaluation_form(tmp_path):
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
    text = "Eu gostaria de ver quanto fica pra trocar o meu iPhone 15 pro bo 17 pro Max"

    decision = await service.respond(text)

    assert is_trade_in_request(text) is True
    assert decision.handoff is True
    assert decision.reply == TRADE_IN_FORM


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
