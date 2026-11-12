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
            external_id="iphone-13-128",
            name="IPHONE 13",
            category="Celular",
            capacity="128GB",
            color="MEIA-NOITE",
            condition="SEMINOVO",
            price_brl=1730.0,
            battery_health=87,
            quantity=1,
            availability="Disponível para venda",
            source="mercado_phone",
            search_text="iphone 13 meia-noite 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-13-256",
            name="IPHONE 13",
            category="Celular",
            capacity="256GB",
            color="AZUL",
            condition="SEMINOVO",
            price_brl=1900.0,
            battery_health=95,
            quantity=1,
            availability="Disponível para venda",
            source="mercado_phone",
            search_text="iphone 13 azul 256gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-14-starlight-128",
            name="IPHONE 14",
            category="Celular",
            capacity="128GB",
            color="ESTELAR",
            condition="SEMINOVO",
            price_brl=1870.0,
            quantity=1,
            availability="Disponível para venda",
            source="mercado_phone",
            search_text="iphone 14 estelar 128gb celular seminovo",
        ),
        InventoryItem(
            external_id="iphone-14-midnight-128",
            name="IPHONE 14",
            category="Celular",
            capacity="128GB",
            color="MEIA NOITE",
            condition="SEMINOVO",
            price_brl=1850.0,
            quantity=1,
            availability="Disponível para venda",
            source="mercado_phone",
            search_text="iphone 14 meia noite 128gb celular seminovo",
        )
    ]
    cache.last_refresh = time.time()
    service = AgentService(cache, FAQStore(root / "data" / "faq.yaml"), settings, offline=True)
    cases = [
        json.loads(line)
        for line in (root / "evals" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    results = []
    for case in cases:
        decision = await service.respond(case["message"], history=case.get("history"))
        normalized_reply = decision.reply.lower()
        checks = {
            "handoff": decision.handoff == case["expect_handoff"],
            "must_contain": all(text.lower() in normalized_reply for text in case["must_contain"]),
            "must_not_contain": all(
                text.lower() not in normalized_reply for text in case.get("must_not_contain", [])
            ),
        }
        if "expect_product_references" in case:
            checks["product_references"] = (
                decision.product_references == case["expect_product_references"]
            )
        results.append(
            {
                "name": case["name"],
                "passed": all(checks.values()),
                "checks": checks,
                "handoff": decision.handoff,
                "product_references": decision.product_references,
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
