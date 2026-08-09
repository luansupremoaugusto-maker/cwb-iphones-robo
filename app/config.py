from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_phone(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def split_phones(value: str | None) -> set[str]:
    return {
        normalized
        for item in re.split(r"[,;\s]+", value or "")
        if (normalized := normalize_phone(item))
    }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.local", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    port: int = 8000
    database_url: str = "sqlite:///./data/robo.db"

    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6-luna"
    openai_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "low"
    openai_transcription_model: str = "gpt-transcribe"

    mercado_phone_base_url: str = "https://platform.mercadophone.tech"
    mercado_phone_files_url: str = "https://app.mercadophone.tech/api.php?class=ArquivoApiController&method=index"
    mercado_phone_api_key: str | None = None
    mercado_system_unit_id: int = 2620
    mercado_page_limit: int = Field(default=300, ge=1, le=300)
    mercado_cache_ttl_seconds: int = Field(default=60, ge=1)
    mercado_refresh_interval_seconds: int = Field(default=300, ge=1)

    google_sheets_enabled: bool = True
    google_sheets_spreadsheet_id: str = "1s-t25cIy4ZVvM92icsXyn5hooDFzboFMeaFiwJdninc"
    google_sheets_prices_tab: str = "BOT"
    google_sheets_rates_tab: str = "TAXA PARCELAMENTO LACRADOS"
    google_sheets_prices_range: str = "A1:E500"
    google_sheets_rates_range: str = "A1:B100"
    google_sheets_public_csv_url: str | None = "https://docs.google.com/spreadsheets/d/e/2PACX-1vT3pBgINGfmjFgy2cegauMo1lBXxPxbl31OMX8NASN1PV6hgQrqaeor_sQ37Qrww9xLOLWeAwavsSJa/pub?gid=901822279&single=true&output=csv"
    google_sheets_cache_ttl_seconds: int = Field(default=3600, ge=1)
    google_sheets_refresh_interval_seconds: int = Field(default=3600, ge=1)
    google_service_account_file: str | None = None
    google_service_account_json: str | None = None

    zapi_base_url: str = "https://api.z-api.io"
    zapi_instance_id: str | None = None
    zapi_token: str | None = None
    zapi_client_token: str | None = None
    zapi_webhook_secret: str = "change-this-webhook-secret"
    zapi_expected_instance_id: str | None = None

    admin_phones: str = ""
    test_phones: str = ""
    outbound_mode: Literal["disabled", "test_only", "live"] = "disabled"
    faq_path: str = "data/faq.yaml"
    retention_days: int = Field(default=30, ge=1)
    media_max_bytes: int = Field(default=15_000_000, ge=1_000)
    message_batch_wait_seconds: int = Field(default=10, ge=0, le=60)

    @property
    def admin_phone_set(self) -> set[str]:
        return split_phones(self.admin_phones)

    @property
    def test_phone_set(self) -> set[str]:
        return split_phones(self.test_phones)

    @property
    def faq_file(self):
        from pathlib import Path

        return Path(self.faq_path)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
