"""Entrypoint for the WhatsApp service.

With PORT set, starts the HTTP service. Without PORT, runs a local offline
smoke check that does not call OpenAI, Mercado Phone, or Z-API.
"""

from app.main import cli


if __name__ == "__main__":
    cli()
