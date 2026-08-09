from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from app.adapters.mercado_phone import InventoryCache
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore
from app.schemas import InventoryItem


async def main() -> int:
    root = Path(__file__).resolve().parents[1]
    settings = Settings(database_url="sqlite:///:memory:", openai_api_key=None)
    cache = InventoryCache(object(), settings, cache_path=root / "data" / "eval-cache.json")
    cache.items = [
        InventoryItem(
            external_id="eval-13",
            name="iPhone 13 128GB",
            description="iPhone 13 128GB",
            category="Celular",
            price_brl=1999.0,
            quantity=1,
            availability="Disponível",
            search_text="iphone 13 128gb celular",
        )
    ]
    cache.last_refresh = time.time()
    service = AgentService(cache, FAQStore(root / "data" / "faq.yaml"), settings, offline=True)
    cases = [json.loads(line) for line in (root / "evals" / "cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    results = []
    for case in cases:
        decision = await service.respond(case["message"])
        results.append(
            {
                "name": case["name"],
                "passed": decision.handoff == case["expect_handoff"]
                and all(text.lower() in decision.reply.lower() for text in case["must_contain"]),
                "handoff": decision.handoff,
                "reply": decision.reply,
            }
        )
    output = root / "evals" / "results" / "latest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": sum(item["passed"] for item in results), "total": len(results)}, ensure_ascii=False))
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
