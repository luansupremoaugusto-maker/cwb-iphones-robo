from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents import Agent, ModelSettings, Runner, function_tool, set_default_openai_key
from agents.tracing import set_tracing_disabled

from app.adapters.catalog_cache import _catalog_score, _is_available_item, _is_device_item
from app.adapters.mercado_phone import InventoryCache
from app.adapters.mercado_phone_files import MAX_PRODUCT_PHOTOS
from app.config import Settings
from app.faq import FAQStore
from app.installments import format_brl, format_installment_result, format_installment_table
from app.safety import protect_customer_decision
from app.schemas import AgentDecision
from app.trade_in import (
    TRADE_IN_FORM,
    TRADE_IN_NEGOTIATION_REPLY,
    TRADE_IN_REASON,
    is_trade_in_negotiation,
    is_trade_in_request,
    trade_in_em_andamento,
)


STORE_TIMEZONE = "America/Sao_Paulo"
WEEKDAY_LABELS = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)


def _store_now() -> datetime:
    """Return the current time in the store's timezone."""
    try:
        store_timezone = ZoneInfo(STORE_TIMEZONE)
    except ZoneInfoNotFoundError:
        # Keep the bot usable on systems without the IANA timezone database.
        store_timezone = timezone(timedelta(hours=-3))
    return datetime.now(store_timezone)


def _today_label(now: datetime | None = None) -> str:
    current = now or _store_now()
    return f"{WEEKDAY_LABELS[current.weekday()]}, {current:%d/%m/%Y}"


def _is_business_weekday(now: datetime | None = None) -> bool:
    current = now or _store_now()
    return current.weekday() < 5


AGENT_INSTRUCTIONS = """
Você é {assistant_name}, atendente virtual da loja {store_name}, no Brasil. Responda em português
brasileiro, com cordialidade e mensagens curtas para WhatsApp.

REGRAS OBRIGATÓRIAS:
- Você atende e consulta informações; nunca cria, edita, vende, reserva ou altera
  qualquer registro ou aparelho no Mercado Phone ou na planilha. Pode registrar
  uma solicitação de visita, mas isso não garante disponibilidade nem separa o aparelho.
- Use a ferramenta de catálogo para preço, quantidade e disponibilidade. Nunca
  invente preço, prazo, condição, garantia ou estoque.
- Os preços de produtos novos lacrados vêm da aba BOT da planilha aprovada e podem
  mudar semanalmente. Quando um resultado indicar preço de lacrado, use esse preço
  e deixe claro que é para aparelho novo lacrado. A planilha não comprova estoque.
- Para aparelhos novos lacrados, informe que trabalhamos por encomenda, com prazo
  de entrega de 1 semana e pagamento somente na hora da entrega. Essa regra
  específica prevalece sobre a regra geral de entrega; consulte o FAQ no tópico
  lacrado/lacrados quando a pergunta for sobre esse tipo de aparelho.
- Quando o cliente perguntar sobre garantia, responda diretamente: produtos
  seminovos têm garantia de 90 dias e produtos novos lacrados têm garantia de
  1 ano pela Apple. Se a pergunta mencionar o produto ou o contexto deixar claro
  qual é a condição, informe somente a garantia correspondente; se for genérica,
  informe as duas condições. Se também perguntar o que acompanha, responda isso
  na mesma mensagem.

- Ao informar o que acompanha um aparelho seminovo, diga que ele acompanha cabo e
  fonte novos, homologados pela Anatel.

- Se o cliente pedir fotos de um lacrado por encomenda, explique que não há fotos
  do produto cadastradas no sistema porque esse aparelho não está a pronta entrega.
  Se o lacrado estiver no estoque do Mercado Phone, marcado como disponível para
  venda e tiver anexos, envie as fotos dele. Nunca use fotos de outro modelo.
- Saiba a data atual usando o relógio do servidor no fuso de Curitiba (America/Sao_Paulo).
  Quando o cliente perguntar o dia de hoje, informe o dia da semana e a data. Quando
  perguntar se a loja está aberta hoje, informe o dia atual e o funcionamento real.
- Se o cliente perguntar se temos loja física, se é somente entrega ou se pode visitar,
  responda que sim, temos loja física no endereço aprovado no FAQ e informe o horário.
  De segunda a sexta, ofereça marcar a visita para hoje, sempre com horário marcado.
  Aos sábados, domingos e feriados, informe que a loja está fechada. Não encaminhe uma
  pergunta simples de endereço ou horário para um atendente quando o FAQ tiver a
  informação aprovada.
- Nunca diga que um aparelho foi reservado ou separado. Só explique que não trabalhamos
  com reserva quando o cliente perguntar se pode reservar, segurar, separar ou deixar
  o aparelho reservado. Nessa situação, explique também que alguns clientes reservam
  e depois cancelam, fazendo a loja deixar de vender o aparelho para outras pessoas
  durante esse período. De segunda a sexta, ofereça marcar uma visita para hoje;
  em dias sem atendimento, ofereça uma visita em um dia de atendimento.
  Se o cliente informar dia e horário para a visita, confirme apenas a solicitação
  e defina handoff=true para um atendente confirmar; não garanta a disponibilidade
  do aparelho.
- Para disponibilidade e quantidade, use os dados do catálogo do Mercado Phone;
  nunca deduza estoque apenas pela existência de uma linha na planilha.
- Quando o cliente perguntar o que temos disponível, quais modelos estão disponíveis,
  pedir a lista ou pedir o catálogo, use list_available_products e envie a mensagem
  pronta completa, separando seminovos em estoque e novos lacrados por encomenda.
  Nos seminovos, mostre cada registro individualmente, sem agrupar modelos iguais;
  informe cor, capacidade, estado do produto e saúde da bateria em cada linha.
  Pode usar o nome do modelo como cabeçalho e sublinhas individuais para cada
  aparelho, mas nunca esconda opções diferentes dentro de uma única linha.
  Só considere seminovos marcados como “Disponível para venda” no Mercado Phone;
  nunca mostre Laboratório, teste ou outro status não comercial. Se algum detalhe
  não estiver cadastrado, diga isso. Não envie somente alguns exemplos.
- Quando o cliente pedir fotos, use get_product_photos. Só envie image_urls que
  vierem do catálogo ou da lista aprovada de fotos; nunca invente links ou use
  fotos de outro modelo. Se não houver foto cadastrada, informe isso sem prometer
  uma foto e ofereça encaminhar ao atendente.
- Quando o cliente perguntar se a loja compra algum produto, responda que sim,
  somente produtos da marca Apple, envie o formulário de avaliação aprovado e
  defina handoff=true.
- Quando o cliente perguntar como fica o parcelamento, quanto fica parcelado,
  quais são as parcelas ou pedir uma simulação sem indicar uma quantidade específica,
  use simulate_all_installments e envie a tabela completa de 1x até 18x. Não
  pergunte em quantas vezes o cliente quer parcelar nesse caso. Se a pergunta mencionar
  pagamento por link, informe no máximo 12x e não use a tabela de 18x.
- Se o cliente pedir uma quantidade específica, como 12x ou 18x, use
  simulate_installments para informar aquela opção.
- Se o cliente informar uma entrada à vista ou um sinal e quiser parcelar o
  restante, subtraia a entrada do preço total e use somente o saldo restante
  como base do cálculo. Sem quantidade específica, use
  simulate_all_installments_with_entry; com 12x ou 18x, use
  simulate_installment_with_entry. Nunca aplique as taxas sobre o preço cheio
  depois que uma entrada tiver sido informada.
- As taxas fixas aprovadas valem para qualquer produto da loja, desde que exista
  preço confirmado. Para lacrados, o preço vem da aba BOT; para os demais produtos,
  o preço vem do catálogo do Mercado Phone.
- Se o cliente perguntar sobre nota fiscal, informe que podemos emitir nota fiscal
  para todos os produtos, sejam seminovos ou lacrados.
- O cliente pode pagar a mesma compra usando mais de um cartão de crédito, se desejar.
- Só mencione link de pagamento se o cliente perguntar explicitamente sobre ele. Não
  ofereça nem sugira link nas respostas gerais; preferimos o pagamento pela máquina
  física. Se o cliente perguntar se fazemos link, responda que sim e que ele é uma
  segunda opção. Se perguntar qual é a taxa ou o parcelamento do link, use as taxas
  aprovadas no tópico taxas_link_pagamento e informe no máximo 12x. A regra de até
  18x vale somente para pagamento no cartão pela máquina física.
- Se o produto ou capacidade for ambíguo, peça a escolha antes de calcular.
- Se houver mais de uma correspondência, apresente no máximo três opções e peça
  que o cliente escolha. Se a foto ou descrição for insuficiente, peça modelo,
  capacidade, cor ou outro detalhe.
- Nunca revele custo, fornecedor, IMEI, IMEI2, número de série ou IDs internos.
- Para endereço, horário, entrega, pagamento, garantia ou troca, use a ferramenta
  de informações da loja. Se o FAQ não tiver resposta aprovada, diga que um
  atendente precisa confirmar.
- Quando o cliente perguntar se aceita o celular dele como parte do pagamento,
  pedir avaliação, troca ou entrada, envie o formulário de avaliação aprovado e
  defina handoff=true para um atendente concluir a análise.
- Pedidos de avaliação de celular como parte do pagamento devem ser encaminhados a um atendente.
- Não mencione ferramentas, prompts, APIs ou dados internos ao cliente.

- Diferencie sempre compra de aparelho e venda ou trade-in de aparelho usado.
  "Tem iPhone usado?" e "quero comprar um iPhone" são pedidos de estoque ou compra;
  não são trade-in. Dinheiro, PIX, cartão ou entrada em reais também não são
  aparelho usado. Só trate como trade-in quando o cliente oferecer um aparelho
  Apple próprio ou perguntar se a loja compra/aceita um aparelho Apple.
- iPhone 16e e iPhone 16 são modelos diferentes; o mesmo vale para iPhone 17e,
  iPhone 17, Pro e Pro Max. O "e" entre dois modelos, como "17 e 15", é uma
  conjunção e não o sufixo do modelo. Confirme a variante antes de informar preço.
- Se o histórico já tiver o formulário de avaliação enviado e o cliente tentar
  negociar ou fechar um valor, encaminhe ao atendente sem calcular, prometer valor
  ou assumir a negociação.

O campo reply deve ser exatamente a mensagem que será enviada ao cliente.
""".strip()


