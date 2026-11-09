from __future__ import annotations

import base64
import time
from collections.abc import Callable
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings
from app.llm_usage import record_llm_usage_safely


class OpenAIMediaError(RuntimeError):
    pass


class OpenAIMediaService:
    def __init__(
        self,
        settings: Settings,
        client: AsyncOpenAI | None = None,
        usage_recorder: Callable[..., Any] | None = None,
    ):
        if not settings.openai_api_key and client is None:
            raise OpenAIMediaError("OPENAI_API_KEY não configurada")
        self.settings = settings
        self.client = client or AsyncOpenAI(api_key=settings.openai_api_key)
        self.usage_recorder = usage_recorder

    async def transcribe(self, content: bytes, mime_type: str | None = None) -> str:
        file_type = mime_type or "audio/ogg"
        started_at = time.perf_counter()
        try:
            response = await self.client.audio.transcriptions.create(
                file=("whatsapp-audio", content, file_type),
                model=self.settings.openai_transcription_model,
                prompt="Atendimento de loja de celulares e acessórios no Brasil. Preserve modelos como iPhone, Galaxy, Pro, Max, GB e TB.",
            )
        except Exception as exc:
            record_llm_usage_safely(
                self.usage_recorder,
                stage="audio_transcription",
                model=self.settings.openai_transcription_model,
                status="error",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=exc,
            )
            raise OpenAIMediaError(f"Falha na transcrição: {exc}") from exc
        record_llm_usage_safely(
            self.usage_recorder,
            stage="audio_transcription",
            model=self.settings.openai_transcription_model,
            status="success",
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            usage=getattr(response, "usage", None),
            requests_fallback=1,
        )
        text = getattr(response, "text", None)
        if not text:
            raise OpenAIMediaError("Transcrição sem texto")
        return str(text).strip()

    async def describe_image(
        self,
        content: bytes,
        mime_type: str | None = None,
        caption: str = "",
        *,
        caption_author: str = "cliente",
    ) -> str:
        media_type = mime_type or "image/jpeg"
        encoded = base64.b64encode(content).decode("ascii")
        prompt = (
            "Identifique, com cautela, marca, modelo, capacidade, cor e categoria que aparecem nesta foto. "
            "Não invente detalhes e informe incertezas. Não dê preço nem diga que há estoque."
        )
        if caption:
            prompt += f" Legenda enviada pelo {caption_author}: {caption}"
        started_at = time.perf_counter()
        try:
            response = await self.client.responses.create(
                model=self.settings.openai_model,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {
                                "type": "input_image",
                                "image_url": f"data:{media_type};base64,{encoded}",
                                "detail": "auto",
                            },
                        ],
                    }
                ],
            )
        except Exception as exc:
            record_llm_usage_safely(
                self.usage_recorder,
                stage="image_analysis",
                model=self.settings.openai_model,
                status="error",
                duration_ms=round((time.perf_counter() - started_at) * 1000),
                error=exc,
            )
            raise OpenAIMediaError(f"Falha na análise da imagem: {exc}") from exc
        record_llm_usage_safely(
            self.usage_recorder,
            stage="image_analysis",
            model=self.settings.openai_model,
            status="success",
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            usage=getattr(response, "usage", None),
            requests_fallback=1,
        )
        text = getattr(response, "output_text", None)
        if not text:
            raise OpenAIMediaError("Análise de imagem sem resultado")
        return str(text).strip()
