from __future__ import annotations

from dataclasses import dataclass

from app.adapters.catalog_cache import StoreCatalogCache
from app.adapters.google_sheets import GoogleSheetsClient
from app.adapters.google_sheets_prices import GoogleSheetsPricesCache
from app.adapters.google_sheets_public_csv import PublicCsvSheetsClient
from app.adapters.mercado_phone import MercadoPhoneClient
from app.adapters.openai_media import OpenAIMediaService
from app.adapters.zapi import ZapiClient
from app.agent import AgentService
from app.config import Settings
from app.faq import FAQStore
from app.processor import MessageProcessor
from app.storage.database import Repository, build_engine


@dataclass
class Runtime:
    settings: Settings
    repository: Repository
    mercado: MercadoPhoneClient
    google_sheets: GoogleSheetsPricesCache
    cache: StoreCatalogCache
    faq: FAQStore
    zapi: ZapiClient
    agent: AgentService
    media: OpenAIMediaService | None
    processor: MessageProcessor

    async def aclose(self) -> None:
        await self.mercado.aclose()
        await self.google_sheets.aclose()
        await self.zapi.aclose()


def build_runtime(settings: Settings, offline: bool = False) -> Runtime:
    repository = Repository(build_engine(settings.database_url))
    repository.initialize()
    mercado = MercadoPhoneClient(settings)
    if settings.google_sheets_public_csv_url:
        sheets_client = PublicCsvSheetsClient(settings)
    else:
        sheets_client = GoogleSheetsClient(settings)
    google_sheets = GoogleSheetsPricesCache(
        sheets_client,
        settings,
        enabled=not offline and settings.google_sheets_enabled,
    )
    cache = StoreCatalogCache(mercado, settings, sealed_cache=google_sheets)
    faq = FAQStore(settings.faq_file)
    zapi = ZapiClient(settings)
    agent = AgentService(cache, faq, settings, offline=offline)
    media = None if offline or not settings.openai_api_key else OpenAIMediaService(settings)
    processor = MessageProcessor(settings, repository, zapi, agent, media)
    return Runtime(settings, repository, mercado, google_sheets, cache, faq, zapi, agent, media, processor)
