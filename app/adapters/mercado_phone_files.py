from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Any

import httpx

from app.adapters.mercado_phone import MercadoPhoneError, _parse_retry_after


# Keep a safety ceiling while allowing the complete product photo set returned
# by Mercado Phone in normal cases (the store currently has items with 7 photos).
MAX_PRODUCT_PHOTOS = 20


def normalize_catalog_key(value: str | None) -> str:
    plain = "".join(
        char
        for char in unicodedata.normalize("NFKD", value or "")
        if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", plain.strip().lower())


def _is_file_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", normalize_catalog_key(value))
    markers = (
        "url",
        "link",
        "arquivo",
        "file",
        "imagem",
        "image",
        "foto",
        "photo",
        "anexo",
        "attachment",
        "media",
        "download",
        "caminho",
        "path",
    )
    return any(marker in normalized for marker in markers)


def extract_file_urls(payload: Any, limit: int = MAX_PRODUCT_PHOTOS) -> list[str]:
    """Extract only HTTPS file URLs from an ArquivoApiController response.

    The Mercado Phone documentation does not include a response example. The
    parser therefore accepts the common nested shapes (items/data/files and
    url/arquivo/imagem fields) while never exposing attachment metadata or
    internal identifiers to the agent.
    """

    urls: list[str] = []
    url_pattern = re.compile(r"https://[^\s\"'<>]+", flags=re.IGNORECASE)

    def add_from_text(value: str) -> None:
        for candidate in url_pattern.findall(value):
            clean = candidate.rstrip(".,;:)]}")
            if clean not in urls:
                urls.append(clean)
            if len(urls) >= limit:
                return

    def visit(value: Any, *, file_context: bool = False, key_hint: str = "") -> None:
        if len(urls) >= limit:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key)
                child_context = file_context or _is_file_key(key_text)
                visit(child, file_context=child_context, key_hint=key_text)
                if len(urls) >= limit:
                    return
            return
        if isinstance(value, list):
            for child in value:
                visit(child, file_context=file_context, key_hint=key_hint)
                if len(urls) >= limit:
                    return
            return
        if isinstance(value, str) and (file_context or _is_file_key(key_hint)):
            add_from_text(value)

    visit(payload)
    return urls[:limit]


async def list_product_files(
    mercado_client: Any,
    object_id: str,
    *,
    origin: int = 1,
    page: int = 1,
) -> dict[str, Any]:
    """List product attachments through Mercado Phone's read-only endpoint."""

    settings = mercado_client.settings
    if not settings.mercado_phone_api_key:
        raise MercadoPhoneError("MERCADO_PHONE_API_KEY não configurada")

    body = {
        "page": page,
        "order": "id",
        "direction": "desc",
        "filters": {
            "id": "",
            "origem": str(origin),
            "objetoId": str(object_id),
        },
    }
    endpoint = settings.mercado_phone_files_url
    http_client = mercado_client._client
    headers = dict(mercado_client.headers)
    # The legacy ArquivoApiController validates the API key in Authorization;
    # the v1 endpoints continue to use X-API-Key. Sending both keeps the two
    # read-only Mercado Phone surfaces compatible.
    headers["Authorization"] = settings.mercado_phone_api_key

    for attempt in range(3):
        try:
            response = await http_client.post(endpoint, json=body, headers=headers)
        except httpx.HTTPError as exc:
            if attempt == 2:
                raise MercadoPhoneError(f"Falha de transporte Mercado Phone: {exc}") from exc
            await asyncio.sleep(0.25 * (attempt + 1))
            continue

        if response.status_code in {429, 502, 503, 504} and attempt < 2:
            retry_after = _parse_retry_after(response)
            await asyncio.sleep(min(retry_after or 0.25 * (attempt + 1), 4.0))
            continue

        if response.status_code >= 400:
            try:
                response_body = response.json()
            except ValueError:
                response_body = response.text[:500]
            detail = (
                response_body.get("detail", response_body)
                if isinstance(response_body, dict)
                else response_body
            )
            raise MercadoPhoneError(
                f"Mercado Phone HTTP {response.status_code}: {detail}",
                status_code=response.status_code,
                retry_after=_parse_retry_after(response),
            )

        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise MercadoPhoneError(
                "Mercado Phone arquivos retornou JSON inválido", response.status_code
            ) from exc
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"items": data}
        return {}

    raise MercadoPhoneError("Mercado Phone arquivos não respondeu após as tentativas")
