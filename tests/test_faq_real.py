from __future__ import annotations

from app.adapters.mercado_phone import InventoryCache
from app.agent import build_customer_agent
from app.config import Settings
from app.faq import FAQStore


def test_real_store_faq_is_loaded_and_accent_insensitive():
    settings = Settings(faq_path="data/faq.yaml", openai_api_key="placeholder")
    faq = FAQStore(settings.faq_file)

    assert faq.get("nome da loja") == "cwb.iphones"
    assert "09:00" in faq.get("horário")
    assert "18 vezes" in faq.get("pagamento")
    assert "90 dias" in faq.get("garantia")
    assert "atendente" in faq.get("avaliação")


def test_agent_uses_store_and_assistant_names_from_faq():
    settings = Settings(faq_path="data/faq.yaml", openai_api_key="placeholder")
    faq = FAQStore(settings.faq_file)
    agent = build_customer_agent(InventoryCache(object(), settings), faq, settings)

    assert agent.name == "Atendimento cwb.iphones"
    assert "Steve" in agent.instructions
    assert agent.model == "gpt-5.6-luna"