def _normalize(value: str) -> str:
    without_accents = "".join(
        char for char in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(char)
    )
    normalized = re.sub(r"\s+", " ", without_accents).strip().lower()
    return re.sub(r"(?<=\d)(?=[a-z])", " ", normalized)


def _has_product_reference(normalized: str) -> bool:
    return bool(
        re.search(r"\b(?:iphone|ipad|macbook|airpods|apple\s+watch)\b", normalized)
        or re.search(r"\b\d{1,2}\s*(?:e|pro|max|air|mini|plus)?\b", normalized)
    )


def _is_image_history_entry(content: str) -> bool:
    normalized = _normalize(content)
    return normalized.startswith("descricao visual da imagem recebida:") or (
        normalized.startswith("legenda da imagem:")
        and "descricao visual da imagem recebida:" in normalized
    )


def _current_catalog_context(text: str, image_description: str | None = None) -> str:
    """Put visual context before explicit text so explicit text wins on conflict."""
    parts = [part.strip() for part in (image_description, text) if part and part.strip()]
    return "\n".join(parts).strip()


def _is_available_list_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    phrases = (
        "o que tem disponivel",
        "o que voces tem",
        "o que a loja tem",
        "quais modelos",
        "quais aparelhos",
        "lista completa",
        "lista de modelos",
        "me manda a lista",
        "me passe a lista",
        "catalogo",
    )
    return any(phrase in normalized for phrase in phrases)


def _is_product_availability_request(text: str) -> bool:
    """Route a product-specific availability question without an LLM guess."""
    normalized = _normalize(text)
    if not normalized or not _has_product_reference(normalized):
        return False
    if _is_available_list_request(text):
        return False
    if any(
        marker in normalized
        for marker in (
            "foto",
            "imagem",
            "parcel",
            "entrada",
            "sinal",
            "garantia",
            "reserva",
            "endereco",
            "horario",
            "entrega",
            "pagamento",
            "nota fiscal",
        )
    ):
        return False
    if any(
        phrase in normalized
        for phrase in (
            "tem ",
            "disponivel",
            "disponibilidade",
            "em estoque",
            "estoque",
            "a venda",
            "vende",
            "possui",
            "quanto custa",
            "qual o preco",
        )
    ):
        return True
    return bool(re.match(r"^(?:iphone|ipad|macbook|airpods|apple\s+watch)\b", normalized))


def _requested_capacity_key(text: str) -> str | None:
    normalized = _normalize(text)
    match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(gb|tb|g)\b", normalized)
    if match:
        number = match.group(1).replace(",", ".")
        unit = "tb" if match.group(2) == "tb" else "gb"
        if number.endswith(".0"):
            number = number[:-2]
        return f"{number}{unit}"
    for number in ("1024", "512", "256", "128", "64", "32"):
        if re.search(rf"\b{number}\b", normalized):
            return f"{number}gb"
    return None


def _capacity_key(value: Any) -> str | None:
    normalized = _normalize(str(value or ""))
    match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(gb|tb|g)\b", normalized)
    if not match:
        return None
    number = match.group(1).replace(",", ".")
    unit = "tb" if match.group(2) == "tb" else "gb"
    if number.endswith(".0"):
        number = number[:-2]
    return f"{number}{unit}"


def _is_physical_store_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    phrases = (
        "loja fisica",
        "so entrega",
        "tem loja",
        "existe loja",
        "nao tem loja",
        "onde fica a loja",
        "qual o endereco",
        "endereco da loja",
        "endereco do escritorio",
        "posso ir na loja",
        "posso visitar a loja",
    )
    return any(phrase in normalized for phrase in phrases)


