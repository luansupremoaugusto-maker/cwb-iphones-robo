from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.adapters.mercado_phone import InventoryCache
from app.agent import AgentService, _should_use_turn_interpreter
from app.config import Settings
from app.faq import FAQStore
from app.schemas import AgentDecision, TurnInterpretation


def test_interpreter_targets_ambiguous_multi_model_messages():
    assert _should_use_turn_interpreter(
        "Não\nEu queria ou o 14 ou o 15\nTalvez até o fim do ano o 16",
        history=None,
    )


def test_interpreter_skips_simple_catalog_and_trade_in_requests():
    assert not _should_use_turn_interpreter("Tem iPhone 14?", history=None)
    assert not _should_use_turn_interpreter(
        "Quero dar meu iPhone 13 como entrada",
        history=None,
    )


def _agent_service(tmp_path) -> AgentService:
    settings = Settings(
        openai_api_key="test-key",
        faq_path=str(tmp_path / "faq.yaml"),
    )
    cache = InventoryCache(object(), settings, cache_path=tmp_path / "inventory.json")
    service = AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)
    service.offline = False
    service.agent = object()
    service.turn_interpreter = object()
    return service


@pytest.mark.asyncio
async def test_ambiguous_turn_interpretation_is_passed_to_customer_agent(tmp_path, monkeypatch):
    service = _agent_service(tmp_path)
    interpretation = TurnInterpretation(
        intents=["catalog"],
        current_request="Consultar iPhone 14 ou 15 agora",
        future_or_hypothetical_interest=["iPhone 16 perto do fim do ano"],
        rejected_suggestions=["outra opção sugerida anteriormente"],
        confidence="high",
    )
    calls: list[tuple[str, TurnInterpretation | None]] = []

    async def interpret(*args, **kwargs):
        return interpretation

    async def respond_with_customer_agent(text, history, image_description=None, interpretation=None):
        calls.append((text, interpretation))
        return AgentDecision(
            reply="Vou consultar as opções atuais e separar o modelo futuro como possibilidade.",
            confidence="high",
            product_references=["iphone-14-128"],
        )

    monkeypatch.setattr(service, "_interpret_turn", interpret)
    monkeypatch.setattr(service, "_run_customer_agent", respond_with_customer_agent)

    decision = await service.respond(
        "Não\nEu queria ou o 14 ou o 15\nTalvez até o fim do ano o 16"
    )

    assert decision.reply.startswith("Vou consultar as opções atuais")
    assert decision.product_references == ["iphone-14-128"]
    assert calls == [
        (
            "Não\nEu queria ou o 14 ou o 15\nTalvez até o fim do ano o 16",
            interpretation,
        )
    ]


@pytest.mark.asyncio
async def test_model_runs_record_sdk_usage_for_interpreter_and_customer_agent(tmp_path, monkeypatch):
    service = _agent_service(tmp_path)
    events = []
    service.usage_recorder = lambda **event: events.append(event)
    usage = SimpleNamespace(
        requests=2,
        input_tokens=10,
        output_tokens=4,
        total_tokens=14,
        input_tokens_details=SimpleNamespace(cached_tokens=3),
        output_tokens_details=SimpleNamespace(reasoning_tokens=2),
    )
    interpretation = TurnInterpretation(intents=["catalog"], confidence="high")
    decision = AgentDecision(reply="Vou consultar o catálogo.", confidence="high")

    async def runner_run(agent, _prompt, **_kwargs):
        output = interpretation if agent is service.turn_interpreter else decision
        return SimpleNamespace(
            final_output=output,
            context_wrapper=SimpleNamespace(usage=usage),
        )

    monkeypatch.setattr("app.agent.Runner.run", runner_run)

    assert await service._interpret_turn("Tem iPhone 15?", history=None) == interpretation
    assert await service._run_customer_agent("Tem iPhone 15?", history=None) == decision

    assert [event["stage"] for event in events] == ["turn_interpreter", "customer_agent"]
    assert all(event["requests"] == 2 for event in events)
    assert all(event["input_tokens"] == 10 for event in events)
    assert all(event["output_tokens"] == 4 for event in events)
    assert all(event["total_tokens"] == 14 for event in events)
    assert all(event["cached_input_tokens"] == 3 for event in events)
    assert all(event["reasoning_tokens"] == 2 for event in events)
    assert all(event["status"] == "success" for event in events)


@pytest.mark.asyncio
async def test_ambiguous_turn_can_request_clarification_without_customer_agent(
    tmp_path,
    monkeypatch,
):
    service = _agent_service(tmp_path)
    interpretation = TurnInterpretation(
        intents=["catalog", "installments"],
        needs_clarification=True,
        clarification_question="Você quer comparar os modelos ou consultar parcelas de algum deles?",
        confidence="medium",
    )

    async def interpret(*args, **kwargs):
        return interpretation

    async def unexpected_customer_agent(*args, **kwargs):
        pytest.fail("O agente principal não deve responder antes do esclarecimento")

    monkeypatch.setattr(service, "_interpret_turn", interpret)
    monkeypatch.setattr(service, "_run_customer_agent", unexpected_customer_agent)

    decision = await service.respond(
        "Não\nEu queria ou o 14 ou o 15\nTalvez até o fim do ano o 16"
    )

    assert decision.reply == interpretation.clarification_question
    assert decision.handoff is False
