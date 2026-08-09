from __future__ import annotations

from app.config import Settings
from app.faq import FAQStore


def test_faq_resolves_natural_language_address_and_hours_questions():
    faq = FAQStore(Settings(faq_path="data/faq.yaml").faq_file)

    address = faq.get("Qual o endereço da loja?")
    hours = faq.get("qual é o horário de funcionamento?")

    assert "Avenida Nossa Senhora da Luz" in address
    assert "horário marcado" in address
    assert "09:00" in hours
    assert "18:00" in hours
