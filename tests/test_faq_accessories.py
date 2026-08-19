from __future__ import annotations

from app.config import Settings
from app.faq import FAQStore


def test_accessories_policy_is_available_to_agent():
    faq = FAQStore(Settings(faq_path="data/faq.yaml").faq_file)

    seminovos = faq.get("seminovos")
    lacrados = faq.get("lacrados")
    accessories = faq.get("acessórios")
    capinhas = faq.get("capinhas")

    assert "cabo e fonte novos" in seminovos
    assert "homologados pela Anatel" in seminovos
    assert "R$ 10,00 cada" in seminovos
    assert "apenas o cabo original" in lacrados
    assert "protetor de câmera" in accessories
    assert "R$ 10,00 cada" in capinhas
