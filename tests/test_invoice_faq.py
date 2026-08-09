from app.adapters.mercado_phone import InventoryCache
from app.agent import build_customer_agent
from app.config import Settings
from app.faq import FAQStore


def test_faq_answers_invoice_questions_for_all_product_conditions():
    settings = Settings(faq_path="data/faq.yaml", openai_api_key="placeholder")
    faq = FAQStore(settings.faq_file)

    answer = faq.get("Podemos emitir nota fiscal?")

    assert "todos os produtos" in answer
    assert "seminovos" in answer
    assert "lacrados" in answer


def test_agent_instructions_include_invoice_policy():
    settings = Settings(faq_path="data/faq.yaml", openai_api_key="placeholder")
    faq = FAQStore(settings.faq_file)
    agent = build_customer_agent(InventoryCache(object(), settings), faq, settings)

    assert "nota fiscal" in agent.instructions.lower()
    assert "seminovos ou lacrados" in agent.instructions.lower()
