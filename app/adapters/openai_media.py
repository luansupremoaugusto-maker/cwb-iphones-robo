from __future__ import annotations

import base64

from openai import AsyncOpenAI

from app.config import Settings


class OpenAIMediaError(RuntimeError):
    pass


class OpenAIMediaService:
    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None):
        if not settings.openai_api_key and client is None:
            raise OpenAIMediaError("OPENAI_API_KEY não configurada")
        self.settings = settings
        self.client = client or AsyncOpenAI(api_key=settings.openai_api_key)

    async def transcribe(self, content: bytes, mime_type: str | None = None) -> str:
        file_type = mime_type or "audio/ogg"
        try:
            response = await self.client.audio.transcriptions.create(
                file=("whatsapp-audio", content, file_type),
                model=self.settings.openai_transcription_model,
                prompt="Atendimento de loja de celulares e acessórios no Brasil. Preserve modelos como iPhone, Galaxy, Pro, Max, GB e TB.",
            )
        except Exception as exc:
            raise OpenAIMediaError(f"Falha na transcrição: {exc}") from exc
        text = getattr(response, "text", None)
        if not text:
            raise OpenAIMediaError("Transcrição sem texto")
        return str(text).strip()

    async def describe_image(self, content: bytes, mime_type: str | None = None, caption: str = "") -> str:
        media_type = mime_type or "image/jpeg"
        encoded = base64.b64encode(content).decode("ascii")
        prompt = (
            "Identifique, com cautela, marca, modelo, capacidade, cor e categoria que aparecem nesta foto. "
            "Não invente detalhes e informe incertezas. Não dê preço nem diga que há estoque."
        )
        if caption:
            prompt += f" Legenda enviada pelo cliente: {caption}"
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
            raise OpenAIMediaError(f"Falha na análise da imagem: {exc}") from exc
        text = getattr(response, "output_text", None)
        if not text:
            raise OpenAIMediaError("Análise de imagem sem resultado")
        return str(text).strip()
