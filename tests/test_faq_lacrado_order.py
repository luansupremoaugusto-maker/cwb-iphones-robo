from __future__ import annotations

from app.config import Settings
from app.faq import FAQStore


def test_lacrado_faq_contains_order_deadline_and_delivery_payment():
    faq = FAQStore(Settings(faq_path="data/faq.yaml").faq_file)

    answer = faq.get("qual o prazo e pagamento do iphone lacrado?")

    assert "por encomenda" in answer
    assert "1 semana" in answer
    assert "somente na hora da entrega" in answer
