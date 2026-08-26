from __future__ import annotations

import pytest

from app.adapters.mercado_phone import InventoryCache
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


@pytest.mark.asyncio
async def test_battery_replacement_question_is_answered_and_forwarded(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("quanto vcs cobram para trocar a bateria do iphone 11 64g?")

    assert decision.handoff is True
    assert "assistência técnica" in decision.reply.lower()
    assert "atendente" in decision.reply.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "Vocês fazem assistência técnica?",
        "Vocês fazem conserto de iPhone?",
        "Vocês trocam a tela do iPhone 13?",
        "Meu iPhone está com a tela trincada, vocês consertam?",
        "Quero trocar a bateria do meu iPhone 11.",
    ],
)
async def test_other_repair_questions_are_also_forwarded(tmp_path, question):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(question)

    assert decision.handoff is True
    assert "assistência técnica" in decision.reply.lower()
    assert "atendente" in decision.reply.lower()


@pytest.mark.asyncio
async def test_battery_health_question_is_not_treated_as_repair(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Qual a saúde da bateria do iPhone 11?")

    assert decision.handoff is False


@pytest.mark.asyncio
async def test_screen_originality_question_is_forwarded_without_technical_assistance(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Já foi trocada a tela dele?\nOu está tudo original?",
        history=[
            {"role": "user", "content": "Para hoje não"},
            {
                "role": "assistant",
                "content": "Tudo bem. Quando quiser visitar, me informe o dia e o horário.",
            },
            {"role": "user", "content": "Mais vamos conversar"},
            {
                "role": "assistant",
                "content": "Claro! 😊 Podemos conversar sim. O que você gostaria de saber sobre o iPhone 12?",
            },
        ],
    )

    assert decision.handoff is True
    assert "atendente" in decision.reply.lower()
    assert "assistência técnica" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_catalog_buyer_condition_questions_are_forwarded_as_product_questions(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Oi! Tenho interesse em comprar esse celular. Antes de fechar, queria confirmar "
        "algumas coisas, por favor. A bateria é original? A tela ou alguma outra peça já "
        "foi trocada? Se sim, quais peças? O Face ID, câmeras, alto-falante, microfone, "
        "carregamento e todos os botões estão funcionando normalmente? O celular já teve "
        "algum problema ou passou por manutenção? E ele está sem bloqueio de iCloud e sem "
        "nenhuma restrição? Se puder me mandar fotos dessas informações do aparelho, eu "
        "agradeço!",
        history=[
            {"role": "user", "content": "Esse mesmo"},
            {
                "role": "assistant",
                "content": (
                    "Perfeito 😊 O iPhone 15 Pro 128GB, titânio azul, seminovo, com 93% "
                    "de bateria, está disponível por R$ 3.460,00. Deseja saber sobre "
                    "parcelamento ou agendar uma visita?"
                ),
            },
        ],
    )

    assert decision.handoff is True
    assert "atendente" in decision.reply.lower()
    assert "dúvida" in decision.reply.lower()
    assert "assistência técnica" not in decision.reply.lower()
    assert "não compramos peças" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_catalog_battery_replacement_followup_is_forwarded_as_product_question(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Algum desses teve a bateria substituída?",
        history=[
            {"role": "user", "content": "Pq eles são usados e estão com 100% de bateria?"},
            {
                "role": "assistant",
                "content": (
                    "Porque ‘seminovo’ significa que o aparelho já teve uso, mas alguns podem "
                    "estar com a saúde da bateria em 100% — por exemplo, por terem tido pouco "
                    "uso ou bateria substituída. No catálogo, há iPhones 16 Pro Max seminovos "
                    "com 100% e também com 89% de saúde da bateria. Todos são conferidos antes "
                    "da venda."
                ),
            },
        ],
    )

    assert decision.handoff is True
    assert "atendente" in decision.reply.lower()
    assert "dúvida" in decision.reply.lower()
    assert "assistência técnica" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_explicit_battery_repair_after_catalog_context_stays_technical(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "Quanto custa trocar a bateria desse iPhone?",
        history=[
            {
                "role": "assistant",
                "content": "iPhone 16 Pro Max seminovo, 256 GB, bateria 100%, disponível.",
            }
        ],
    )

    assert decision.handoff is True
    assert "assistência técnica" in decision.reply.lower()


@pytest.mark.asyncio
async def test_single_catalog_battery_question_keeps_battery_detail_flow(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond("Tenho interesse em comprar esse celular. A bateria é original?")

    assert decision.handoff is False
    assert "assistência técnica" not in decision.reply.lower()


@pytest.mark.asyncio
async def test_visual_port_question_with_photo_is_forwarded_without_evaluation_form(tmp_path):
    settings = Settings(openai_api_key=None, faq_path=str(tmp_path / "faq.yaml"))
    cache = InventoryCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
    )
    agent = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)

    decision = await agent.respond(
        "O que seria esse branco perto da entrada do carregador?",
        image_description=(
            "Descrição visual da imagem recebida: há um ponto branco perto da entrada "
            "do carregador de um aparelho Apple."
        ),
    )

    assert decision.handoff is True
    assert "atendente" in decision.reply.lower()
    assert "lista de avaliação" not in decision.reply.lower()
