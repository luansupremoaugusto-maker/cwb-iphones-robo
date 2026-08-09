from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class InventoryItem(BaseModel):
    external_id: str
    name: str
    description: str = ""
    category: str | None = None
    price_brl: float | None = None
    quantity: float | None = None
    availability: str | None = None
    updated_at: str | None = None
    search_text: str = ""
    source: str = "mercado_phone"
    condition: str | None = None
    capacity: str | None = None
    colors: str | None = None
    color: str | None = None
    battery_health: float | None = None
    availability_id: str | None = None
    photo_urls: list[str] = Field(default_factory=list)
    installment_18_price_brl: float | None = None


class AgentDecision(BaseModel):
    reply: str = Field(min_length=1)
    handoff: bool = False
    handoff_reason: str | None = None
    confidence: Literal["high", "medium", "low"] = "high"
    product_references: list[str] = Field(default_factory=list)
    image_urls: list[str] = Field(default_factory=list)


class IncomingMessage(BaseModel):
    event_id: str
    message_id: str
    phone: str
    chat_name: str | None = None
    from_me: bool = False
    is_group: bool = False
    is_newsletter: bool = False
    is_status_reply: bool = False
    kind: Literal["text", "audio", "image", "button", "list", "unsupported"] = "unsupported"
    text: str = ""
    caption: str = ""
    media_url: str | None = None
    mime_type: str | None = None
    raw: dict = Field(default_factory=dict)