def _is_current_date_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    phrases = (
        "que dia e hoje",
        "qual dia e hoje",
        "que data e hoje",
        "qual a data de hoje",
        "hoje e que dia",
    )
    return any(phrase in normalized for phrase in phrases)


def _is_today_store_status_request(text: str) -> bool:
    normalized = _normalize(text)
    if "hoje" not in normalized:
        return False
    return any(
        marker in normalized
        for marker in (
            "aberto",
            "fechado",
            "funcionamento",
            "atendimento",
            "horario",
            "horas",
            "abre",
            "fecha",
        )
    )


def _is_visit_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    phrases = (
        "marcar um horario",
        "marcar horario",
        "marcar uma visita",
        "marcar visita",
        "agendar",
        "agendamento",
        "reservar um horario",
        "deixar um horario marcado",
        "visitar a loja",
        "visitar o escritorio",
        "ir na loja",
        "ir ate a loja",
        "ir ao escritorio",
        "passar na loja",
        "comparecer",
        "que dia posso ir",
        "ir hoje",
        "visitar hoje",
        "passar hoje",
        "comparecer hoje",
    )
    return any(phrase in normalized for phrase in phrases) or bool(
        re.search(r"\b(?:posso|consigo|gostaria de)\s+(?:ir|visitar|passar|comparecer)\b", normalized)
    )


def _is_reservation_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    phrases = (
        "reserva",
        "reservar",
        "segurar",
        "separar",
        "deixar separado",
        "guardar o aparelho",
        "guardar ele",
    )
    return any(phrase in normalized for phrase in phrases)


