from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.runtime import build_runtime  # noqa: E402
from app.schemas import InventoryItem  # noqa: E402


def seed_offline_catalog(runtime) -> None:
    runtime.cache.items = [
        InventoryItem(
            external_id="sandbox-iphone-13-128",
            name="iPhone 13 128GB",
            description="iPhone 13 128GB seminovo",
            category="Celular Apple",
            price_brl=1999.0,
            quantity=1,
            availability="Disponível",
            updated_at="sandbox",
            search_text="iphone 13 128gb seminovo celular apple",
        ),
        InventoryItem(
            external_id="sandbox-iphone-13-256",
            name="iPhone 13 256GB",
            description="iPhone 13 256GB novo lacrado",
            category="Celular Apple",
            price_brl=2399.0,
            quantity=1,
            availability="Disponível",
            updated_at="sandbox",
            search_text="iphone 13 256gb novo lacrado celular apple",
        ),
        InventoryItem(
            external_id="sandbox-airpods-pro-2",
            name="AirPods Pro 2",
            description="AirPods Pro 2",
            category="Acessório Apple",
            price_brl=1499.0,
            quantity=0,
            availability="Indisponível",
            updated_at="sandbox",
            search_text="airpods pro 2 acessorio apple",
        ),
    ]
    runtime.cache.last_refresh = time.time()


async def run_console(offline: bool) -> None:
    if offline:
        settings = Settings(
            database_url="sqlite:///:memory:",
            openai_api_key=None,
            mercado_phone_api_key=None,
            outbound_mode="disabled",
            faq_path=str(ROOT / "data" / "faq.yaml"),
        )
        runtime = build_runtime(settings, offline=True)
        seed_offline_catalog(runtime)
        print("Modo sandbox offline: catálogo fictício, sem OpenAI, Mercado Phone ou WhatsApp.")
    else:
        settings = Settings(outbound_mode="disabled")
        runtime = build_runtime(settings, offline=False)
        print("Modo homologação: OpenAI + Mercado Phone, sem envio pela Z-API.")
        print("As consultas podem consumir créditos da OpenAI; estoque usa GET e fotos usam POST somente leitura.")

    history: list[dict[str, str]] = []
    try:
        print("Digite /sair para terminar, /limpar para apagar o contexto ou /humano para testar handoff.")
        while True:
            try:
                message = input("Você: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not message:
                continue
            if message.lower() in {"/sair", "/exit", "/quit"}:
                break
            if message.lower() in {"/limpar", "/reset"}:
                history.clear()
                print("Contexto limpo.")
                continue
            if message.lower() == "/humano":
                message = "Quero falar com um atendente humano."

            decision = await runtime.agent.respond(message, history=history)
            print(f"Steve: {decision.reply}")
            if decision.image_urls:
                print(
                    f"[teste local] {len(decision.image_urls)} foto(s) retornada(s); "
                    "o console não envia pelo WhatsApp."
                )
                for index, image_url in enumerate(decision.image_urls, start=1):
                    print(f"  {index}. {image_url}")
            if decision.handoff:
                print(f"[handoff: {decision.handoff_reason or 'sim'}]")
            history.extend(
                [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": decision.reply},
                ]
            )
    finally:
        await runtime.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Console controlado para testar o robô sem WhatsApp")
    parser.add_argument(
        "--live",
        action="store_true",
        help="usa OpenAI e Mercado Phone; nunca envia mensagens pela Z-API",
    )
    args = parser.parse_args()
    asyncio.run(run_console(offline=not args.live))


if __name__ == "__main__":
    main()

