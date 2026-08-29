from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import app.agent as agent_module


@pytest.fixture(autouse=True)
def freeze_store_clock(monkeypatch):
    """Keep existing non-calendar tests independent of the real current date."""
    current = datetime(2026, 8, 10, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    monkeypatch.setattr(agent_module, "_store_now", lambda: current)
