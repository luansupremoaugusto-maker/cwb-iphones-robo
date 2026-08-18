from __future__ import annotations

import time

import pytest

from app.adapters.catalog_cache import StoreCatalogCache
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem


class EmptyMercadoClient:
    async def fetch_all_inventory(self):
        return []


class SealedCatalog:
    def __init__(self):
        self.items = [
            InventoryItem(
                external_id="sheet:iphone-17-pro-max",
                name="iPhone 17 Pro Max",
                capacity="256 GB",
                price_brl=8000.0,
                source="google_sheets",
                condition="novo lacrado",
                search_text="iphone 17 pro max 256 gb novo lacrado",
            )
        ]

    async def search(self, query: str, limit: int = 5):
        return self.items[:limit]

    async def get(self, product_id: str):
        return next((item for item in self.items if item.external_id == product_id), None)


def _used_15_pro_max(external_id: str, capacity: str, price: float, photo: str) -> InventoryItem:
    return InventoryItem(
        external_id=external_id,
        name="IPHONE 15 PRO MAX",
        category="Celular",
        capacity=capacity,
        color="TITÂNIO NATURAL",
        condition="SEMINOVO",
        availability="Disponível para venda",
        quantity=1,
        price_brl=price,
        search_text=f"iphone 15 pro max titanio natural {capacity} celular seminovo",
        photo_urls=[photo],
    )


def _build_agent(tmp_path) -> AgentService:
    settings = Settings(google_sheets_enabled=True, mercado_cache_ttl_seconds=60)
    cache = StoreCatalogCache(
        EmptyMercadoClient(),
        settings,
        cache_path=tmp_path / "inventory.json",
        sealed_cache=SealedCatalog(),
    )
    cache.items = [
        _used_15_pro_max(
            "9445935",
            "256GB",
            4130.0,
            "https://photos.example/15-pro-max-256.jpg",
        ),
        _used_15_pro_max(
            "9902333",
            "512GB",
            4230.0,
            "https://photos.example/15-pro-max-512.jpg",
        ),
    ]
    cache.last_refresh = time.time()
    return AgentService(cache, FAQStore(settings.faq_file), settings, offline=True)


def _model_history() -> list[dict[str, str]]:
    return [
        {"role": "user", "content": "To vendo se vou pro 15 pro max"},
        {
            "role": "assistant",
            "content": (
                "Tranquilo. Se decidir pelo iPhone 15 Pro Max, me avise a capacidade "
                "e eu consulto as opcoes e valores disponiveis."
            ),
        },
    ]


@pytest.mark.asyncio
async def test_ambiguous_used_photo_request_does_not_fall_back_to_unrelated_sealed_catalog(tmp_path):
    agent = _build_agent(tmp_path)

    decision = await agent.respond("Pode me mandar fotos", history=_model_history())

    reply = decision.reply.lower()
    assert decision.handoff is False
    assert decision.image_urls == []
    assert "seminovo" in reply
    assert "256gb" in reply
    assert "512gb" in reply
    assert "fotos cadastradas" in reply
    assert "qual capacidade" in reply
    assert "novo lacrado" not in reply


@pytest.mark.asyncio
async def test_used_and_insistence_followups_keep_the_previous_photo_context(tmp_path):
    agent = _build_agent(tmp_path)
    history = [
        *_model_history(),
        {"role": "user", "content": "Pode me mandar fotos"},
        {
            "role": "assistant",
            "content": (
                "Os aparelhos novos lacrados sao vendidos por encomenda, entao nao temos "
                "fotos do produto cadastradas no sistema."
            ),
        },
        {"role": "user", "content": "Usado"},
    ]

    decision = await agent.respond("Eu vi que voce tem", history=history)

    reply = decision.reply.lower()
    assert decision.handoff is False
    assert decision.image_urls == []
    assert "seminovo" in reply
    assert "256gb" in reply
    assert "512gb" in reply
    assert "fotos cadastradas" in reply
    assert "nao encontrei fotos" not in reply
    assert "novo lacrado" not in reply


@pytest.mark.asyncio
async def test_capacity_followup_sends_the_selected_used_phone_photos(tmp_path):
    agent = _build_agent(tmp_path)
    history = [
        *_model_history(),
        {"role": "user", "content": "Pode me mandar fotos"},
        {
            "role": "assistant",
            "content": (
                "Encontrei o iPhone 15 Pro Max seminovo em 256 GB e 512 GB, "
                "ambos com fotos cadastradas. Qual capacidade você quer?"
            ),
        },
    ]

    decision = await agent.respond("256 GB", history=history)

    assert decision.handoff is False
    assert decision.image_urls == ["https://photos.example/15-pro-max-256.jpg"]
    assert decision.product_references == ["9445935"]
    assert "15 pro max" in decision.reply.lower()