def _has_visit_date_reference(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    date_markers = (
        "segunda",
        "terca",
        "quarta",
        "quinta",
        "sexta",
        "sabado",
        "domingo",
        "amanha",
        "hoje",
        "proxima semana",
        "esta semana",
    )
    return any(marker in normalized for marker in date_markers) or bool(
        re.search(r"\bdia\s+\d{1,2}\b", normalized)
    )


def _has_visit_time_reference(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    return bool(
        re.search(
            r"\b(?:as)\s*(?:[01]?\d|2[0-3])(?:\s*(?::|h)\s*[0-5]\d)?\s*(?:h|horas)?\b",
            normalized,
        )
        or re.search(r"\b(?:[01]?\d|2[0-3])\s*(?::\s*[0-5]\d|h\s*[0-5]\d|horas)\b", normalized)
    )


def _has_appointment_prompt(history: list[dict[str, str]] | None) -> bool:
    return any(
        entry.get("role") == "assistant"
        and any(
            marker in _normalize(entry.get("content", ""))
            for marker in ("qual dia", "que dia", "qual horario", "que horario")
        )
        for entry in (history or [])
    )


def _is_appointment_followup(text: str, history: list[dict[str, str]] | None) -> bool:
    """Use appointment history only when the current message looks like a reply to it."""
    if not _has_appointment_prompt(history):
        return False

    normalized = _normalize(text)
    if not normalized:
        return False

    # A new request must win over an old appointment prompt.
    if (
        _is_current_date_request(text)
        or _is_today_store_status_request(text)
        or _is_available_list_request(text)
        or _is_product_availability_request(text)
        or _is_physical_store_request(text)
    ):
        return False

    if _has_visit_date_reference(text) or _has_visit_time_reference(text):
        return True

    return normalized in {
        "sim",
        "pode",
        "pode ser",
        "ok",
        "okay",
        "beleza",
        "esse horario",
        "esse horario serve",
        "esse horario esta bom",
    }


def _is_handoff_confirmation(text: str, history: list[dict[str, str]] | None) -> bool:
    """Treat a short affirmative as human handoff only after an explicit offer."""
    normalized = re.sub(r"[^\w\s]", " ", _normalize(text), flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized not in {
        "sim",
        "sim pfv",
        "sim por favor",
        "pode",
        "pode sim",
        "claro",
        "ok",
        "okay",
        "beleza",
    }:
        return False

    offer_markers = (
        "posso encaminhar",
        "vou encaminhar",
        "encaminhar seu pedido",
        "falar com um atendente",
        "atendente finalizar",
        "atendente confirmar",
    )
    return any(
        entry.get("role") == "assistant"
        and any(marker in _normalize(entry.get("content", "")) for marker in offer_markers)
        for entry in (history or [])
    )


def _appointment_context(text: str, history: list[dict[str, str]] | None) -> str:
    previous_user_text = [
        entry.get("content", "").strip()
        for entry in (history or [])
        if entry.get("role") == "user" and entry.get("content", "").strip()
    ]
    return "\n".join([*previous_user_text[-4:], text.strip()]).strip()


def _store_address(faq: FAQStore) -> str:
    return faq.get("address") or (
        "Avenida Nossa Senhora da Luz, 1341 - Jardim Social, Curitiba - PR, 82520-060"
    )


def _store_hours(faq: FAQStore) -> str:
    return faq.get("hours") or faq.get("horario") or (
        "Atendemos de segunda a sexta, das 09:00 às 18:00. "
        "Aos sábados, domingos e feriados, a loja fica fechada."
    )


def _today_store_reply(faq: FAQStore, *, include_physical_store: bool = True) -> str:
    current = _store_now()
    today = _today_label(current)
    address = _store_address(faq)
    if _is_business_weekday(current):
        intro = "Sim, temos loja física. " if include_physical_store else ""
        return (
            f"{intro}Hoje é {today}. Atendemos hoje das 09:00 às 18:00, com horário marcado. "
            f"Endereço: {address}. Posso marcar uma visita para hoje? "
            "Qual horário fica melhor para você?"
        )
    intro = "Sim, temos loja física. " if include_physical_store else ""
    return (
        f"{intro}Hoje é {today}. Hoje a loja está fechada. {_store_hours(faq)} "
        f"Endereço: {address}. O atendimento é feito com horário marcado. "
        "Podemos marcar uma visita em um dia de atendimento. Qual dia e horário ficam "
        "melhores para você?"
    )


def _reservation_reply(faq: FAQStore) -> str:
    reason = faq.get("reserva") or (
        "Não trabalhamos com reserva de aparelhos porque alguns clientes reservam "
        "e depois cancelam, e nesse período deixamos de vender o aparelho para outras pessoas."
    )
    current = _store_now()
    if _is_business_weekday(current):
        return (
            f"{reason} Hoje é {_today_label(current)}. Podemos marcar sua visita para hoje, "
            f"com horário marcado, no endereço {_store_address(faq)}. Qual horário fica melhor para você?"
        )
    return (
        f"{reason} Hoje é {_today_label(current)} e a loja está fechada. "
        "Podemos marcar uma visita em um dia de atendimento, com horário marcado. "
        "Qual dia e horário ficam melhores para você?"
    )


def _is_warranty_request(text: str) -> bool:
    normalized = _normalize(text)
    return bool(normalized and "garantia" in normalized)


def _has_sealed_reference(normalized: str) -> bool:
    return any(marker in normalized for marker in ("lacrado", "encomenda"))


def _has_seminovo_reference(normalized: str) -> bool:
    return any(marker in normalized for marker in ("seminovo", "seminovos", "usado", "usados"))


def _is_photo_retry_request(text: str) -> bool:
    normalized = _normalize(text)
    retry_phrases = (
        "nao foi enviado",
        "nao chegou",
        "nao recebi",
        "nao apareceu",
        "nao veio",
        "nao carregou",
        "manda de novo",
        "manda novamente",
        "envia de novo",
        "envia novamente",
        "enviar de novo",
        "reenviar",
        "reenvia",
    )
    return bool(normalized and any(phrase in normalized for phrase in retry_phrases))


def _is_photo_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if _is_photo_retry_request(text):
        return True
    if not any(word in normalized for word in ("foto", "fotos", "imagem", "imagens")):
        return False
    if any(
        phrase in normalized
        for phrase in ("manda", "mande", "mandar", "enviar", "envia", "mostra", "ver", "tem", "possui", "disponivel")
    ):
        return True
    # Short WhatsApp requests such as "fotos do 16e preto?" often omit the
    # verb. A model/color reference or an object connector is enough to route
    # them through the deterministic photo lookup.
    return _has_product_reference(normalized) or bool(
        re.search(r"\b(?:do|da|desse|dessa|deste|desta|dele|dela)\b", normalized)
    )


def _is_sealed_photo_request(text: str) -> bool:
    """Identify photo requests for sealed, made-to-order products."""
    if not _is_photo_request(text):
        return False
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "por encomenda",
            "encomenda",
        )
    )


def _is_sealed_item(item: Any) -> bool:
    """Identify a sealed item returned by the price sheet/catalog search."""
    source = _normalize(str(getattr(item, "source", "") or ""))
    condition = _normalize(str(getattr(item, "condition", "") or ""))
    return source == "google sheets" or "lacrado" in condition or "encomenda" in condition


def _is_made_to_order_sealed_item(item: Any) -> bool:
    """Identify sheet/order items that must never send catalog photos."""
    source = _normalize(str(getattr(item, "source", "") or "")).replace("_", " ")
    condition = _normalize(str(getattr(item, "condition", "") or ""))
    return source == "google sheets" or "encomenda" in condition


SEALED_PHOTO_REPLY = (
    "Os aparelhos novos lacrados são vendidos por encomenda, então não temos "
    "fotos do produto cadastradas no sistema."
)


PAYMENT_LINK_REPLY = (
    "Sim, fazemos link de pagamento quando necessário. Preferimos o pagamento pela "
    "máquina física; o link fica como segunda opção."
)


def _is_payment_link_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    phrases = (
        "link de pagamento",
        "link para pagamento",
        "link do pagamento",
        "pagamento por link",
        "pagamento via link",
        "pagar por link",
        "pagar via link",
        "pagar com link",
        "pagar pelo link",
        "pagamento com link",
        "aceita link",
        "tem link",
        "por link",
        "gerar link",
        "gerar um link",
        "enviar link",
        "enviar um link",
        "mandar link",
        "mandar um link",
        "criar link",
        "criar um link",
    )
    return any(phrase in normalized for phrase in phrases)


def _is_payment_link_rate_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or not _is_payment_link_request(text):
        return False
    if re.search(r"\b(?:[1-9]|1[0-8])\s*x\b", normalized):
        return True
    return any(
        phrase in normalized
        for phrase in (
            "taxa",
            "tarifa",
            "juros",
            "quanto fica",
            "qual o valor",
            "qual valor",
            "parcelamento",
            "parcelar",
            "simulacao",
            "simular",
            "em quantas",
        )
    )


def _is_full_installment_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if re.search(r"\b(?:[1-9]|1[0-8])\s*x\b", normalized):
        return False
    phrases = (
        "como fica o parcelamento",
        "como fica parcelado",
        "quanto fica parcelado",
        "quanto fica o parcelamento",
        "quais sao as parcelas",
        "quais as parcelas",
        "me passa o parcelamento",
        "simulacao de parcelamento",
        "simular parcelamento",
    )
    return any(phrase in normalized for phrase in phrases)


def _parse_brl_amount(raw_value: str) -> float | None:
    value = re.sub(r"[^0-9,.]", "", raw_value or "")
    if not value:
        return None
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    elif value.count(".") > 1:
        value = value.replace(".", "")
    elif "." in value:
        integer, fraction = value.split(".", 1)
        if len(fraction) == 3 and integer.isdigit():
            value = value.replace(".", "")
    try:
        amount = float(value)
    except ValueError:
        return None
    return amount if amount >= 0 else None


def _extract_entry_amount(text: str) -> float | None:
    normalized = _normalize(text)
    if "entrada" not in normalized and "sinal" not in normalized:
        return None

    amount = r"(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?"
    patterns = (
        rf"\b(?:entrada|sinal)\s*(?:(?:a|à)\s+vista\s*)?(?:de|no valor de|:)??\s*(?:r\$\s*)?({amount})",
        rf"\b(?:r\$\s*)?({amount})\s*(?:de\s+)?(?:entrada|sinal)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if match:
            return _parse_brl_amount(match.group(1))
    return None


def _requested_installments(text: str) -> int | None:
    normalized = _normalize(text)
    for pattern in (r"\b(1[0-8]|[1-9])\s*x\b", r"\b(1[0-8]|[1-9])\s+vezes\b"):
        match = re.search(pattern, normalized)
        if match:
            return int(match.group(1))
    return None


def _last_requested_installments(history: list[dict[str, str]] | None) -> int | None:
    for item in reversed(history or []):
        if item.get("role") != "user":
            continue
        requested = _requested_installments(item.get("content", ""))
        if requested is not None:
            return requested
    return None


def _is_installment_selection_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    if _requested_installments(text) is not None:
        return True
    if _last_requested_installments(history) is None:
        return False
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in ("bateria", "saude da bateria", "essa opcao", "essa unidade", "qual delas", "a de ", "o de ")
    )


def _has_installment_product_context(value: str) -> bool:
    normalized = _normalize(value)
    return bool(
        re.search(r"\b(?:iphone|ipad|macbook|airpods|apple\s+watch)\b", normalized)
        or re.search(r"\b\d{1,2}\s*(?:e|pro|max|air|mini|plus)\b", normalized)
    )


def _installment_context_query(text: str, history: list[dict[str, str]] | None) -> str:
    current = text.strip()
    for item in reversed(history or []):
        if item.get("role") == "assistant" and any(
            marker in _normalize(item.get("content", "")) for marker in ("iphone", "ipad", "produto")
        ):
            return f"{item.get('content', '')}\n{current}".strip()
    previous_user_text = [
        item.get("content", "").strip()
        for item in (history or [])
        if item.get("role") == "user" and item.get("content", "").strip()
    ]
    return "\n".join([*previous_user_text[-4:], current]).strip()


def _product_context_query(text: str, history: list[dict[str, str]] | None) -> str:
    """Preserva a referência explícita do produto atual ao procurar fotos."""
    current = text.strip()
    normalized = _normalize(current)
    if _has_product_reference(normalized):
        return current

    def is_specific_product_answer(content: str) -> bool:
        answer = _normalize(content)
        return bool(
            _has_product_reference(answer)
            and any(marker in answer for marker in ("r$", "bateria", "disponivel", "capacidade", "gb"))
            and not any(marker in answer for marker in ("lista completa", "novos lacrados por encomenda"))
        )

    entries = [
        (index, item.get("role"), item.get("content", "").strip())
        for index, item in enumerate(history or [])
        if item.get("content", "").strip()
    ]
    anchors = [
        index
        for index, role, content in entries
        if role == "user" and _has_product_reference(_normalize(content))
    ]
    anchor_index = anchors[-1] if anchors else 0

    image_anchor = any(
        index == anchor_index and role == "user" and _is_image_history_entry(content)
        for index, role, content in entries
    )

    parts: list[str] = []
    for index, role, content in entries:
        if index >= anchor_index and role == "user" and content not in parts:
            parts.append(content)
    if not image_anchor:
        for index, role, content in reversed(entries):
            if index >= anchor_index and role == "assistant" and is_specific_product_answer(content):
                if content not in parts:
                    parts.append(content)
                break
    parts.append(current)
    return "\n".join(parts[-6:]).strip()


def _has_photo_request_in_history(history: list[dict[str, str]] | None) -> bool:
    photo_words = ("foto", "fotos", "imagem", "imagens")
    return any(
        entry.get("role") == "user"
        and any(word in _normalize(entry.get("content", "")) for word in photo_words)
        for entry in (history or [])
    )


def _public_item(item: Any) -> dict[str, Any]:
    return {
        "nome": item.name,
        "categoria": item.category,
        "condicao": item.condition,
        "capacidade": item.capacity,
        "cor": getattr(item, "color", None) or item.colors,
        "cores": item.colors,
        "saude_bateria": getattr(item, "battery_health", None),
        "preco_brl": item.price_brl,
        "quantidade": item.quantity,
        "disponibilidade": item.availability,
        "fotos_disponiveis": len(getattr(item, "photo_urls", []) or []),
        "fonte_preco": "planilha BOT - novos lacrados"
        if item.source == "google_sheets"
        else "catálogo de estoque",
    }


def _format_battery(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "não informada no cadastro"
    shown = str(int(number)) if number.is_integer() else str(number).replace(".", ",")
    return f"{shown}%"


def _format_product_availability(items: list[Any]) -> str:
    if not items:
        return "No momento não localizei esse produto no catálogo."

    model = str(getattr(items[0], "name", None) or "produto").strip()
    lines = [f"Sim 😊 Encontrei estas opções de {model} disponíveis:"]
    for item in items:
        color = str(getattr(item, "color", None) or getattr(item, "colors", None) or "cor não informada")
        capacity = str(getattr(item, "capacity", None) or "").strip()
        if not capacity:
            name_and_description = f"{getattr(item, 'name', '')} {getattr(item, 'description', '')}"
            capacity_match = re.search(
                r"\b(\d+(?:[.,]\d+)?\s*(?:GB|TB))\b",
                name_and_description,
                flags=re.IGNORECASE,
            )
            capacity = capacity_match.group(1).upper() if capacity_match else "capacidade não informada"
        if getattr(item, "source", None) == "google_sheets":
            condition = "NOVO LACRADO"
            battery_text = "não se aplica"
        else:
            condition = str(getattr(item, "condition", None) or "estado não informado").upper()
            battery_text = _format_battery(getattr(item, "battery_health", None))
        price = getattr(item, "price_brl", None)
        price_text = format_brl(float(price)) if price is not None else "preço a confirmar"
        lines.append(f"• {color} — {capacity} — {condition} — {price_text} | Bat: {battery_text}")
    return "\n".join(lines)


def _format_available_products(result: dict[str, Any]) -> str:
    def entry_line(entry: dict[str, Any]) -> str:
        color = entry.get("cor")
        colors = entry.get("cores") or []
        color_text = str(color or "")
        if not color_text and colors:
            color_text = ", ".join(str(value) for value in colors)
        if not color_text:
            color_text = "cor não informada"

        capacity = str(entry.get("capacidade") or "capacidade não informada")
        condition = str(entry.get("condicao") or "estado não informado").strip().upper()
        prices = entry.get("precos_brl") or []
        price_text = format_brl(float(prices[0])) if len(prices) == 1 else "preço a confirmar"
        battery = entry.get("saude_bateria")
        if battery is not None:
            battery_text = _format_battery(battery)
        elif "LACRADO" in condition:
            battery_text = "não se aplica"
        else:
            battery_text = "não informada"

        parts = [color_text, capacity, condition]
        quantity = entry.get("quantidade")
        if quantity not in (None, 1, 1.0):
            parts.append(f"{quantity} unidades")
        return f"  • - {' - '.join(parts)} — {price_text} | Bat: {battery_text}"

    def grouped_lines(entries: list[dict[str, Any]]) -> list[str]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for entry in entries:
            model = str(entry.get("nome") or "Produto")
            if model not in grouped:
                grouped[model] = []
                order.append(model)
            grouped[model].append(entry)
        lines: list[str] = []
        for model in order:
            lines.append(model)
            lines.extend(entry_line(entry) for entry in grouped[model])
        return lines

    lines = ["📋 Lista completa de produtos disponíveis:"]
    seminovos = result.get("seminovos") or []
    lacrados = result.get("lacrados") or []
    if seminovos:
        lines.extend(["", "📱 Seminovos disponíveis para venda:"])
        lines.extend(grouped_lines(seminovos))
    if lacrados:
        lines.extend(["", "📦 Novos lacrados por encomenda:"])
        lines.extend(grouped_lines(lacrados))
        lines.append("\nOs lacrados são por encomenda, com prazo de 1 semana e pagamento somente na hora da entrega.")
    if not seminovos and not lacrados:
        return "No momento não localizei modelos disponíveis para informar. Vou encaminhar para um atendente confirmar."
    return "\n".join(lines)


def build_customer_agent(cache: InventoryCache, faq: FAQStore, settings: Settings) -> Agent:
    @function_tool
    async def search_catalog(query: str) -> str:
        """Busca produtos, preços, cor, bateria e disponibilidade no catálogo da loja."""
        items = await cache.search(query, limit=5)
        return json.dumps([_public_item(item) for item in items], ensure_ascii=False)

    @function_tool
    async def get_product_availability(product_id: str) -> str:
        """Consulta um produto pelo identificador retornado pela busca."""
        item = await cache.get(product_id)
        return json.dumps(_public_item(item) if item else {"encontrado": False}, ensure_ascii=False)

    @function_tool
    async def list_available_products() -> str:
        """Lista individualmente os seminovos à venda e os lacrados por encomenda."""
        method = getattr(cache, "list_available_products", None)
        if not callable(method):
            return json.dumps({"encontrado": False, "motivo": "Lista de disponibilidade indisponível"})
        result = await method()
        if result.get("encontrado"):
            result["mensagem_pronta"] = _format_available_products(result)
        return json.dumps(result, ensure_ascii=False)

    @function_tool
    async def get_product_photos(product_query: str) -> str:
        """Retorna somente fotos aprovadas cadastradas para o produto solicitado."""
        items = await cache.search(product_query, limit=5)
        products = [
            {
                "nome": item.name,
                "capacidade": item.capacity,
                "fotos": list(getattr(item, "photo_urls", []) or [])[:MAX_PRODUCT_PHOTOS],
            }
            for item in items
            if getattr(item, "photo_urls", [])
        ]
        return json.dumps({"encontrado": bool(products), "produtos": products}, ensure_ascii=False)

    @function_tool
    async def simulate_all_installments(product_query: str) -> str:
        """Calcula e devolve todas as parcelas de 1x a 18x para um produto."""
        method = getattr(cache, "simulate_all_installments", None)
        if not callable(method):
            return json.dumps({"encontrado": False, "motivo": "Tabela de parcelamento indisponível"})
        result = await method(product_query)
        if result.get("encontrado"):
            result["mensagem_pronta"] = format_installment_table(result)
        return json.dumps(result, ensure_ascii=False)

    @function_tool
    async def simulate_all_installments_with_entry(product_query: str, entry_amount_brl: float) -> str:
        """Calcula 1x a 18x sobre o saldo que sobra depois de uma entrada à vista."""
        method = getattr(cache, "simulate_all_installments_with_entry", None)
        if not callable(method):
            return json.dumps({"encontrado": False, "motivo": "Cálculo com entrada indisponível"})
        result = await method(product_query, entry_amount_brl)
        if result.get("encontrado"):
            result["mensagem_pronta"] = format_installment_table(result)
        return json.dumps(result, ensure_ascii=False)

    @function_tool
    async def simulate_installments(product_query: str, installments: int) -> str:
        """Simula uma quantidade específica entre 1x e 18x."""
        result = await cache.simulate_installment(product_query, installments)
        return json.dumps(result, ensure_ascii=False)

    @function_tool
    async def simulate_installment_with_entry(
        product_query: str,
        entry_amount_brl: float,
        installments: int,
    ) -> str:
        """Calcula uma quantidade específica sobre o saldo após a entrada à vista."""
        method = getattr(cache, "simulate_installment_with_entry", None)
        if not callable(method):
            return json.dumps({"encontrado": False, "motivo": "Cálculo com entrada indisponível"})
        result = await method(product_query, entry_amount_brl, installments)
        if result.get("encontrado"):
            result["mensagem_pronta"] = format_installment_result(result)
        return json.dumps(result, ensure_ascii=False)

    @function_tool
    def get_store_information(topic: str) -> str:
        """Retorna uma informação aprovada no FAQ da loja."""
        return faq.get(topic)

    store_name = faq.get("store_name") or "cwb.iphones"
    assistant_name = faq.get("assistant_name") or "Steve"
    instructions = AGENT_INSTRUCTIONS.format(store_name=store_name, assistant_name=assistant_name)
    model_settings = ModelSettings(
        reasoning={"effort": settings.openai_reasoning_effort},
        verbosity="low",
    )
    return Agent(
        name=f"Atendimento {store_name}",
        instructions=instructions,
        model=settings.openai_model,
        model_settings=model_settings,
        tools=[
            search_catalog,
            get_product_availability,
            list_available_products,
            get_product_photos,
            simulate_all_installments,
            simulate_all_installments_with_entry,
            simulate_installments,
            simulate_installment_with_entry,
            get_store_information,
        ],
        output_type=AgentDecision,
    )


class AgentService:
    def __init__(self, cache: InventoryCache, faq: FAQStore, settings: Settings, offline: bool = False):
        self.cache = cache
        self.faq = faq
        self.settings = settings
        self.offline = offline
        if not offline and settings.openai_api_key:
            set_default_openai_key(settings.openai_api_key, use_for_tracing=False)
            set_tracing_disabled(True)
        self.agent = None if offline else build_customer_agent(cache, faq, settings)

    @staticmethod
    def _sanitize_image_urls(decision: AgentDecision) -> AgentDecision:
        urls = [
            url
            for url in decision.image_urls
            if isinstance(url, str) and re.match(r"^https://", url, flags=re.IGNORECASE)
        ][:MAX_PRODUCT_PHOTOS]
        return decision.model_copy(update={"image_urls": urls})

    async def respond(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
        image_description: str | None = None,
    ) -> AgentDecision:
        combined_request = " ".join(part for part in (text, image_description) if part)
        if is_trade_in_request(combined_request):
            return AgentDecision(
                reply=TRADE_IN_FORM,
                handoff=True,
                handoff_reason=TRADE_IN_REASON,
                confidence="high",
            )
        if trade_in_em_andamento(history) and is_trade_in_negotiation(text):
            return AgentDecision(
                reply=TRADE_IN_NEGOTIATION_REPLY,
                handoff=True,
                handoff_reason=TRADE_IN_REASON,
                confidence="high",
            )

        payment_link_decision = self._try_payment_link(text)
        if payment_link_decision is not None:
            return protect_customer_decision(payment_link_decision)

        if _is_handoff_confirmation(text, history):
            return protect_customer_decision(
                AgentDecision(
                    reply="Perfeito! Vou encaminhar seu pedido para um atendente finalizar o atendimento.",
                    handoff=True,
                    handoff_reason="Cliente confirmou o encaminhamento para atendimento humano",
                    confidence="high",
                )
            )

        visit_decision = self._try_visit_scheduling(text, history)
        if visit_decision is not None:
            return protect_customer_decision(visit_decision)

        current_day_decision = self._try_current_day_information(text)
        if current_day_decision is not None:
            return protect_customer_decision(current_day_decision)

        availability_decision = await self._try_available_products(text)
        if availability_decision is not None:
            return protect_customer_decision(availability_decision)

        product_availability_decision = await self._try_product_availability(
            text,
            image_description=image_description,
        )
        if product_availability_decision is not None:
            return protect_customer_decision(product_availability_decision)

        physical_store_decision = self._try_physical_store(text)
        if physical_store_decision is not None:
            return protect_customer_decision(physical_store_decision)

        photo_decision = await self._try_product_photos(
            text,
            history,
            image_description=image_description,
        )
        if photo_decision is not None:
            return protect_customer_decision(self._sanitize_image_urls(photo_decision))

        warranty_decision = await self._try_warranty(text, history)
        if warranty_decision is not None:
            return protect_customer_decision(warranty_decision)

        entry_installment_decision = await self._try_entry_installment(text, history)
        if entry_installment_decision is not None:
            return protect_customer_decision(entry_installment_decision)

        specific_installment_decision = await self._try_specific_installment(text, history)
        if specific_installment_decision is not None:
            return protect_customer_decision(specific_installment_decision)

        full_installment_decision = await self._try_full_installment_table(text, history)
        if full_installment_decision is not None:
            return protect_customer_decision(full_installment_decision)

        if self.offline:
            return protect_customer_decision(
                await self._offline_response(text, history=history, image_description=image_description)
            )
        if not self.settings.openai_api_key or self.agent is None:
            return AgentDecision(
                reply="Vou encaminhar sua mensagem para um atendente confirmar essa informação.",
                handoff=True,
                handoff_reason="OPENAI_API_KEY não configurada",
                confidence="low",
            )

        context_lines = []
        for item in (history or [])[-12:]:
            role = "Cliente" if item.get("role") == "user" else "Atendente virtual"
            context_lines.append(f"{role}: {item.get('content', '')}")
        current = text.strip() or "O cliente enviou uma mensagem sem texto."
        if image_description:
            current += f"\nDescrição cautelosa da imagem: {image_description}"
        prompt = "\n".join([*context_lines, f"Cliente: {current}"])
        try:
            result = await Runner.run(self.agent, prompt, max_turns=6)
            output = result.final_output
            decision = output if isinstance(output, AgentDecision) else AgentDecision.model_validate(output)
            return protect_customer_decision(self._sanitize_image_urls(decision))
        except Exception as exc:
            return AgentDecision(
                reply="Vou encaminhar sua mensagem para um atendente confirmar essa informação.",
                handoff=True,
                handoff_reason=f"Falha no agente: {type(exc).__name__}",
                confidence="low",
            )

    def _try_payment_link(self, text: str) -> AgentDecision | None:
        if not _is_payment_link_request(text):
            return None
        if _is_payment_link_rate_request(text):
            rates = self.faq.get("taxas_link_pagamento")
            if rates:
                return AgentDecision(
                    reply=f"{PAYMENT_LINK_REPLY}\n\n{rates}",
                    confidence="high",
                )
        reply = self.faq.get("link_pagamento") or PAYMENT_LINK_REPLY
        return AgentDecision(reply=reply, confidence="high")

    async def _try_available_products(self, text: str) -> AgentDecision | None:
        if not _is_available_list_request(text):
            return None
        method = getattr(self.cache, "list_available_products", None)
        if not callable(method):
            return None
        try:
            result = await method()
        except Exception:
            return None
        if not result.get("encontrado"):
            return None
        return AgentDecision(reply=_format_available_products(result), confidence="high")

    async def _try_product_availability(
        self,
        text: str,
        *,
        image_description: str | None = None,
    ) -> AgentDecision | None:
        query = _current_catalog_context(text, image_description)
        if not _is_product_availability_request(query):
            return None

        try:
            candidates = await self.cache.search(query, limit=30)
        except Exception:
            return None

        public_candidates = [
            item
            for item in candidates
            if _is_device_item(item)
            and (
                getattr(item, "source", None) != "mercado_phone"
                or _is_available_item(item)
            )
        ]
        requested_capacity = _requested_capacity_key(query)
        if requested_capacity:
            public_candidates = [
                item
                for item in public_candidates
                if _capacity_key(getattr(item, "capacity", None)) == requested_capacity
            ]

        if not public_candidates:
            capacity_text = f" {requested_capacity.upper()}" if requested_capacity else ""
            return AgentDecision(
                reply=(
                    f"No momento não localizei uma opção cadastrada{capacity_text} para esse produto. "
                    "Pode me informar outro modelo ou capacidade?"
                ),
                confidence="medium",
            )

        scored = [(_catalog_score(query, item), item) for item in public_candidates]
        best_score = max(score for score, _item in scored)
        if best_score <= 0:
            return AgentDecision(
                reply="No momento não localizei esse produto no catálogo. Pode me informar o modelo ou capacidade?",
                confidence="medium",
            )

        top_matches = [item for score, item in scored if score == best_score]
        selected = top_matches[:3]
        references = [str(getattr(item, "external_id", "")) for item in selected]
        return AgentDecision(
            reply=_format_product_availability(selected),
            product_references=[reference for reference in references if reference],
            confidence="high",
        )

    def _try_current_day_information(self, text: str) -> AgentDecision | None:
        if _is_current_date_request(text):
            return AgentDecision(reply=f"Hoje é {_today_label()}.", confidence="high")
        if _is_today_store_status_request(text):
            return AgentDecision(
                reply=_today_store_reply(self.faq, include_physical_store=False),
                confidence="high",
            )
        return None

    def _try_visit_scheduling(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        is_visit = _is_visit_request(text)
        is_reservation = _is_reservation_request(text)
        is_followup = _is_appointment_followup(text, history)
        if not (is_visit or is_reservation or is_followup):
            return None

        if is_reservation and not is_visit:
            reply = _reservation_reply(self.faq)
            return AgentDecision(reply=reply, confidence="high")

        context = _appointment_context(text, history)
        if _has_visit_date_reference(context) and _has_visit_time_reference(context):
            address = self.faq.get("address") or (
                "Avenida Nossa Senhora da Luz, 1341 - Jardim Social, Curitiba - PR, 82520-060"
            )
            return AgentDecision(
                reply=(
                    "Perfeito 😊 Vou registrar a solicitação da sua visita para o dia e horário "
                    "informados e encaminhar para um atendente confirmar. O atendimento é feito "
                    "com horário marcado. "
                    f"Endereço: {address}"
                ),
                handoff=True,
                handoff_reason="Agendamento de visita solicitado; atendente deve confirmar o horário",
                confidence="high",
            )

        reply = _today_store_reply(self.faq)
        return AgentDecision(reply=reply, confidence="high")

    def _try_physical_store(self, text: str) -> AgentDecision | None:
        if not _is_physical_store_request(text):
            return None
        reply = _today_store_reply(self.faq)
        return AgentDecision(reply=reply, confidence="high")

    async def _try_warranty(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        if not _is_warranty_request(text):
            return None

        normalized = _normalize(text)
        has_sealed = _has_sealed_reference(normalized)
        has_seminovo = _has_seminovo_reference(normalized)
        kind: str | None = None
        if has_sealed and not has_seminovo:
            kind = "sealed"
        elif has_seminovo and not has_sealed:
            kind = "seminovo"
        else:
            previous_user_messages = [
                item.get("content", "").strip()
                for item in (history or [])
                if item.get("role") == "user" and item.get("content", "").strip()
            ]
            has_product_context = _has_product_reference(normalized) or any(
                _has_product_reference(_normalize(message))
                for message in previous_user_messages[-3:]
            )
            if has_product_context:
                try:
                    items = await self.cache.search(
                        _installment_context_query(text, history),
                        limit=3,
                    )
                except Exception:
                    items = []
                if items:
                    kind = "sealed" if _is_sealed_item(items[0]) else "seminovo"

        include_accessories = "acompanha" in normalized
        if kind == "sealed":
            reply = self.faq.get("garantia_lacrados") or (
                "Produtos novos lacrados têm garantia de 1 ano pela Apple."
            )
            if include_accessories:
                reply = f"{reply} {self.faq.get('lacrados')}".strip()
        elif kind == "seminovo":
            reply = self.faq.get("garantia_seminovos") or (
                "Produtos seminovos têm garantia de 90 dias."
            )
            if include_accessories:
                reply = f"{reply} {self.faq.get('seminovos')}".strip()
        else:
            reply = self.faq.get("garantia") or (
                "Produtos seminovos têm garantia de 90 dias. "
                "Produtos novos lacrados têm garantia de 1 ano pela Apple."
            )
            if include_accessories:
                reply = (
                    f"{reply} {self.faq.get('seminovos')} "
                    f"{self.faq.get('lacrados')}"
                ).strip()
        return AgentDecision(reply=reply, confidence="high")

    async def _try_product_photos(
        self,
        text: str,
        history: list[dict[str, str]] | None,
        *,
        image_description: str | None = None,
    ) -> AgentDecision | None:
        if not _is_photo_request(text):
            return None
        if _is_photo_retry_request(text) and not _has_photo_request_in_history(history):
            return None
        if _is_sealed_photo_request(text):
            return AgentDecision(reply=SEALED_PHOTO_REPLY, confidence="high")
        query = _product_context_query(
            _current_catalog_context(text, image_description),
            history,
        )
        finder = getattr(self.cache, "find_product_photos", None)
        if callable(finder):
            try:
                selected = await finder(query)
            except Exception:
                return None
            if selected is None:
                fallback = []
                sealed_cache = getattr(self.cache, "sealed_cache", None)
                if sealed_cache is not None:
                    try:
                        ensure_fresh = getattr(sealed_cache, "ensure_fresh", None)
                        if callable(ensure_fresh):
                            await ensure_fresh()
                        search_sealed = getattr(sealed_cache, "search", None)
                        if callable(search_sealed):
                            fallback = await search_sealed(query, limit=5)
                        else:
                            fallback = list(getattr(sealed_cache, "items", []))[:5]
                    except Exception:
                        fallback = []
                if not fallback:
                    try:
                        fallback = await self.cache.search(query, limit=5)
                    except Exception:
                        fallback = []
                if fallback and _is_sealed_item(fallback[0]):
                    return AgentDecision(reply=SEALED_PHOTO_REPLY, confidence="high")
                return None
            items = [selected]
        else:
            try:
                items = await self.cache.search(query, limit=5)
            except Exception:
                return None
        if items:
            selected = items[0]
            if _is_made_to_order_sealed_item(selected):
                return AgentDecision(reply=SEALED_PHOTO_REPLY, confidence="high")
            urls = list(dict.fromkeys(getattr(selected, "photo_urls", []) or []))[:MAX_PRODUCT_PHOTOS]
            if urls:
                return AgentDecision(
                    reply=f"Claro! Seguem as fotos do {selected.name}{f' {selected.capacity}' if selected.capacity else ''}.",
                    image_urls=urls,
                    product_references=[selected.external_id],
                    confidence="high",
                )
            return AgentDecision(
                reply=(
                    f"Encontrei o {selected.name}, mas ainda não há fotos cadastradas para esse modelo. "
                    "Posso encaminhar o pedido para um atendente confirmar as fotos."
                ),
                confidence="low",
            )
        return None

    async def _try_specific_installment(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        installments = _requested_installments(text)
        if installments is None:
            if not _is_installment_selection_followup(text, history):
                return None
            installments = _last_requested_installments(history)
        if installments is None:
            return None

        query = _installment_context_query(text, history)
        if not _has_installment_product_context(query):
            return None
        method = getattr(self.cache, "simulate_installment", None)
        if not callable(method):
            return None
        try:
            result = await method(query, installments)
        except Exception:
            return None
        if result.get("encontrado"):
            return AgentDecision(
                reply=format_installment_result(result),
                confidence="high",
            )
        if not result.get("ambiguo"):
            return None

        candidates = result.get("candidatos") or []
        lines: list[str] = []
        for candidate in candidates[:3]:
            if not isinstance(candidate, dict):
                continue
            name = str(candidate.get("nome") or "Produto")
            capacity = candidate.get("capacidade")
            battery = candidate.get("saude_bateria")
            price = candidate.get("preco_brl")
            details = [name]
            if capacity:
                details.append(str(capacity))
            if battery is not None:
                details.append(f"bateria {float(battery):g}%")
            if price is not None:
                details.append(format_brl(float(price)))
            lines.append(" - ".join(details))
        if not lines:
            return None
        return AgentDecision(
            reply=(
                "Encontrei mais de uma unidade compativel:\n"
                + "\n".join(lines)
                + "\nQual delas voce quer simular?"
            ),
            confidence="medium",
        )

    async def _try_full_installment_table(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        if not _is_full_installment_request(text):
            return None
        method = getattr(self.cache, "simulate_all_installments", None)
        if not callable(method):
            return None
        try:
            result = await method(_installment_context_query(text, history))
        except Exception:
            return None
        if not result.get("encontrado"):
            return None
        return AgentDecision(
            reply=format_installment_table(result),
            confidence="high",
        )

    async def _try_entry_installment(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        entry_amount = _extract_entry_amount(text)
        if entry_amount is None:
            return None

        query = _installment_context_query(text, history)
        installments = _requested_installments(text)
        try:
            if installments is not None:
                method = getattr(self.cache, "simulate_installment_with_entry", None)
                if not callable(method):
                    return None
                result = await method(query, entry_amount, installments)
                if result.get("encontrado"):
                    return AgentDecision(
                        reply=format_installment_result(result),
                        confidence="high",
                    )
                return None

            method = getattr(self.cache, "simulate_all_installments_with_entry", None)
            if not callable(method):
                return None
            result = await method(query, entry_amount)
            if result.get("encontrado"):
                return AgentDecision(
                    reply=format_installment_table(result),
                    confidence="high",
                )
        except Exception:
            return None
        return None

    async def _offline_response(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
        image_description: str | None = None,
    ) -> AgentDecision:
        combined = " ".join(part for part in [text, image_description] if part).strip()
        normalized = _normalize(combined)
        if any(word in normalized for word in ("atendente", "humano", "pessoa", "reclamacao")):
            return AgentDecision(
                reply="Vou encaminhar você para um atendente. Só um momento, por favor.",
                handoff=True,
                handoff_reason="Pedido explícito de atendimento humano",
                confidence="high",
            )
        query = _installment_context_query(combined, history)
        items = await self.cache.search(query, limit=3) if query else []
        if not items:
            return AgentDecision(
                reply="Não localizei esse produto no catálogo. Pode me informar o modelo, capacidade ou cor?",
                confidence="low",
            )
        lines = []
        for item in items:
            if item.price_brl is None:
                price = "preço a confirmar"
            else:
                price = format_brl(float(item.price_brl))
            availability = item.availability or ("Disponível" if (item.quantity or 0) > 0 else "Indisponível")
            condition = f" ({item.condition})" if item.condition else ""
            details: list[str] = []
            color = getattr(item, "color", None) or item.colors
            if color:
                details.append(f"cor: {color}")
            battery = getattr(item, "battery_health", None)
            details.append(
                f"saúde da bateria: {_format_battery(battery) if battery is not None else 'não informada no cadastro'}"
            )
            suffix = f" — {' — '.join(details)}" if details else ""
            lines.append(f"• {item.name}{condition} — {price} — {availability}{suffix}")
        return AgentDecision(reply="Encontrei estas opções:\n" + "\n".join(lines), confidence="medium")
