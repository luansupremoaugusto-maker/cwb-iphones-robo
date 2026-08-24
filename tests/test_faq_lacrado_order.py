from __future__ import annotations

from app.config import Settings
from app.faq import FAQStore


def test_lacrado_faq_contains_order_deadline_and_delivery_payment():
    faq = FAQStore(Settings(faq_path="data/faq.yaml").faq_file)

    answer = faq.get("qual o prazo e pagamento do iphone lacrado?")

    assert "por encomenda" in answer
    assert "1 semana" in answer
    assert "pagamento deve ser antecipado antes do despacho" in answer
    assert "hora da entrega" not in answer

    pickup_answer = faq.get("retirada")
    assert "hora da retirada" in pickup_answer
