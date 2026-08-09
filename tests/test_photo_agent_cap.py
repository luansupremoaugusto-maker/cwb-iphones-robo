from app.agent import AgentService
from app.schemas import AgentDecision


def test_agent_keeps_all_seven_approved_photo_urls():
    urls = [f"https://cdn.example/iphone-16e-black-{index}.jpg" for index in range(7)]
    decision = AgentDecision(reply="Seguem as fotos.", image_urls=urls)

    sanitized = AgentService._sanitize_image_urls(decision)

    assert sanitized.image_urls == urls
