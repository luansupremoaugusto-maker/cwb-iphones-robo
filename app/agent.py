from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents import Agent, ModelSettings, Runner, function_tool, set_default_openai_key
from agents.tracing import set_tracing_disabled

from app.adapters.catalog_cache import (
    _MODEL_PATTERN,
    _catalog_score,
    _catalog_family,
    _catalog_families,
    _is_available_item,
    _is_accessory_catalog_query,
    _is_device_item,
    _is_sealed_accessory_item,
    _matches_requested_apple_watch_model,
    _matches_requested_model,
    _model_key,
    _requested_apple_watch_model_keys,
    _requested_battery_health,
    _requested_ipad_model_keys,
    _requested_iphone_model_floor,
    _requested_iphone_model_keys,
    _requested_photo_condition,
)
from app.adapters.mercado_phone import InventoryCache
from app.adapters.mercado_phone_files import MAX_PRODUCT_PHOTOS
from app.capacity import sort_capacity_variants
from app.config import Settings
from app.faq import FAQStore
from app.installments import (
    format_brl,
    format_installment_rates,
    format_installment_result,
    format_installment_table,
    simulate_installment,
    simulate_installment_with_entry,
)
from app.safety import protect_customer_decision
from app.schemas import AgentDecision
from app.trade_in import (
    CONDITION_HANDOFF_REASON,
    CONDITION_HANDOFF_REPLY,
    NON_APPLE_TRADE_IN_REPLY,
    PARTS_BUYBACK_REPLY,
    PURCHASE_WITHOUT_TRADE_IN_REPLY,
    TRADE_IN_FORM,
    TRADE_IN_NEGOTIATION_REPLY,
    TRADE_IN_REASON,
    is_trade_in_negotiation,
    is_completed_trade_in_form,
    is_box_inclusion_question,
    is_trade_in_context_request,
    is_trade_in_request,
    has_explicit_trade_in_cue,
    is_photo_offer_confirmation,
    is_parts_buyback_request,
    is_non_apple_trade_in_request,
    is_catalog_purchase_advice_request,
    catalog_price_recall_amount,
    trade_in_em_andamento,
    is_contextual_generic_device_purchase_request,
    is_purchase_without_trade_in_request,
)


STORE_TIMEZONE = "America/Sao_Paulo"
TECHNICAL_ASSISTANCE_REPLY = (
    "A assistência técnica, incluindo troca de bateria, tela e outros reparos, "
    "é tratada por um atendente. Vou encaminhar sua mensagem para ele confirmar "
    "valores e disponibilidade."
)
TECHNICAL_ASSISTANCE_REASON = "Solicitação de assistência técnica ou reparo"
CATALOG_BUYER_DETAILS_REPLY = (
    "Vou encaminhar suas dúvidas sobre esse aparelho para um atendente confirmar "
    "essas informações com você."
)
CATALOG_BUYER_DETAILS_REASON = "Dúvidas sobre detalhes de um aparelho disponível"
PRODUCT_PHOTO_HANDOFF_REASON = "Atendente deve confirmar as fotos do produto selecionado"
CATALOG_PRICE_NEGOTIATION_REPLY = (
    "Vou encaminhar sua pergunta sobre o valor desse aparelho para um atendente "
    "confirmar essa negociação com você."
)
CATALOG_PRICE_NEGOTIATION_REASON = "Negociação de preço de aparelho disponível"
CATALOG_BUDGET_OBJECTION_REPLY = (
    "Entendo 😊 Se quiser, me diga qual faixa de valor cabe no seu orçamento "
    "que procuro opções mais em conta."
)
DELIVERY_FEE_HANDOFF_REPLY = (
    "Vou encaminhar sua mensagem para um atendente confirmar a taxa e o prazo de entrega."
)
DELIVERY_FEE_HANDOFF_REASON = "Taxa e prazo de entrega precisam ser cotados com um atendente"
CASE_ACCESSORY_REPLY = (
    "Sim 😊 Conseguimos capinhas, películas e protetores de câmera por R$ 10,00 cada."
)
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
- A aba BOT e a fonte exclusiva para quais modelos novos lacrados podem
  ser oferecidos. Nunca ofereca, confirme valor ou diga que vai consultar um
  lacrado que nao aparece nela. Se o lacrado pedido nao estiver na aba,
  informe isso e ofereca as alternativas retornadas pelo catalogo, sem
  encaminhar automaticamente para um atendente.
- Para aparelhos novos lacrados, informe que trabalhamos por encomenda, com prazo
  de entrega de 1 semana. Em qualquer envio, inclusive por motoboy ou Sedex,
  o pagamento deve ser antecipado antes do despacho. Somente na retirada na loja
  o pagamento é feito na hora. Nunca diga que um lacrado pode ser pago na entrega
  quando o cliente estiver pedindo envio.
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
  pronta completa, separando seminovos em estoque, lacrados a pronta entrega e
  novos lacrados por encomenda.
  Nos seminovos, mostre cada registro individualmente, sem agrupar modelos iguais;
  informe cor, capacidade, estado do produto e saúde da bateria em cada linha.
  Pode usar o nome do modelo como cabeçalho e sublinhas individuais para cada
  aparelho, mas nunca esconda opções diferentes dentro de uma única linha.
  Só considere seminovos marcados como “Disponível para venda” no Mercado Phone;
  nunca mostre Laboratório, teste ou outro status não comercial. Se algum detalhe
  não estiver cadastrado, diga isso. Não envie somente alguns exemplos.
- Quando o cliente pedir preço ou valores de um modelo sem especificar a condição,
  se houver opção seminova em estoque e opção nova lacrada, envie as duas, separadas
  e identificadas. Só envie uma condição quando o cliente pedir explicitamente
  seminovo/usado ou lacrado/por encomenda.
- Não ofereça fotos espontaneamente em uma listagem com várias unidades. Para um
  aparelho selecionado, só ofereça fotos depois de get_product_photos confirmar
  URLs daquele registro exato; preserve capacidade, cor, condição, bateria e preço
  que identificam a unidade. Se o cliente confirmar uma oferta de fotos, trate a
  confirmação como pedido para esse mesmo registro.
- Quando o cliente pedir ou confirmar fotos, use get_product_photos. Só envie
  image_urls que vierem do catálogo ou da lista aprovada de fotos; nunca invente
  links ou use fotos de outro modelo. Se não houver foto cadastrada, informe isso
  sem prometer uma foto e ofereça encaminhar ao atendente.
- Quando o cliente perguntar se a loja compra algum produto, responda que sim,
  somente produtos da marca Apple, envie o formulário de avaliação aprovado e
  defina handoff=true.
- Se o cliente mencionar boleto junto com parcelamento ou quantidade de parcelas,
  informe que não parcelamos no boleto e que o parcelamento é somente no cartão
  de crédito na máquina física. Não use ferramentas de simulação nem envie tabela
  de parcelas nesse caso.
- Sempre que o cliente perguntar como fica o parcelamento, quanto fica parcelado,
  quantas vezes fica, quais são as parcelas ou pedir uma simulação, use
  simulate_all_installments e
  envie a tabela completa de 1x até 18x. Isso vale mesmo quando ele mencionar
  uma quantidade específica, como 5x, 6x, 12x ou 18x: a quantidade é uma
  referência para a dúvida, não um filtro para esconder as demais opções.
  Não pergunte em quantas vezes o cliente quer parcelar. Se a pergunta mencionar
  link de pagamento ou cartão online, informe que essa modalidade não é mais aceita
  e não faça simulação ou cálculo de parcelas pelo link.
- Se o cliente perguntar se valores de uma simulação já enviada incluem juros,
  confirme que os valores parcelados já incluem a taxa da máquina. O preço à vista
  aparece separado e o total varia conforme a quantidade de parcelas.
- Fora do contexto de uma simulação já enviada, se o cliente perguntar se o
  parcelamento no cartão tem juros ou se é sem juros, responda diretamente que há a
  taxa da máquina e que o acréscimo varia conforme o valor e a quantidade de
  parcelas. Não informe percentuais nem peça modelo/capacidade só para responder;
  ofereça uma simulação como próximo passo se houver produto e preço confirmados.
- Se o cliente informar uma entrada à vista ou um sinal e quiser parcelar o
  restante, subtraia a entrada do preço total e use somente o saldo restante
  como base do cálculo. Use sempre simulate_all_installments_with_entry para
  enviar a tabela completa de 1x até 18x sobre o saldo. Nunca aplique as taxas
  sobre o preço cheio depois que uma entrada tiver sido informada.
- Os valores de parcelamento no cartão da máquina física valem para qualquer produto
  da loja, desde que exista preço confirmado. Para lacrados, o preço vem da aba BOT;
  para os demais produtos, o preço vem do catálogo do Mercado Phone.
- Se o cliente perguntar sobre nota fiscal, informe que podemos emitir nota fiscal
  para todos os produtos, sejam seminovos ou lacrados.
- Aceitamos PIX, dinheiro, cartão de débito e cartão de crédito. PIX, dinheiro e
  cartão de débito têm pagamento integral à vista, sem taxas. O cliente pode usar
  mais de um cartão de crédito na mesma compra e completar o valor com PIX,
  dinheiro ou cartão de débito.
- O cliente pode pagar a mesma compra usando mais de um cartão de crédito, se desejar.
- Se o cliente perguntar sobre link de pagamento, cartão de crédito online, pagamento
  à distância ou pela internet, informe que não aceitamos mais link de pagamento nem
  pagamento por cartão online. Oriente para pagamento com cartão na máquina física,
  PIX, dinheiro ou cartão de débito. Não informe percentuais de taxas.
- Se o produto ou capacidade for ambíguo, peça a escolha antes de calcular.
- Para pedidos específicos ambíguos, apresente no máximo três candidatos e peça
  modelo, capacidade, cor ou outro detalhe. Para pedidos genéricos, listas, faixa de
  preço, orçamento ou quantidade de aparelhos, mostre todas as opções disponíveis
  que atendam aos filtros informados, sem limitar a três. Se o cliente pedir vários
  aparelhos, informe todas as opções compatíveis e peça que ele escolha a quantidade
  solicitada.
- Não defina handoff apenas porque o cliente quer comprar e retirar em outro dia.
  Continue no atendimento automático e peça a escolha dos aparelhos e o horário;
  só encaminhe quando houver pedido explícito de atendente ou uma confirmação de
  visita que, pelas regras acima, precise de atendente.
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
    # Keep the Portuguese copula in phrases such as "15 é original" from
    # becoming the model alias "15 e" during accent removal.
    value = re.sub(r"(?<=\d)\s+é\b", " __copula__ ", value or "", flags=re.IGNORECASE)
    without_accents = "".join(
        char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char)
    )
    normalized = re.sub(r"\s+", " ", without_accents).strip().lower()
    normalized = re.sub(r"(?<=\d)(?=[a-z])", " ", normalized)
    return re.sub(r"\bpromax\b", "pro max", normalized)


def _without_installment_count_list(normalized: str) -> str:
    if not re.search(
        r"\b(?:simul\w*|sublim\w*|calcul\w*|parcel\w*|"
        r"(?:1[0-8]|[1-9])\s*(?:x|vezes))\b",
        normalized,
    ):
        return normalized

    count = r"(?:1[0-8]|[1-9])"
    separator = r"(?:\s*[,/]\s*|\s+(?:e|ou)\s+)"
    count_list = re.compile(
        rf"\b{count}(?:\s*x)?(?:{separator}(?:em\s+)?{count}(?:\s*x)?)*"
        rf"{separator}(?:em\s+)?{count}\s*(?:x|vezes|parcelas?)\b"
    )
    match = count_list.search(normalized)
    while match:
        prefix = normalized[: match.start()]
        if not re.search(
            r"\b(?:iphones?|ipads?|macbooks?|airpods?|apple\s+watch)\s*$",
            prefix,
        ):
            return normalized[: match.start()] + " " + normalized[match.end() :]

        model_number = re.match(r"\d{1,2}", match.group())
        if model_number is None:
            return normalized
        match = count_list.search(normalized, match.start() + model_number.end())
    return normalized


def _has_product_reference(normalized: str) -> bool:
    # A bare number is not enough to identify a product: values such as
    # "1k" (entry) and "18x" (installments) are common in the same chat.
    # Keep numeric shorthand only when it carries a model variant or an
    # explicit catalog condition, such as "16 novo" or "16 lacrado".
    model_reference_text = _without_installment_count_list(normalized)
    return bool(
        _catalog_families(normalized)
        or re.search(r"\b\d{1,2}\s+(?:e|pro|max|air|mini|plus)\b", model_reference_text)
        or re.search(
            r"\b\d{1,2}\s+(?:novo|nova|lacrado|lacrados|encomenda|"
            r"seminovo|seminovos|usado|usados)\b",
            normalized,
        )
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


_SEMINOVO_TRADE_IN_CATALOG_TARGET_RE = re.compile(
    r"\b(?:procur(?:ando|ava|ar|o|a)|busc(?:ando|ava|ar|o|a)|"
    r"quer(?:endo|ia|o|er)|gostaria\s+de)\b.{0,48}?"
    r"\b(?:semi(?:[\s-]*nov[oa]s?)?|usad[oa]s?)\b\s+"
    r"(?P<models>\d{1,2}(?:\s*(?:pro\s*max|pro|max|plus|mini|air|e|se))?"
    r"(?:\s*/\s*\d{1,2}(?:\s*(?:pro\s*max|pro|max|plus|mini|air|e|se))?)?)"
    r"(?=\s*(?:para|pra|na)\s+troca\b|\s*(?:[,.;!?]|$))",
    re.IGNORECASE,
)


def _seminovo_trade_in_catalog_target_query(text: str) -> str | None:
    """Isolate requested used upgrade models from details about the owned phone."""
    match = _SEMINOVO_TRADE_IN_CATALOG_TARGET_RE.search(text or "")
    if not match:
        return None
    models = re.sub(r"\s*/\s*", "/", match.group("models").strip())
    return f"iPhone {models} seminovo disponíveis"


def _visual_catalog_search_query(
    image_description: str | None,
    items: list[Any] | None = None,
) -> str | None:
    """Reduce OCR from a product card to its model, capacity, and color."""
    if not image_description:
        return None
    normalized = _normalize(image_description)
    explicit_models: set[tuple[int | str, str]] = set()
    for match in _MODEL_PATTERN.finditer(normalized):
        if not match.group(0).lower().startswith("iphone"):
            continue
        legacy = match.group("legacy")
        if legacy:
            explicit_models.add((legacy.lower(), ""))
        elif match.group("number") is not None:
            explicit_models.add(
                (
                    int(match.group("number")),
                    " ".join((match.group("variant") or "").split()).lower(),
                )
            )
    if len(explicit_models) > 1:
        return None
    models = _requested_iphone_model_keys(image_description)
    if len(models) != 1:
        return None
    number, variant = models[0]
    parts = ["iPhone", str(number), variant]
    capacities: list[str] = []
    for match in re.finditer(r"\b\d+(?:[.,]\d+)?\s*(?:gb|tb)\b", normalized):
        for capacity in _requested_capacity_keys(match.group(0)):
            if capacity not in capacities:
                capacities.append(capacity)
    if len(capacities) == 1:
        match = re.fullmatch(r"(?P<number>\d+(?:\.\d+)?)(?P<unit>gb|tb)", capacities[0])
        if match:
            parts.extend((match.group("number"), match.group("unit").upper()))
    color = _current_catalog_color(image_description, items or [])
    if color:
        parts.append(color)
    return " ".join(part for part in parts if part)


def _visual_iphone_model_keys(
    image_description: str,
) -> tuple[tuple[int | str, str], ...]:
    """Read every explicitly labeled iPhone model from a catalog image description."""
    models: list[tuple[int | str, str]] = []
    for match in _MODEL_PATTERN.finditer(_normalize(image_description)):
        if not match.group(0).lower().startswith("iphone"):
            continue
        legacy = match.group("legacy")
        model = (
            (legacy.lower(), "")
            if legacy
            else (
                int(match.group("number")),
                " ".join((match.group("variant") or "").split()).lower(),
            )
        )
        if model not in models:
            models.append(model)
    return tuple(models)


def _visual_iphone_model_segments(
    image_description: str,
) -> dict[tuple[int | str, str], tuple[str, ...]]:
    """Keep each product's visual attributes with its labeled iPhone model."""
    normalized = _normalize(image_description)
    mentions: list[tuple[tuple[int | str, str], int]] = []
    for match in _MODEL_PATTERN.finditer(normalized):
        if not match.group(0).lower().startswith("iphone"):
            continue
        legacy = match.group("legacy")
        model = (
            (legacy.lower(), "")
            if legacy
            else (
                int(match.group("number")),
                " ".join((match.group("variant") or "").split()).lower(),
            )
        )
        mentions.append((model, match.start()))

    segments: dict[tuple[int | str, str], list[str]] = {}
    for index, (model, start) in enumerate(mentions):
        end = mentions[index + 1][1] if index + 1 < len(mentions) else len(normalized)
        segments.setdefault(model, []).append(normalized[start:end].strip(" ,;"))
    return {model: tuple(values) for model, values in segments.items()}


def _explicit_image_availability_query(
    text: str,
    image_description: str | None,
) -> str | None:
    """Use only model identity from a photo when the customer clearly asks about stock."""
    normalized_text = _normalize(text)
    if (
        not image_description
        or not re.search(r"\b(?:disponivel|disponibilidade|estoque)\b", normalized_text)
        or not re.search(r"\b(?:tem|teria|possui|sobrou|restou)\b", normalized_text)
    ):
        return None
    if re.search(
        r"\b(?:foto|fotos|imagem|imagens|parcel\w*|entrada|sinal|garantia|"
        r"reserva|endereco|horario|nota\s+fiscal|preco|valor|quanto|"
        r"bateria|cor|\d+\s*(?:gb|tb))\b",
        normalized_text,
    ):
        return None
    if not re.search(
        r"\b(?:desse|desses|dessa|dessas|dele|deles|dela|delas)\b",
        normalized_text,
    ):
        return None

    normalized_image = _normalize(image_description)
    if any(family != "iphone" for family in _catalog_families(normalized_image)):
        return None
    models = _visual_iphone_model_keys(image_description)
    if not models:
        return ""
    query = ", ".join(
        " ".join(part for part in ("iPhone", str(number), variant) if part)
        for number, variant in models
    )
    if not _has_seminovo_reference(text) and not _has_explicit_sealed_condition(text):
        image_seminovos = _has_seminovo_reference(image_description)
        image_sealed = _has_explicit_sealed_condition(image_description)
        if image_seminovos != image_sealed:
            query += " seminovo" if image_seminovos else " lacrado"
    return query


def _is_catalog_followup(text: str) -> bool:
    """Recognize a short price/condition/color follow-up for a prior product."""
    normalized = _normalize(text)
    if not normalized:
        return False
    if any(
        marker in normalized
        for marker in (
            "lacrado",
            "encomenda",
            "novo",
            "seminovo",
            "semi novo",
            "usado",
            "valor",
            "valores",
            "preco",
            "precos",
            "disponivel",
            "disponibilidade",
            "estoque",
            "tem no",
            "tem na",
            "tem em",
            "fonte",
            "carregador",
            "cor",
            "cores",
        )
    ):
        return True
    # A capacity-only availability answer such as "Tb tem de 256gb" refers
    # to the product discussed immediately before it.
    return bool(
        re.search(r"\bem\s+conta\b", normalized)
        or re.search(
            r"\btem\s+de\s+\d+(?:[.,]\d+)?\s*(?:gb|tb|g|t)\b",
            normalized,
        )
    )


def _is_catalog_price_followup(text: str) -> bool:
    normalized = _normalize(text)
    if not re.search(
        r"\b(?:valor(?:es)?|precos?|custa|quanto\s+(?:custa|fica|sai))\b",
        normalized,
    ):
        return False
    if (
        _has_product_reference(normalized)
        or _is_accessory_catalog_request(normalized)
        or re.search(r"\b(?:parcel\w*|juros|\d+\s*x|entrada)\b", normalized)
    ):
        return False
    has_deictic_reference = bool(
        re.search(r"\b(?:dele|dela|desse|dessa|esse|essa|disto|disso)\b", normalized)
    )
    short_price_question = bool(
        re.fullmatch(
            r"(?:e\s+)?(?:(?:qual|me\s+fala)\s+)?(?:o\s+)?(?:valor|precos?)\s*[?!.,]*"
            r"|(?:e\s+)?quanto(?:\s+(?:ele|ela))?\s+(?:custa|fica|sai)\s*[?!.,]*",
            normalized,
        )
    )
    return has_deictic_reference or short_price_question


def _is_other_color_request(text: str) -> bool:
    return bool(re.search(r"\boutra(?:s)?\s+cor(?:es)?\b", _normalize(text)))


def _is_catalog_color_list_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:quais|todas|lista(?:r)?)\s+(?:(?:sao|seriam)\s+)?(?:as\s+)?cores\b",
            _normalize(text),
        )
    )


def _is_battery_detail_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or "bateria" not in normalized:
        return False
    return bool(
        re.search(
            r"\b(?:original|originais?|trocad\w*|substitu\w*|saude|percentual|porcentagem|"
            r"quanto|qto|boa|ruim)\b",
            normalized,
        )
    )


def _is_catalog_availability_confirmation(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Recognize a short confirmation after an explicit availability prompt."""
    normalized = re.sub(r"[^\w\s]", " ", _normalize(text), flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized not in {"sim", "sim por favor", "pode", "pode sim", "claro", "ok", "okay", "beleza"}:
        return False

    for entry in reversed(history or []):
        if entry.get("role") != "assistant" or not entry.get("content"):
            continue
        answer = _normalize(entry.get("content", ""))
        if not _has_product_reference(answer):
            return False
        has_consultation_prompt = bool(re.search(r"\b(?:consultar|verificar|confirmar)\b", answer))
        has_availability_marker = bool(re.search(r"\b(?:disponibilidade|disponivel|estoque)\b", answer))
        has_other_flow = any(marker in answer for marker in ("foto", "imagem", "atendente", "encaminhar"))
        return has_consultation_prompt and has_availability_marker and not has_other_flow
    return False


def _extract_catalog_product_id(text: str) -> str | None:
    """Extract the stock item id from a Mercado Phone catalog message."""
    normalized = _normalize(text)
    url_match = re.search(r"[?&]\s*produto_id\s*=\s*(\d+)", normalized)
    if url_match:
        return url_match.group(1)

    code_match = re.search(
        r"\bcodigo\s*(?:\(\s*estoque\s*\)|estoque)?\s*[:#-]\s*(\d{4,})\b",
        normalized,
    )
    return code_match.group(1) if code_match else None


def _has_pronta_entrega_reference(text: str) -> bool:
    return bool(re.search(r"\bpronta\s+entrega\b", _normalize(text)))


def _is_pronta_entrega_only_request(text: str) -> bool:
    normalized = _normalize(text)
    return _has_pronta_entrega_reference(text) and not re.search(r"\bencomenda\b", normalized)


def _is_contextual_pronta_entrega_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Recognize a short ready-stock question about the product just discussed."""
    normalized = _normalize(text)
    if (
        not normalized
        or not _has_pronta_entrega_reference(text)
        or not re.search(
            r"\b(?:algum|alguma|nenhum|nenhuma|tem|teria|disponivel|"
            r"disponibilidade|estoque|existe|existem)\b",
            normalized,
        )
        or re.search(
            r"\b(?:sedex|motoboy|fretes?|taxas?|envio|enviar|envia\w*|"
            r"mandar|entregam|entregar|prazo)\b",
            normalized,
        )
    ):
        return False
    if _has_product_reference(normalized):
        return True
    context_query = _product_context_query(text, history)
    return _has_product_reference(_normalize(context_query))


def _is_available_list_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if _is_variant_catalog_list_request(text):
        return True
    if _is_generic_iphone_list_request(text):
        return True
    if _has_product_reference(normalized):
        return False
    phrases = (
        "o que tem disponivel",
        "o que voce tem",
        "o que vc tem",
        "o que voces tem",
        "o que a loja tem",
        "quais modelos",
        "quais aparelhos",
        "lista completa",
        "lista de modelos",
        "me manda a lista",
        "me passe a lista",
        "catalogo",
        "tabela de preco",
        "tabela de precos",
        "tabela de valor",
        "tabela de valores",
    )
    return any(phrase in normalized for phrase in phrases)


def _is_variant_catalog_list_request(text: str) -> bool:
    """Recognize a list request for a variant without a generation number."""
    normalized = _normalize(text)
    if not re.search(r"\bpro\s+max\b", normalized):
        return False
    if _requested_iphone_model_keys(text):
        return False
    if _requested_catalog_colors(text, []):
        return False
    return bool(
        re.search(
            r"\b(?:quais?|opcoes?|modelos?|lista|tem|teria|disponivel|"
            r"disponibilidade|estoque|valor|valores|preco|precos)\b",
            normalized,
        )
    )


def _is_all_iphone_17_line_request(text: str) -> bool:
    """Recognize an explicit request for every iPhone 17 line variant."""
    normalized = _normalize(text)
    if _requested_iphone_model_keys(normalized) != ((17, ""),):
        return False
    if _is_accessory_catalog_request(normalized):
        return False
    if not re.search(r"\biphones?\s+17\b", normalized):
        return False
    return bool(
        re.search(
            r"\btodos?\s+(?:eles|os\s+(?:modelos?|iphones?))\b|"
            r"\btodas?\s+(?:as\s+)?(?:opcoes|versoes|variantes)\b|"
            r"\b(?:linha|geracao|familia)\s+(?:completa|17)\b|"
            r"\blinha\s+completa\b",
            normalized,
        )
    )


def _is_generic_iphone_list_request(text: str) -> bool:
    normalized = _normalize(text)
    if not re.search(r"\biphones?\b", normalized):
        return False
    all_line_request = _is_all_iphone_17_line_request(text)
    if _requested_iphone_model_keys(text) and not all_line_request:
        return False
    if _is_accessory_catalog_request(normalized):
        return False
    if _requested_catalog_colors(text, []):
        return False
    if (
        re.search(r"\bate\b", normalized)
        or any(
            marker in normalized
            for marker in (
                "faixa de",
                "orcamento",
                "em torno de",
                "cerca de",
                "por volta de",
                "comprar",
                "retirar",
                "aparelho",
                "unidade",
                "quantos",
                "preciso de",
                "necessito de",
            )
        )
        or ("entrega" in normalized and not _has_pronta_entrega_reference(text))
    ):
        return False
    return all_line_request or bool(
        re.search(
            r"\b(?:modelo|modelos|preco|precos|valor|valores|lista|catalogo|"
            r"tem|vende|possui|disponivel|disponibilidade|ver|mostrar|mostre|"
            r"a\s+venda)\b",
            normalized,
        )
    )


def _is_sealed_catalog_list_request(text: str) -> bool:
    """Recognize a category-level request for all sealed catalog prices."""
    normalized = _normalize(text)
    if not normalized or _has_product_reference(normalized):
        return False
    if _has_seminovo_reference(normalized):
        return False
    new_model_list_request = _is_new_model_list_request(normalized)
    if not new_model_list_request and not re.search(r"\blacrados\b", normalized):
        return False
    return bool(
        re.search(
            r"\b(?:quanto|qual|quais|preco|precos|valor|valores|lista|tabela|"
            r"tem|vende|possui|disponiveis?|disponibilidade|estoque)\b",
            normalized,
        )
    )


def _is_new_model_list_request(text: str) -> bool:
    """Recognize broad requests for new models without an explicit model."""
    normalized = _normalize(text)
    return bool(
        normalized
        and not _has_product_reference(normalized)
        and not _has_seminovo_reference(normalized)
        and re.search(r"\b(?:modelos|aparelhos|celulares|iphones)\b", normalized)
        and re.search(r"\bnov[oa]s?\b", normalized)
    )


def _is_sealed_iphone_list_request(text: str) -> bool:
    """Recognize a sealed-cell-phone request that must exclude Apple accessories."""
    normalized = _normalize(text)
    if not normalized:
        return False
    if not re.search(r"\bcelulares?\b|\biphones?\b", normalized):
        return False
    if not re.search(r"\blacrados?\b", normalized):
        return False
    requested_models = _requested_iphone_model_keys(normalized)
    if requested_models and not _is_all_iphone_17_line_request(normalized):
        return False
    return bool(
        re.search(
            r"\b(?:quanto|qual|quais|preco|precos|valor|valores|lista|tabela|"
            r"tem|vende|possui|disponivel|disponibilidade|estoque)\b",
            normalized,
        )
    )


def _is_broad_airpods_request(text: str) -> bool:
    """Recognize a family-level AirPods query that should show every option."""
    normalized = _normalize(text)
    if not re.search(r"\bairpods?\b|\bair pods\b", normalized):
        return False
    return not re.search(r"\b(?:pro|anc|max|mini|plus|\d+)\b", normalized)


def _is_cheapest_catalog_request(text: str) -> bool:
    """Recognize a request for the lowest-priced available catalog item."""
    normalized = _normalize(text)
    if not normalized:
        return False
    if not re.search(
        r"\bmais\s+barat[oa]\b"
        r"|\bmais\s+em\s+conta\b"
        r"|\b(?:um|uma)\s+(?:mais\s+)?em\s+conta\b"
        r"|\bmenor\s+(?:preco|valor)\b",
        normalized,
    ):
        return False
    return not bool(
        re.search(
            r"\b(?:quais|todos|todas|iphones|modelos|opcoes|aparelhos|unidades)\b",
            normalized,
        )
    )


def _confirmed_catalog_price(item: Any) -> float | None:
    try:
        price = float(getattr(item, "price_brl", None))
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price >= 0 else None


def _is_explicit_catalog_price_request(text: str) -> bool:
    normalized = _normalize(text)
    return bool(
        normalized
        and _has_product_reference(normalized)
        and re.search(
            r"\b(?:precos?|valor(?:es)?|custa|cotacao)\b|\bquanto\s+(?:custa|fica)\b",
            normalized,
        )
    )


def _is_ipad_model_alternative_price_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    if len(_requested_ipad_model_keys(text)) <= 1:
        return False
    request_context = _customer_product_request_context_query(text, history)
    return _is_explicit_catalog_price_request(request_context)


def _is_independent_payment_methods_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if re.search(r"\b(?:forma[s]?|metodo[s]?|meio[s]?)\s+de\s+pagamento\b", normalized):
        return True
    asks_about_payment_methods = re.search(
        r"\b(?:aceita(?:m)?|posso\s+pagar|pagar\s+com)\b",
        normalized,
    )
    names_payment_method = re.search(
        r"\b(?:pix|dinheiro|debito|credito|cartao|cartoes)\b",
        normalized,
    )
    return bool(asks_about_payment_methods and names_payment_method)


def _is_product_availability_request(
    text: str,
    history: list[dict[str, str]] | None = None,
    *,
    allow_buyer_interest_with_image: bool = False,
    buyer_interest_text: str | None = None,
) -> bool:
    """Route a product-specific availability question without an LLM guess."""
    normalized = _normalize(text)
    accessory_request = _is_accessory_catalog_request(normalized)
    bare_model_request = _is_bare_model_availability_request(text)
    catalog_followup = _is_bare_model_catalog_followup(text, history) or (
        _is_catalog_price_followup(text)
        and _has_recent_catalog_product_context(history)
    )
    contextual_ready_stock_followup = _is_contextual_pronta_entrega_followup(text, history)
    ipad_model_alternative_price_followup = _is_ipad_model_alternative_price_followup(
        text,
        history,
    )
    installment_budget_request = _is_installment_budget_options_request(text, history)
    generic_phone_budget_request = bool(
        re.search(r"\bcelulares?\b", normalized)
        and _extract_budget_limit(text) is not None
    )
    if not normalized or (
        not _has_product_reference(normalized)
        and not generic_phone_budget_request
        and not installment_budget_request
        and not accessory_request
        and not bare_model_request
        and not catalog_followup
        and not contextual_ready_stock_followup
    ):
        return False
    if (
        _is_available_list_request(text) or _is_sealed_catalog_list_request(text)
    ) and not installment_budget_request:
        return False
    if (
        allow_buyer_interest_with_image
        and _is_catalog_buyer_interest(buyer_interest_text)
        and _has_product_reference(normalized)
    ):
        return True
    explicit_catalog_price_request = _is_explicit_catalog_price_request(text)
    explicit_availability_question = bool(
        _has_product_reference(normalized)
        and re.search(r"\b(?:qual|quais)\s+(?:esta|estao)\s+disponivel\b", normalized)
    )
    has_specialized_detail = any(
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
            "nota fiscal",
            "quantas vezes",
            "em quantas",
        )
    )
    if (
        (
            has_specialized_detail
            and not explicit_availability_question
            and not installment_budget_request
        )
        or ("entrega" in normalized and not _has_pronta_entrega_reference(text))
    ):
        return False
    if "pagamento" in normalized and not explicit_catalog_price_request:
        return False
    if ipad_model_alternative_price_followup:
        return True
    if installment_budget_request:
        return True
    if catalog_followup or contextual_ready_stock_followup:
        return True
    if accessory_request:
        return True
    if _is_cheapest_catalog_request(normalized):
        return True
    availability_phrases = (
        "tem",
        "teria",
        "disponivel",
        "disponibilidade",
        "em estoque",
        "estoque",
        "a venda",
        "vende",
        "possui",
        "quanto custa",
        "quanto esta",
        "quanto fica",
        "qual o preco",
        "valor",
        "valores",
        "preco",
        "precos",
        "cotar",
        "cotacao",
        "novo",
        "nova",
        "novos",
        "novas",
        "lacrado",
        "lacrados",
        "encomenda",
        "semi",
        "seminovo",
        "seminovos",
        "usado",
        "usados",
    )
    if any(re.search(rf"\b{re.escape(phrase)}\b", normalized) for phrase in availability_phrases):
        return True
    if re.match(r"^(?:iphones?|ipads?|mac(?:books?)?|airpods?|apple\s+watch)\b", normalized):
        return True
    has_purchase_intent = any(
        phrase in normalized
        for phrase in (
            "gostaria de ver",
            "gostaria de saber mais",
            "gostaria de informacoes sobre",
            "queria ver",
            "quero ver",
            "quero comprar",
            "possibilidade de comprar",
            "procuro",
            "procurando",
            "preciso",
            "necessito",
        )
    )
    has_purchase_interest = bool(
        re.search(
            r"\b(?:(?:me\s+)?interessei\s+(?:no|na|em)|"
            r"tenho\s+(?:o\s+)?interesse\s+(?:no|na|em))\b",
            normalized,
        )
        and not re.search(
            r"\bnao\s+(?:me\s+interessei|tenho\s+(?:o\s+)?interesse)\b",
            normalized,
        )
        or _is_catalog_buyer_interest(normalized)
    )
    has_catalog_model_correction = bool(
        re.search(r"\b(?:na\s+verdade\s+)?(?:eu\s+)?queria\s+(?:o|a|um|uma)\b", normalized)
        and _has_recent_catalog_product_context(history)
    )
    has_broad_filter = _extract_budget_limit(text) is not None or any(
        marker in normalized
        for marker in ("faixa de", "orcamento", "em torno de", "cerca de", "por volta de")
    )
    return bool(
        (
            has_purchase_intent
            or has_purchase_interest
            or has_catalog_model_correction
            or has_broad_filter
        )
        and (
            _catalog_families(normalized)
            or _has_product_reference(normalized)
            or generic_phone_budget_request
        )
    )


def _is_affirmative_first_iphone_statement(text: str) -> bool:
    normalized = _normalize(text)
    return bool(
        re.search(r"\bprimeir[oa]\s+iphones?\b", normalized)
        and not re.search(
            r"\b(?:nao|nunca)\s+(?:e\s+|foi\s+|sera\s+)?(?:o\s+)?"
            r"(?:meu|minha)\s+primeir[oa]\s+iphones?\b",
            normalized,
        )
    )


def _is_unresolved_iphone_model_selection(text: str) -> bool:
    normalized = _normalize(text)
    has_model_selection_prompt = bool(
        re.search(r"\bmodelo\s+em\s+mente\b", normalized)
        and re.search(r"\b(?:compr\w*|lacrad\w*)\b", normalized)
    )
    first_iphone_purchase = bool(
        _is_affirmative_first_iphone_statement(normalized)
        and re.search(
            r"\b(?:pretend\w*|planej\w*|vou|quero)\b.{0,30}\bcompr\w*\b",
            normalized,
        )
    )
    return bool(
        "iphone" in _catalog_families(normalized)
        and not _requested_iphone_model_keys(normalized)
        and _extract_budget_limit(text) is None
        and (has_model_selection_prompt or first_iphone_purchase)
    )


def _is_accessory_catalog_request(text: str) -> bool:
    normalized = _normalize(text)
    if not _is_accessory_catalog_query(normalized):
        return False
    if any(
        marker in normalized
        for marker in (
            "nao funciona",
            "quebrad",
            "defeit",
            "problema",
            "consert",
            "assistencia",
            "acompanha",
            "vem com",
            "vem na caixa",
            "inclus",
        )
    ):
        return False
    return normalized in {"fonte", "fontes", "carregador", "carregadores"} or bool(
        re.search(
            r"\b(?:tem|vende\w*|possui|disponivel|estoque|a venda|valor|valores|preco|precos|custa|original|tipo|usb|20w)\b",
            normalized,
        )
    )


def _is_case_accessory_request(text: str) -> bool:
    normalized = _normalize(text)
    if not re.search(
        r"\b(?:capinha|capa|case|pelicula|protetor(?:\s+de\s+camera)?)\b",
        normalized,
    ):
        return False
    if normalized.strip(" ?!.,") in {"capinha", "capa", "case", "pelicula", "protetor de camera"}:
        return True
    return bool(
        re.search(
            r"\b(?:quanto|valor|preco|custa|a partir|tem|vende|disponivel|consegue|conseguem)\b",
            normalized,
        )
    )


def _is_accessory_inclusion_question(text: str) -> bool:
    normalized = _normalize(text)
    if not re.search(r"\b(?:fontes?|carregador(?:es)?|cabo|acessorio|acessorios)\b", normalized):
        return False
    asks_if_phone_is_sold_alone = bool(
        re.search(
            r"\bvem\s+(?:somente|so)\s+(?:(?:o|a)\s+)?(?:celular|aparelho|iphone)\b",
            normalized,
        )
    )
    asks_what_is_included = bool(
        re.search(
            r"(?:\bvem\s+(?:com|junto|inclus\w*|na\s+caixa)\b|\bacompanha\w*\b|\binclui\w*\b)",
            normalized,
        )
    )
    return asks_if_phone_is_sold_alone or asks_what_is_included


def _requested_capacity_keys(text: str) -> tuple[str, ...]:
    normalized = _normalize(text)
    keys: list[str] = []
    for match in re.finditer(r"\b(\d+(?:[.,]\d+)?)\s*(gb|tb|g|t)\b", normalized):
        number = match.group(1).replace(",", ".")
        unit = "tb" if match.group(2) in {"tb", "t"} else "gb"
        # Apple Watch catalog rows use 4G for cellular connectivity. It is
        # not a customer request for 4 GB of storage.
        if (
            unit == "gb"
            and match.group(2) == "g"
            and number == "4"
            and re.search(r"\bapple\s+watch\b", normalized)
        ):
            continue
        # In Portuguese, "tb" commonly means "também". When it follows
        # "iPhone" directly, a current model such as "iPhone 17 tb?" must
        # not be interpreted as a 17 TB capacity filter.
        if unit == "tb" and re.search(r"\biphones?\s*$", normalized[: match.start()]):
            continue
        if number.endswith(".0"):
            number = number[:-2]
        # Accept the common customer shorthand for the iPhone 512 GB option.
        if unit == "gb" and number == "500":
            number = "512"
        key = f"{number}{unit}"
        if key not in keys:
            keys.append(key)
    for match in re.finditer(r"\b(1024|512|256|128|64|32)\b", normalized):
        key = f"{match.group(1)}gb"
        if key not in keys:
            keys.append(key)
    return tuple(keys)


_RequestedModelKey = tuple[int | str, str]
_RequestedModelScope = tuple[str, tuple[str, ...], tuple[str, ...]]


def _requested_model_segments(text: str) -> dict[_RequestedModelKey, tuple[str, ...]]:
    """Split batched customer lines into the iPhone model each line refers to."""
    segments_by_model: dict[_RequestedModelKey, list[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        models = _requested_iphone_model_keys(line)
        if len(models) == 1:
            model_segments = ((models[0], line),)
        elif len(models) > 1:
            segments = _multi_model_installment_queries(line, None)
            model_segments = tuple(zip(models, segments)) if len(segments) == len(models) else ()
        else:
            model_segments = ()
        for model, segment in model_segments:
            segments_by_model.setdefault(model, []).append(segment)
    return {model: tuple(segments) for model, segments in segments_by_model.items()}


def _requested_model_attribute_scopes(
    model_segments: dict[_RequestedModelKey, tuple[str, ...]],
    items: list[Any],
) -> dict[_RequestedModelKey, tuple[_RequestedModelScope, ...]]:
    """Keep explicit capacities and colors attached to each model segment."""
    scopes: dict[_RequestedModelKey, list[_RequestedModelScope]] = {}
    for model, segments in model_segments.items():
        for segment in segments:
            capacities = _requested_capacity_keys(segment)
            colors = _requested_catalog_colors(segment, items)
            if capacities or colors:
                scopes.setdefault(model, []).append((segment, capacities, colors))
    return {model: tuple(requests) for model, requests in scopes.items()}


def _capacity_list_no_match_reply(text: str, *, sealed_only: bool = False) -> str | None:
    requested_capacity_keys = _requested_capacity_keys(text)
    if not requested_capacity_keys:
        return None

    normalized = _normalize(text)
    other_family_requested = bool(
        re.search(r"\b(?:ipads?|mac(?:books?)?|airpods?|apple\s+watch)\b", normalized)
    )
    is_iphone = bool(re.search(r"\biphones?\b", normalized))
    if is_iphone and not other_family_requested:
        subject = (
            "iPhones lacrados"
            if sealed_only or (_has_sealed_reference(normalized) and not _has_seminovo_reference(normalized))
            else "iPhones"
        )
    else:
        subject = "modelos novos/lacrados" if sealed_only else "modelos"

    capacity_labels = [
        re.sub(
            r"(\d+(?:[.,]\d+)?)(gb|tb)",
            lambda match: f"{match.group(1)} {match.group(2).upper()}",
            capacity,
            flags=re.IGNORECASE,
        )
        for capacity in requested_capacity_keys
    ]
    return (
        f"No momento, não encontrei {subject} com {' e '.join(capacity_labels)} "
        "disponíveis no catálogo. Posso verificar outras capacidades?"
    )


def _requested_capacity_key(text: str) -> str | None:
    keys = _requested_capacity_keys(text)
    return keys[0] if keys else None


def _trailing_model_capacity_scope(
    text: str,
    requested_models: tuple[tuple[int | str, str], ...],
) -> tuple[tuple[int | str, str], str] | None:
    """Scope a trailing capacity to the last model when a request lists alternatives."""
    if len(requested_models) <= 1:
        return None

    normalized = _normalize(text)
    capacity_match = re.search(
        r"(?P<capacity>\d+(?:[.,]\d+)?\s*(?:gb|tb|g|t))\s*$",
        normalized,
    )
    if capacity_match is None:
        return None
    capacities = _requested_capacity_keys(capacity_match.group("capacity"))
    if len(capacities) != 1:
        return None

    model_number, variant = requested_models[-1]
    model_label = " ".join(part for part in (str(model_number), variant) if part)
    before_capacity = normalized[: capacity_match.start()]
    if not re.search(
        rf"\b{re.escape(model_label)}\s*(?:(?:de|do|da|dos|das)\s*)?$",
        before_capacity,
    ):
        return None
    return requested_models[-1], capacities[0]


def _has_requested_catalog_color(text: str, items: list[Any]) -> bool:
    """Return whether the query names a color present in its candidates."""
    normalized = _normalize(text)
    if not normalized:
        return False

    for item in items:
        raw_colors = getattr(item, "color", None) or getattr(item, "colors", None)
        if isinstance(raw_colors, (list, tuple, set)):
            colors = raw_colors
        else:
            colors = re.split(r"\s*[|;/,]\s*", str(raw_colors or ""))
        for value in colors:
            color = _normalize(str(value or "")).strip()
            if color and re.search(rf"(?<!\w){re.escape(color)}(?!\w)", normalized):
                return True
    return False


def _catalog_color_map(items: list[Any]) -> dict[str, str]:
    colors: dict[str, str] = {}
    for item in items:
        raw_colors = getattr(item, "color", None) or getattr(item, "colors", None)
        if isinstance(raw_colors, (list, tuple, set)):
            values = raw_colors
        else:
            values = re.split(r"\s*[|;/,]\s*", str(raw_colors or ""))
        for value in values:
            color = str(value or "").strip()
            normalized_color = _normalize(color)
            if normalized_color:
                colors.setdefault(normalized_color, color)
    return colors


_COMMON_CATALOG_COLOR_NAMES = (
    "azul claro",
    "azul escuro",
    "azul intenso",
    "azul ultramarino",
    "roxo profundo",
    "preto espacial",
    "titânio natural",
    "titânio deserto",
    "titânio branco",
    "titânio preto",
    "meia noite",
    "ultramarino",
    "estelar",
    "roxo",
    "roxa",
    "azul",
    "preto",
    "preta",
    "branco",
    "branca",
    "verde",
    "rosa",
    "amarelo",
    "amarela",
    "vermelho",
    "vermelha",
    "laranja",
    "dourado",
    "dourada",
    "prata",
    "prateado",
    "prateada",
    "cinza",
    "grafite",
    "bege",
    "marrom",
    "purple",
    "blue",
    "black",
    "white",
    "green",
    "pink",
    "midnight",
    "starlight",
    "ultramarine",
)


def _requested_catalog_colors(text: str, items: list[Any]) -> tuple[str, ...]:
    """Find color names in a customer request, even when none are in stock."""
    normalized = re.sub(r"\s*[-‐‑–—]\s*", " ", _normalize(text))
    if not normalized:
        return ()

    known_colors = {
        *_COMMON_CATALOG_COLOR_NAMES,
        *_catalog_color_map(items).keys(),
    }
    matches: list[tuple[int, int, str]] = []
    for color in known_colors:
        normalized_color = re.sub(r"\s*[-‐‑–—]\s*", " ", _normalize(color)).strip()
        if not normalized_color:
            continue
        match = re.search(
            rf"(?<!\w){re.escape(normalized_color)}(?!\w)",
            normalized,
        )
        if match:
            matches.append((match.start(), match.end(), normalized_color))

    selected: list[tuple[int, int, str]] = []
    for candidate in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
        start, end, _color = candidate
        if any(start < selected_end and selected_start < end for selected_start, selected_end, _ in selected):
            continue
        selected.append(candidate)

    negated_color_spans: set[tuple[int, int]] = set()
    for negation in re.finditer(r"\b(?:nao|sem|exceto)\b", normalized):
        scope_start = negation.end()
        remainder = normalized[scope_start:]
        if negation.group() == "sem" and re.match(r"\s+duvida\b", remainder):
            continue
        if negation.group() == "nao" and re.match(r"\s+apenas\b", remainder):
            continue
        clause_break = re.search(
            r"[,;.!?]|\b(?:mas|porem|e\s+sim|so\s+que)\b",
            remainder,
        )
        scope_end = (
            scope_start + clause_break.start()
            if clause_break
            else len(normalized)
        )
        scoped_colors = [
            (start, end)
            for start, end, _color in selected
            if scope_start <= start < scope_end
        ]
        if negation.group() == "nao" and scoped_colors:
            first_color_start = min(start for start, _end in scoped_colors)
            prefix = normalized[scope_start:first_color_start].strip()
            preference_phrase = bool(
                re.search(
                    r"\b(?:quero|queria|gosto|gostaria|prefiro|prefira|aceito|"
                    r"aceitaria|desejo|escolho|escolheria)\b",
                    prefix,
                )
            )
            availability_phrase = bool(
                re.search(
                    r"\b(?:tem|teria|tenho|tinha|esta|estar|disponivel|existe|"
                    r"encontrei|aparece|vende|vendido)\b",
                    prefix,
                )
            )
            simple_prefix_words = {
                "o", "a", "cor", "na", "no", "de", "do", "da", "em", "um", "uma",
                "esse", "essa", "este", "e", "ser", "seria",
            }
            simple_prefix = all(word in simple_prefix_words for word in prefix.split())
            if availability_phrase or not (preference_phrase or simple_prefix):
                continue
        if negation.group() == "nao" and not scoped_colors and clause_break:
            previous_colors = [
                (start, end)
                for start, end, _color in selected
                if end <= negation.start()
            ]
            next_clause_start = scope_start + clause_break.end()
            has_following_color = any(
                start >= next_clause_start for start, end, _color in selected
            )
            if previous_colors and has_following_color:
                previous_color = max(previous_colors, key=lambda span: span[1])
                if re.fullmatch(
                    r"[\s,;.!?]*",
                    normalized[previous_color[1] : negation.start()],
                ):
                    negated_color_spans.add(previous_color)
        negated_color_spans.update(
            scoped_colors
        )

    return tuple(
        color
        for start, end, color in selected
        if (start, end) not in negated_color_spans
    )


def _catalog_color_correction_scope(text: str) -> str:
    normalized = _normalize(text)
    correction_markers = list(
        re.finditer(
            r"\b(?:na verdade|quer dizer|quis dizer|queria dizer|corrigindo|"
            r"prefiro|prefira|quero|queria|apenas|so)\b",
            normalized,
        )
    )
    return normalized[correction_markers[-1].start() :] if correction_markers else normalized


def _latest_catalog_color_request(
    current_text: str,
    customer_request_context: str,
    items: list[Any],
) -> tuple[str, ...]:
    for candidate in [current_text, *reversed(customer_request_context.splitlines())]:
        colors = _requested_catalog_colors(candidate, items)
        if colors:
            return _requested_catalog_colors(
                _catalog_color_correction_scope(candidate),
                items,
            ) or colors
    return ()


def _is_catalog_color_either_or_request(
    current_text: str,
    image_description: str | None,
    customer_request_context: str,
    items: list[Any],
) -> bool:
    candidates = [
        current_text,
        image_description or "",
        *reversed(customer_request_context.splitlines()),
    ]
    for candidate in candidates:
        colors = _requested_catalog_colors(candidate, items)
        if not colors:
            continue
        if len(colors) < 2:
            return False
        normalized = _normalize(candidate)
        color_spans = []
        for color in colors:
            match = re.search(rf"(?<!\w){re.escape(color)}(?!\w)", normalized)
            if match:
                color_spans.append((match.start(), match.end()))
        color_spans.sort()
        return any(
            re.search(r"\b(?:ou|or)\b", normalized[left_end:right_start])
            for (_left_start, left_end), (right_start, _right_end)
            in zip(color_spans, color_spans[1:])
        )
    return False


def _catalog_color_key(value: Any) -> str:
    normalized = _normalize(str(value or ""))
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    aliases = {
        "amarela": "amarelo",
        "branca": "branco",
        "dourada": "dourado",
        "prata": "prateado",
        "prateada": "prateado",
        "preta": "preto",
        "roxa": "roxo",
        "vermelha": "vermelho",
    }
    return " ".join(aliases.get(word, word) for word in normalized.split())


def _item_matches_catalog_colors(item: Any, requested_colors: tuple[str, ...]) -> bool:
    raw_colors = getattr(item, "color", None) or getattr(item, "colors", None)
    if isinstance(raw_colors, (list, tuple, set)):
        values = raw_colors
    else:
        values = re.split(r"\s*[|;/,]\s*", str(raw_colors or ""))
    item_colors = [_catalog_color_key(value) for value in values]
    return any(
        color and requested_color
        and re.search(rf"(?<!\w){re.escape(requested_color)}(?!\w)", color)
        for color in item_colors
        for requested_color in map(_catalog_color_key, requested_colors)
    )


def _strip_catalog_color_references(query: str, items: list[Any]) -> str:
    normalized_query = _normalize(query)
    for color in sorted(_catalog_color_map(items), key=len, reverse=True):
        normalized_query = re.sub(
            rf"(?<!\w){re.escape(color)}(?!\w)",
            " ",
            normalized_query,
        )
    return re.sub(r"\s+", " ", normalized_query).strip()


def _current_catalog_color(text: str, items: list[Any]) -> str | None:
    """Return one color explicitly chosen in the current photo request."""
    normalized = _normalize(text)
    if not normalized:
        return None

    colors: set[str] = set()
    for item in items:
        raw_colors = getattr(item, "color", None) or getattr(item, "colors", None)
        if isinstance(raw_colors, (list, tuple, set)):
            values = raw_colors
        else:
            values = re.split(r"\s*[|;/,]\s*", str(raw_colors or ""))
        for value in values:
            color = _normalize(str(value or "")).strip()
            if color and re.search(rf"(?<!\w){re.escape(color)}(?!\w)", normalized):
                colors.add(color)
    return next(iter(colors)) if len(colors) == 1 else None


def _capacity_key(value: Any) -> str | None:
    normalized = _normalize(str(value or ""))
    match = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(gb|tb|g)\b", normalized)
    if not match:
        return None
    number = match.group(1).replace(",", ".")
    unit = "tb" if match.group(2) == "tb" else "gb"
    if number.endswith(".0"):
        number = number[:-2]
    if unit == "gb" and number == "500":
        number = "512"
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
        "de onde e a loja",
        "de onde fica a loja",
        "posso ir na loja",
        "posso visitar a loja",
    )
    return any(phrase in normalized for phrase in phrases) or bool(
        re.search(
            r"\b(?:de\s+onde\s+(?:e|fica)|a?onde\s+fica\s+(?:localizad\w*\s+)?(?:a\s+)?loja)\b",
            normalized,
        )
    )


def _has_catalog_condition_signal(text: str) -> bool:
    normalized = _normalize(text)
    return bool(
        re.search(
            r"\b(?:bateria|tela|display|vidro|pecas?|face\s+id|c[aâ]meras?|"
            r"alto\s+falante|microfone|carregamento|botoes?|bloqueio|icloud|"
            r"restric\w*|manuten\w*|trocad\w*|substituid\w*)\b",
            normalized,
        )
    )


def _is_delivery_or_pickup_request(
    text: str,
    history: list[dict[str, str]] | None = None,
) -> bool:
    normalized = _normalize(text)
    if not normalized or _has_sealed_reference(normalized):
        return False
    if _has_pronta_entrega_reference(text) and (
        _is_available_list_request(text)
        or _is_product_availability_request(text)
        or _is_contextual_pronta_entrega_followup(text, history)
    ):
        return False
    has_delivery = bool(re.search(r"\b(?:entrega|entregam|entregas)\b", normalized))
    has_pickup = bool(
        re.search(r"\bretirad\w*\b", normalized)
        or "retirar na loja" in normalized
        or "buscar na loja" in normalized
        or "pegar na loja" in normalized
    )
    return has_delivery or has_pickup


def _is_pickup_schedule_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or _has_sealed_reference(normalized):
        return False
    has_pickup = bool(
        re.search(r"\b(?:retirad\w*|retir\w*|busc\w*|peg\w*)\b", normalized)
    )
    return (
        has_pickup
        and _has_visit_date_reference(text)
        and _has_visit_time_reference(text)
    )


def _is_delivery_fee_request(
    text: str,
    history: list[dict[str, str]] | None = None,
) -> bool:
    normalized = _normalize(text)
    if not normalized or not re.search(r"\b(?:taxas?|tarifas?|fretes?)\b", normalized):
        return False
    if re.search(
        r"\b(?:taxas?\s+de\s+entrega|taxas?\s+do\s+frete|fretes?|motoboy|sedex|entrega)\b",
        normalized,
    ):
        return True
    has_address_marker = bool(
        re.search(
            r"\b(?:rua|avenida|av\.?|alameda|travessa|praca|bairro|endereco|cep)\b",
            normalized,
        )
    )
    has_address_number = bool(re.search(r"\b\d{1,5}\b", normalized))
    return has_address_marker and has_address_number and _has_delivery_context(history)


def _has_delivery_context(history: list[dict[str, str]] | None) -> bool:
    delivery_markers = (
        "enviamos para curitiba",
        "por motoboy",
        "por sedex",
        "pagamento deve ser antecipado antes do despacho",
    )
    return any(
        entry.get("role") == "assistant"
        and any(marker in _normalize(entry.get("content", "")) for marker in delivery_markers)
        for entry in (history or [])
    )


def _is_delivery_followup_request(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Keep a short shipping confirmation in the delivery flow after the FAQ answer."""
    normalized = _normalize(text)
    if not normalized or not _has_delivery_context(history):
        return False
    if _is_available_list_request(text):
        return False
    if re.search(
        r"\b(?:foto|fotos|imagem|imagens|valor|preco|precos|link|dados|"
        r"informacao|informacoes|detalhes)\b",
        normalized,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:conseguem?|podem?|poderiam?)\s+(?:me\s+)?(?:mandar|enviar)\b",
            normalized,
        )
    )


def _is_delivery_payment_question(
    text: str,
) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    has_delivery_context = bool(
        re.search(r"\b(?:entreg\w*|motoboy|sedex|frete|envio|envi\w*)\b", normalized)
    )
    has_payment_context = bool(
        re.search(
            r"\b(?:pagamento|pagar|pago|parcelad\w*|parcelamento|pix|dinheiro|"
            r"debito|credito|cartao)\b",
            normalized,
        )
    )
    return has_delivery_context and has_payment_context


def _is_delivery_quote_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    normalized = _normalize(text)
    if not normalized or not history:
        return False

    latest_assistant_reply = next(
        (
            _normalize(entry.get("content", ""))
            for entry in reversed(history)
            if entry.get("role") == "assistant" and entry.get("content")
        ),
        "",
    )
    if not (
        "qual e seu bairro ou cep" in latest_assistant_reply
        and "taxa" in latest_assistant_reply
        and "prazo" in latest_assistant_reply
        and "pagamento" in latest_assistant_reply
        and re.search(r"\b(?:entrega|motoboy|sedex)\b", latest_assistant_reply)
    ):
        return False

    has_postal_code = bool(re.search(r"\b\d{5}-?\d{3}\b", text))
    has_location_statement = bool(
        re.search(
            r"\b(?:(?:moro|resido)\s+(?:em|no|na)|sou\s+de|"
            r"meu\s+(?:bairro|endereco)\s+e)\s+[a-z0-9]+|"
            r"\bbairro\s+(?!(?:qual|e|pode|ser\w*|voce|fica|melhor|atende|"
            r"de|do|dos|da|das|em|no|na|para|pra)\b)[a-z0-9]+|"
            r"\bfica\s+(?:em|no|na)\s+[a-z0-9]+",
            normalized,
        )
    )
    if "?" in text and re.search(r"\bfica\s+(?:em|no|na)\b", normalized):
        has_location_statement = False
    has_street_address = bool(
        re.search(
            r"\b(?:rua|avenida|av|alameda|travessa|praca|endereco)\b"
            r".{0,60}\b\d{1,5}\b",
            normalized,
        )
    )
    if has_postal_code or has_location_statement or has_street_address:
        return True

    candidate = re.sub(
        r"^(?:(?:sim|ok|certo|beleza|obrigad\w*|ola|oi|bom dia|boa tarde|boa noite)"
        r"\b[\s,]*)+",
        "",
        normalized,
    ).strip()
    candidate = re.sub(r"^[,;:\s]+|[,;:!?]+\s*$", "", candidate).strip()
    if not candidate:
        return False
    words = re.findall(r"[a-z0-9]+", candidate)
    if not words or len(words) > 6:
        return False
    unrelated_markers = (
        r"\b(?:sim|nao|ok|certo|beleza|obrigad\w*|ola|oi|pode|ser|tudo|bem|claro|"
        r"perfeito|combinado|confirmo|iphone|ipad|airpods|watch|samsung|motorola|xiaomi|redmi|"
        r"modelo|preco|quanto|qual|como|"
        r"pagamento|pagar|pago|pix|dinheiro|debito|credito|cartao|parcela\w*|vezes|reais?|"
        r"voces|entrega|entreg\w*|motoboy|sedex|frete|taxa|prazo|fica|em|no|na|de|para|pra|"
        r"hoje|amanha|visita|marcar|agendar|remarcar|vou|quero|gostaria|"
        r"consigo|posso|tempo|ir|loja|atendente)\b"
    )
    if re.search(unrelated_markers, candidate):
        return False

    location_only = bool(re.fullmatch(r"[a-z]+(?:[ ,]+[a-z]+){0,3}", candidate))
    if location_only and re.search(r"\b(?:curitiba|pinhais|colombo|araucaria)\b", candidate):
        return True
    if "?" in text or re.fullmatch(r"\d+", normalized):
        return False
    return location_only and len(words) <= 4


def _is_explicit_human_request(text: str) -> bool:
    normalized = _normalize(text)
    return bool(
        re.search(r"\b(?:atendente|humano|pessoa|reclamacao)\b", normalized)
        or "quero falar com" in normalized
        or "falar com um atendente" in normalized
    )


def _delivery_or_pickup_reply(
    faq: FAQStore,
    text: str,
    *,
    force_delivery: bool = False,
) -> str:
    normalized = _normalize(text)
    replies: list[str] = []
    if force_delivery or re.search(r"\b(?:entrega|entregam|entregas)\b", normalized):
        replies.append(
            faq.get("entrega")
            or "Enviamos para Curitiba e região por motoboy. Para fora de Curitiba, enviamos por Sedex."
        )
    if re.search(r"\bretirad\w*\b", normalized) or any(
        phrase in normalized for phrase in ("buscar na loja", "pegar na loja")
    ):
        replies.append(
            faq.get("retirada")
            or "Fazemos retirada na loja com horário marcado. O pagamento é feito na hora da retirada."
        )
    return "\n\n".join(reply for reply in replies if reply)


def _is_visit_unavailability_message(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    cannot_visit = bool(
        re.search(
            r"\bnao\s+(?:consigo|posso)\s+(?:ir|vir|visitar|passar|comparecer)\b"
            r".{0,45}\b(?:loja|estabelecimento)\b"
            r"|\bnao\s+da\s+tempo\b"
            r"|\bsem\s+tempo\s+(?:de|para)\s+(?:ir|vir|visitar|passar|comparecer)\b",
            normalized,
        )
    )
    if not cannot_visit:
        return False
    has_positive_visit_plan = bool(
        re.search(
            r"(?<!nao\s)\b(?:mas\s+)?(?:posso|consigo|vou|gostaria\s+de)\s+"
            r"(?:ir|vir|visitar|passar|comparecer|marcar|agendar)\b"
            r"|\b(?:vou|quero|pretendo)\s+deixar\s+(?:para|pra)\s+"
            r"(?:ir|vir|visitar|passar|comparecer)\b",
            normalized,
        )
    )
    return not has_positive_visit_plan


def _delivery_payment_reply(
    faq: FAQStore,
    *,
    acknowledge_cannot_visit: bool = False,
) -> str:
    delivery = faq.get("entrega") or (
        "Enviamos para Curitiba e região por motoboy. Para fora de Curitiba, enviamos por Sedex."
    )
    delivery = re.sub(
        r"\s*O pagamento deve ser antecipado antes do despacho\.?",
        "",
        delivery,
        flags=re.IGNORECASE,
    )
    delivery = re.sub(
        r"\s*Taxa e prazo devem ser cotados com um atendente\.?",
        "",
        delivery,
        flags=re.IGNORECASE,
    ).strip()
    payment = faq.get("pagamento") or PAYMENT_METHODS_REPLY
    acknowledgement = (
        "Entendi que você não consegue vir até a loja. " if acknowledge_cannot_visit else ""
    )
    return (
        f"{acknowledgement}{delivery} "
        "Para envio, o pagamento deve ser antecipado antes do despacho; não é possível pagar na entrega. "
        f"{payment} Não aceitamos pagamento por cartão de crédito online ou link. "
        "Um atendente precisa confirmar a taxa e o prazo pelo endereço e orientar "
        "como concluir o pagamento antes do envio. Qual é seu bairro ou CEP?"
    )


def _extract_time_reference(text: str) -> tuple[int, str] | None:
    normalized = _normalize(text)
    patterns = (
        r"\b(?P<hour>[01]?\d|2[0-3])\s*:\s*(?P<minute>[0-5]\d)\b",
        r"\b(?P<hour>[01]?\d|2[0-3])\s*h\s*(?P<minute>[0-5]\d)?\b",
        r"\b(?P<hour>[01]?\d|2[0-3])\s+horas?\b",
        r"\bas\s*(?P<hour>[01]?\d|2[0-3])\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        hour = int(match.group("hour"))
        minute = int(match.groupdict().get("minute") or 0)
        return hour * 60 + minute, f"{hour:02d}:{minute:02d}"
    return None


def _pickup_schedule_reply(faq: FAQStore, text: str) -> str:
    normalized = _normalize(text)
    day_names = {
        "segunda": ("segunda-feira", "Na"),
        "terca": ("terça-feira", "Na"),
        "quarta": ("quarta-feira", "Na"),
        "quinta": ("quinta-feira", "Na"),
        "sexta": ("sexta-feira", "Na"),
        "sabado": ("sábado", "No"),
        "domingo": ("domingo", "No"),
    }
    day_match = re.search(
        r"\b(segunda(?:-feira)?|terca(?:-feira)?|quarta(?:-feira)?|"
        r"quinta(?:-feira)?|sexta(?:-feira)?|sabado|domingo)\b",
        normalized,
    )
    day_key = day_match.group(1).split("-")[0] if day_match else None
    day_label, day_preposition = day_names.get(day_key, ("dia informado", "No"))
    pickup = "Fazemos retirada na loja com horário marcado."
    hours = faq.get("hours") or (
        "Atendemos de segunda a sexta, das 09:00 às 18:00. "
        "Aos sábados, domingos e feriados, a loja fica fechada."
    )

    if day_key in {"sabado", "domingo"}:
        detail = f"{day_preposition} {day_label}, a loja fica fechada para atendimento e retirada."
    else:
        time_reference = _extract_time_reference(text)
        if time_reference is None:
            detail = (
                "A retirada precisa ocorrer dentro do expediente e ter o horário confirmado "
                "por um atendente."
            )
        else:
            minutes, time_label = time_reference
            after_time = bool(re.search(r"\b(?:depois|apos)\b.{0,16}\b\d", normalized))
            if minutes < 9 * 60:
                detail = (
                    f"{day_preposition} {day_label}, {time_label} é antes da abertura às 09:00; "
                    "um atendente pode confirmar outro horário."
                )
            elif minutes >= 18 * 60:
                detail = (
                    f"{day_preposition} {day_label}, o expediente termina às 18:00, então "
                    f"{time_label} fica no limite ou após o fechamento e precisa ser confirmado "
                    "por um atendente."
                )
            elif after_time:
                detail = (
                    f"{day_preposition} {day_label}, depois das {time_label} a retirada só pode "
                    "ser solicitada até o fechamento, às 18:00, e depende da confirmação de "
                    "um atendente."
                )
            else:
                detail = (
                    f"{day_preposition} {day_label}, às {time_label} fica dentro do expediente, "
                    "mas a retirada precisa de confirmação de um atendente."
                )

    return f"{pickup} {hours} {detail}"


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
    normalized = re.sub(r"\bhj\b", "hoje", _normalize(text))
    has_status_marker = bool(
        re.search(r"\b(?:abert\w*|fechad\w*|atend\w*)\b", normalized)
        or re.search(
            r"\b(?:voce|voces|vcs|a loja|loja)\s+"
            r"(?:(?:esta|estao)\s+)?(?:trabalh|funcion|atend)\w*\b",
            normalized,
        )
        or re.search(
            r"\b(?:trabalha|trabalham|funciona|funcionam|atende|atendem)\s+hoje\b",
            normalized,
        )
    ) or any(
        marker in normalized
        for marker in (
            "funcionamento",
            "horario",
            "horas",
            "abre",
            "fecha",
        )
    )
    if not has_status_marker:
        return False
    if "hoje" in normalized:
        return True
    # In WhatsApp, "a loja está aberta?" commonly omits "hoje" but still
    # asks for the current-day status. Keep hour questions on the FAQ path.
    if _is_store_hours_request(text):
        return False
    implicit_open_status = re.fullmatch(
        r"(?:(?:voces|vcs)\s+)?(?:estao|estamos)\s+abert\w*",
        normalized.strip(" ?!.,"),
    )
    return bool(
        (
            "loja" in normalized
            and re.search(r"\b(?:abert\w*|fechad\w*)\b", normalized)
        )
        or implicit_open_status
    )


def _is_store_day_hours_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    has_day = bool(
        re.search(
            r"\b(?:segunda(?:-feira)?|terca(?:-feira)?|quarta(?:-feira)?|"
            r"quinta(?:-feira)?|sexta(?:-feira)?|sabado|domingo)\b",
            normalized,
        )
    )
    has_hours_marker = bool(
        re.search(r"\b(?:abr\w*|fechad\w*|atend\w*|funcion\w*|trabalh\w*)\b", normalized)
    ) or any(marker in normalized for marker in ("horario", "horas"))
    return has_day and has_hours_marker


def _is_store_hours_request(text: str) -> bool:
    normalized = _normalize(text)
    normalized = re.sub(r"\bq\b", "que", normalized)
    normalized = re.sub(r"\bhrs?\b", "horas", normalized)
    if not normalized or "hoje" in normalized:
        return False
    if _is_store_day_hours_request(text):
        return True
    phrases = (
        "ate que horas",
        "ate que horario",
        "qual o horario",
        "qual horario",
        "horario de atendimento",
        "horario de funcionamento",
        "que horas voces atendem",
        "que horario voces atendem",
        "quando voces atendem",
    )
    return any(phrase in normalized for phrase in phrases) or (
        "horario" in normalized and "atendimento" in normalized
    )


def _is_visit_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or _is_visit_unavailability_message(text):
        return False
    phrases = (
        "marcar um horario",
        "marcar horario",
        "marcar uma visita",
        "marcar visita",
        "agendar",
        "agendamento",
        "agenda pra mim",
        "agenda para mim",
        "marque pra mim",
        "marque para mim",
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
        re.search(
            r"\b(?:posso|consigo|da para|da pra|gostaria de)\s+"
            r"(?:ir|visitar|comparecer)\b",
            normalized,
        )
        or re.search(
            r"\b(?:posso|consigo|da para|da pra|gostaria de)\s+passar\s+"
            r"(?:(?:na|pela)\s+loja(?:\s+fisica)?|ai|aqui|amanha|hoje)\b",
            normalized,
        )
        or re.search(
            r"\b(?:posso|consigo)\s+(?:te\s+)?entreg\w*\b.{0,60}\bhoje\b",
            normalized,
        )
        or (
            re.search(r"\bmarcar\b", normalized)
            and (
                _has_visit_date_reference(text)
                or _has_visit_time_reference(text)
            )
        )
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
    return (
        any(marker in normalized for marker in date_markers)
        or bool(re.search(r"\bhj\b", normalized))
        or bool(re.search(r"\bdia\s+\d{1,2}\b", normalized))
        or bool(re.search(r"\b(?:0?[1-9]|[12]\d|3[01])/(?:0?[1-9]|1[0-2])(?:/\d{4})?\b", normalized))
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
        or re.search(r"\b(?:[01]?\d|2[0-3])\s*(?::\s*[0-5]\d|h(?:\s*[0-5]\d)?|horas)\b", normalized)
    )


def _visit_time_for_request(text: str) -> str | None:
    if not _has_visit_time_reference(text):
        return None
    normalized = _normalize(text)
    patterns = (
        r"\b(?P<hour>[01]?\d|2[0-3])\s*:\s*(?P<minute>[0-5]\d)\b",
        r"\b(?P<hour>[01]?\d|2[0-3])\s*h\s*(?P<minute>[0-5]\d)?\b",
        r"\b(?P<hour>[01]?\d|2[0-3])\s*horas?\b",
        r"\bas\s*(?P<hour>[01]?\d|2[0-3])\b",
    )
    for pattern in patterns:
        matches = list(re.finditer(pattern, normalized))
        if matches:
            match = matches[-1]
            hour = int(match.group("hour"))
            minute = match.groupdict().get("minute") or "00"
            return f"{hour:02d}:{minute}"
    return None


def _has_appointment_prompt(history: list[dict[str, str]] | None) -> bool:
    return any(
        entry.get("role") == "assistant"
        and any(
            marker in _normalize(entry.get("content", ""))
            for marker in ("qual dia", "que dia", "qual horario", "que horario")
        )
        for entry in (history or [])
    )


def _has_today_visit_offer(history: list[dict[str, str]] | None) -> bool:
    return any(
        entry.get("role") == "assistant"
        and "visita para hoje" in _normalize(entry.get("content", ""))
        for entry in (history or [])
    )


def _latest_appointment_date_offer(
    history: list[dict[str, str]] | None,
) -> str | None:
    last_assistant = next(
        (
            entry
            for entry in reversed(history or [])
            if entry.get("role") == "assistant"
        ),
        None,
    )
    if last_assistant is None:
        return None

    content = last_assistant.get("content", "")
    offer = re.search(
        r"\b(?:marcar|solicitar)\s+(?:sua\s+|uma\s+)?visita\s+para\s+([^,\r\n.!?]+)",
        content,
        re.IGNORECASE,
    )
    if (
        not _has_appointment_prompt([last_assistant])
        or offer is None
        or not _has_visit_date_reference(offer.group(1))
    ):
        return None
    return offer.group(1)


def _has_unanswered_reservation_request(history: list[dict[str, str]] | None) -> bool:
    entries = history or []
    last_user_index = next(
        (index for index in range(len(entries) - 1, -1, -1) if entries[index].get("role") == "user"),
        None,
    )
    if last_user_index is None or not _is_reservation_request(entries[last_user_index].get("content", "")):
        return False

    last_assistant = next(
        (
            entry
            for entry in reversed(entries[last_user_index + 1 :])
            if entry.get("role") == "assistant"
        ),
        None,
    )
    if last_assistant is None:
        return False

    answer = _normalize(last_assistant.get("content", ""))
    if "nao trabalhamos com reserva" in answer:
        return False
    return _has_appointment_prompt([last_assistant]) or "visita para hoje" in answer


def _is_appointment_followup(text: str, history: list[dict[str, str]] | None) -> bool:
    """Use appointment history only when the current message looks like a reply to it."""
    if not _has_appointment_prompt(history):
        return False

    normalized = _normalize(text)
    if not normalized or _is_visit_unavailability_message(text):
        return False

    # A new request must win over an old appointment prompt.
    if (
        _is_current_date_request(text)
        or _is_today_store_status_request(text)
        or _is_available_list_request(text)
        or _is_sealed_catalog_list_request(text)
        or _is_product_availability_request(text)
        or _is_store_hours_request(text)
        or _is_physical_store_request(text)
    ):
        return False

    if _has_visit_date_reference(text) or _has_visit_time_reference(text):
        return True

    short_reply = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    short_reply = re.sub(r"\s+", " ", short_reply).strip()
    is_now_availability_reply = bool(
        re.fullmatch(
            r"(?:estou|estamos) dispon(?:ivel|iveis) (?:agora|agr)",
            short_reply,
        )
    )
    return is_now_availability_reply or short_reply in {
        "sim",
        "pode",
        "pode ser",
        "ok",
        "okay",
        "beleza",
        "esse horario",
        "esse horario serve",
        "esse horario esta bom",
        "agora tem como",
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
        "pode ser",
        "claro",
        "ok",
        "okay",
        "beleza",
        "blz",
        "blz pode ser",
        "beleza pode ser",
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
    latest_assistant_reply = next(
        (
            entry.get("content", "")
            for entry in reversed(history or [])
            if entry.get("role") == "assistant" and entry.get("content", "").strip()
        ),
        "",
    )
    normalized_latest_reply = _normalize(latest_assistant_reply)
    return any(marker in normalized_latest_reply for marker in offer_markers)


def _appointment_context(text: str, history: list[dict[str, str]] | None) -> str:
    previous_user_text = [
        entry.get("content", "").strip()
        for entry in (history or [])
        if entry.get("role") == "user" and entry.get("content", "").strip()
        and not _is_today_store_status_request(entry.get("content", ""))
    ]
    context_parts = [*previous_user_text[-4:]]
    latest_offer = _latest_appointment_date_offer(history)
    if latest_offer:
        context_parts.insert(0, latest_offer)
    return "\n".join([*context_parts, text.strip()]).strip()


def _store_address(faq: FAQStore) -> str:
    return faq.get("address") or (
        "Avenida Nossa Senhora da Luz, 1341 - Jardim Social, Curitiba - PR, 82520-060"
    )


def _store_hours(faq: FAQStore) -> str:
    return faq.get("hours") or faq.get("horario") or (
        "Atendemos de segunda a sexta, das 09:00 às 18:00. "
        "Aos sábados, domingos e feriados, a loja fica fechada."
    )


def _store_hours_reply(faq: FAQStore) -> str:
    hours = _store_hours(faq)
    if "marcad" in _normalize(hours):
        return hours
    return f"{hours} O atendimento \u00e9 feito com hor\u00e1rio marcado."


def _today_store_reply(faq: FAQStore, *, include_physical_store: bool = True) -> str:
    current = _store_now()
    if _is_business_weekday(current) and current.hour >= 18:
        return _tomorrow_store_reply(faq, include_physical_store=include_physical_store)
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


def _tomorrow_store_reply(faq: FAQStore, *, include_physical_store: bool = True) -> str:
    current = _store_now()
    tomorrow = current + timedelta(days=1)
    tomorrow_label = _today_label(tomorrow)
    address = _store_address(faq)
    intro = "Sim, temos loja física. " if include_physical_store else ""
    if _is_business_weekday(tomorrow):
        return (
            f"{intro}Perfeito 😊 Podemos marcar sua visita para amanhã, {tomorrow_label}, "
            "das 09:00 às 18:00, com horário marcado. "
            f"Endereço: {address}. Qual horário fica melhor para você?"
        )
    return (
        f"{intro}Amanhã será {tomorrow_label} e a loja estará fechada. {_store_hours(faq)} "
        f"Endereço: {address}. O atendimento é feito com horário marcado. "
        "Podemos marcar uma visita em um dia de atendimento. Qual dia e horário ficam "
        "melhores para você?"
    )


def _visit_date_for_request(
    text: str,
    now: datetime | None = None,
) -> datetime | None:
    normalized = _normalize(text)
    current = now or _store_now()
    if re.search(r"\b(?:hoje|hj)\b", normalized):
        return current
    if "amanha" in normalized:
        return current + timedelta(days=1)

    numeric_date = re.search(
        r"\b(?P<day>0?[1-9]|[12]\d|3[01])/(?P<month>0?[1-9]|1[0-2])"
        r"(?:/(?P<year>\d{4}))?\b",
        normalized,
    )
    if numeric_date is not None:
        day = int(numeric_date.group("day"))
        month = int(numeric_date.group("month"))
        explicit_year = numeric_date.group("year")
        years = [int(explicit_year)] if explicit_year else range(current.year, current.year + 5)
        for year in years:
            try:
                visit_day = current.replace(
                    year=year,
                    month=month,
                    day=day,
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
            except ValueError:
                continue
            if explicit_year or visit_day.date() >= current.date():
                return visit_day
        return None

    match = re.search(
        r"\b(segunda(?:[- ]feira)?|terca(?:[- ]feira)?|quarta(?:[- ]feira)?|"
        r"quinta(?:[- ]feira)?|sexta(?:[- ]feira)?|sabado|domingo)\b",
        normalized,
    )
    if not match:
        return None

    weekday = match.group(1).split("-")[0].split(" ")[0]
    weekday_numbers = {
        "segunda": 0,
        "terca": 1,
        "quarta": 2,
        "quinta": 3,
        "sexta": 4,
        "sabado": 5,
        "domingo": 6,
    }
    if "proxima semana" in normalized or "semana que vem" in normalized:
        days_ahead = 7 - current.weekday() + weekday_numbers[weekday]
    else:
        days_ahead = (weekday_numbers[weekday] - current.weekday()) % 7
    return current + timedelta(days=days_ahead)


def _visit_day_reply(visit_day: datetime) -> str:
    day_label = _today_label(visit_day)
    if _is_business_weekday(visit_day):
        return (
            f"Entendi 😊 Podemos marcar sua visita para {day_label}, das 09:00 às 18:00, "
            "com horário marcado. Qual horário fica melhor para você?"
        )
    return (
        f"A loja fica fechada no {day_label}. Podemos solicitar sua visita em um dia de "
        "atendimento, com horário marcado. Qual dia e horário ficam melhores para você?"
    )


def _reservation_policy(faq: FAQStore) -> str:
    return faq.get("reserva") or (
        "Não trabalhamos com reserva de aparelhos porque alguns clientes reservam "
        "e depois cancelam, e nesse período deixamos de vender o aparelho para outras pessoas."
    )


def _reservation_reply(faq: FAQStore, visit_day: datetime | None = None) -> str:
    reason = _reservation_policy(faq)
    if visit_day is not None:
        return (
            f"{reason} Não consigo garantir que o aparelho continue disponível até a visita. "
            f"{_visit_day_reply(visit_day)}"
        )

    current = _store_now()
    if _is_business_weekday(current):
        if current.hour >= 18:
            return (
                f"{reason} Hoje é {_today_label(current)} e a loja já encerrou o atendimento. "
                f"{_tomorrow_store_reply(faq, include_physical_store=False)}"
            )
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


def _is_technical_assistance_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if re.search(r"\bassistencia\s+tecnica\b", normalized):
        return True

    component = r"(?:bateria|tela|display|vidro|conector|camera|carcaca|microfone|alto\s+falante)"
    action = r"(?:troca\w*|substitu\w*|consert\w*|repar\w*|manuten\w*|arrum\w*)"
    damaged = r"(?:quebrad\w*|trincad\w*|danificad\w*|defeit\w*)"
    device = r"(?:iphone|ipad|macbook|airpods?|apple\s+watch|celular|aparelho|smartphone|telefone)"
    repair_action = r"(?:consert\w*|repar\w*|manuten\w*|arrum\w*)"
    return bool(
        re.search(rf"\b{action}\b.{{0,35}}\b{component}\b", normalized)
        or re.search(rf"\b{component}\b.{{0,35}}\b{action}\b", normalized)
        or re.search(rf"\b{component}\b.{{0,35}}\b{damaged}\b", normalized)
        or re.search(rf"\b{repair_action}\b.{{0,35}}\b{device}\b", normalized)
        or re.search(rf"\b{device}\b.{{0,35}}\b{repair_action}\b", normalized)
    )


_CATALOG_CONTEXT_ACKNOWLEDGEMENT_RE = re.compile(
    r"^(?:(?:muito\s+)?obrigad[oa]s?|valeu|agradeco|perfeito|beleza|blz|show|"
    r"certo|ok|imagina|de nada|por nada)(?:\s+(?:amigo|amiga))?[.!?,\s]*$",
    re.IGNORECASE,
)


def _has_recent_catalog_product_context(
    history: list[dict[str, str]] | None,
    *,
    skip_acknowledgements: bool = False,
) -> bool:
    for entry in reversed(history or []):
        if entry.get("role") == "user":
            if skip_acknowledgements and _CATALOG_CONTEXT_ACKNOWLEDGEMENT_RE.fullmatch(
                _normalize(entry.get("content", ""))
            ):
                continue
            break
        if entry.get("role") != "assistant" or not entry.get("content"):
            continue
        answer = _normalize(entry.get("content", ""))
        if answer in {"imagem enviada", "foto enviada", "fotos enviadas"}:
            continue
        if (
            _has_product_reference(answer)
            and any(
                marker in answer
                for marker in (
                    "r$",
                    "bateria",
                    "disponivel",
                    "disponibilidade",
                    "capacidade",
                    "gb",
                    "seminovo",
                    "lacrado",
                )
            )
        ):
            return True
    return False


def _is_catalog_purchase_advice_question(
    text: str | None,
    history: list[dict[str, str]] | None,
) -> bool:
    if not is_catalog_purchase_advice_request(text):
        return False
    if not history or trade_in_em_andamento(history):
        return False
    if is_trade_in_context_request(text, history):
        return False
    return _has_recent_catalog_product_context(history)


_CATALOG_PRICE_NEGOTIATION_AMOUNT_RE = re.compile(
    r"(?:\br\$\s*(?:\d{1,3}(?:[.,]\d{3})+|\d{3,5})(?:[.,]\d{1,2})?(?!\w)"
    r"|(?:\d{1,3}(?:[.,]\d{3})+|\d{3,5})(?:[.,]\d{1,2})?\s*"
    r"(?:\$|reais?|conto|mil|k)(?!\w))",
    re.IGNORECASE,
)
_CATALOG_PRICE_NEGOTIATION_VERB_RE = re.compile(
    r"\b(?:consegue|conseguem|faz|fazer|melhor(?:ar)?|negociar|negociacao)\b",
    re.IGNORECASE,
)
_CATALOG_PRICE_DISCOUNT_RE = re.compile(
    r"\b(?:descont\w*|abatimento|promoc\w*)\b"
    r"|\b(?:preco|valor)\s+(?:mais\s+)?melhor\b"
    r"|\bmelhor\s+(?:preco|valor)\b",
    re.IGNORECASE,
)


def _has_recent_catalog_sale_context(history: list[dict[str, str]] | None) -> bool:
    for entry in reversed((history or [])[-8:]):
        if entry.get("role") != "assistant" or not entry.get("content"):
            continue
        answer = _normalize(entry.get("content", ""))
        if re.search(
            r"\b(?:favor preencher lista de avaliacao|parte do pagamento|como entrada|"
            r"para troca|na troca|compramos produtos|mediante avaliacao)\b",
            answer,
        ):
            return False
        return bool(
            _has_product_reference(answer)
            and any(
                marker in answer
                for marker in (
                    "r$",
                    "bateria",
                    "disponivel",
                    "disponibilidade",
                    "capacidade",
                    "gb",
                    "seminovo",
                    "lacrado",
                )
            )
        )
    return False


_CATALOG_BUDGET_OBJECTION_RE = re.compile(
    r"\b(?:fora\s+do\s+(?:meu\s+)?orcamento|"
    r"acima\s+do\s+(?:meu\s+)?orcamento|"
    r"nao\s+(?:cabe|entra)\s+no\s+(?:meu\s+)?orcamento)\b",
    re.IGNORECASE,
)
_CATALOG_ACKNOWLEDGEMENT_RE = re.compile(
    r"(?:mas\s+)?(?:obrigad[oa]|valeu|agradeco)(?:\s+(?:amigo|amiga))?[.!?,\s]*"
)


def _has_recent_catalog_offer(
    history: list[dict[str, str]] | None,
    current_text: str | None = None,
) -> bool:
    entries = list(history or [])
    if (
        entries
        and entries[-1].get("role") == "user"
        and _normalize(entries[-1].get("content", "")) == _normalize(current_text)
    ):
        entries.pop()

    customer_messages_after_offer = 0
    for entry in reversed(entries[-8:]):
        role = entry.get("role")
        content = entry.get("content", "")
        if role == "user":
            customer_messages_after_offer += 1
            if customer_messages_after_offer > 1:
                return False
            continue
        if role != "assistant" or not content:
            continue
        answer = _normalize(content)
        if re.search(
            r"\b(?:lista de avaliacao|parte do pagamento|como entrada|"
            r"para troca|na troca|compramos produtos|mediante avaliacao)\b",
            answer,
        ):
            return False
        if (
            _has_product_reference(answer)
            and re.search(r"\br\s*\$\s*[\d.]", answer)
        ):
            return True
    return False


def _is_catalog_budget_objection(
    text: str | None,
    history: list[dict[str, str]] | None,
) -> bool:
    normalized = _normalize(text)
    if re.search(
        r"\b(?:tem|teria|mostra|mostrar|manda|mandar|envia|enviar|"
        r"quero|queria|preciso|consegue|conseguem|faz|fazer|qual|quais|"
        r"quanto|custa|aceita|pix|cartao|parcel\w*|foto|cor|estoque|disponivel)\b",
        normalized,
    ):
        return False
    if _CATALOG_BUDGET_OBJECTION_RE.search(normalized):
        objection = normalized
    elif _CATALOG_ACKNOWLEDGEMENT_RE.fullmatch(normalized):
        objection = next(
            (
                _normalize(entry.get("content", ""))
                for entry in reversed(history or [])
                if entry.get("role") == "user"
                and _normalize(entry.get("content", "")) != normalized
            ),
            "",
        )
    else:
        return False
    return bool(
        _CATALOG_BUDGET_OBJECTION_RE.search(objection)
        and _has_recent_catalog_offer(history, text)
    )


def _is_catalog_price_negotiation(
    text: str | None,
    history: list[dict[str, str]] | None,
) -> bool:
    """Recognize a buyer negotiating a price already quoted by the store."""
    normalized = _normalize(text)
    if not normalized or not history:
        return False
    if trade_in_em_andamento(history) or is_trade_in_context_request(normalized, history):
        return False
    if _is_cash_discount_question(normalized):
        return False
    if re.search(r"\b(?:entrada|sinal|parcel\w*|parcelamento|restante)\b", normalized):
        return False
    if not _has_recent_catalog_sale_context(history):
        return False

    has_discount_request = bool(_CATALOG_PRICE_DISCOUNT_RE.search(normalized))
    has_target_amount = bool(_CATALOG_PRICE_NEGOTIATION_AMOUNT_RE.search(normalized)) or bool(
        re.search(
            r"\b(?:faz|fazer|sair|sai|fica|ficar)\s+por\s+(?:r\$\s*)?"
            r"(?:\d{1,3}(?:[.,]\d{3})+|\d{3,5})(?:[.,]\d{1,2})?\b",
            normalized,
        )
    )
    if not has_discount_request and not has_target_amount:
        return False
    return has_discount_request or bool(_CATALOG_PRICE_NEGOTIATION_VERB_RE.search(normalized))


def _is_catalog_buyer_details_question(
    text: str | None,
    history: list[dict[str, str]] | None = None,
) -> bool:
    """Recognize a buyer checking the condition of an advertised device."""
    normalized = _normalize(text)
    if not normalized:
        return False

    # A generic payment follow-up after a catalog offer is not a device-state question.
    has_device_detail = bool(
        re.search(
            r"\b(?:bateria|tela|display|vidro|pecas?|face\s+id|c[aâ]meras?|"
            r"alto\s+falante|microfone|carregamento|botoes?|trocad\w*|"
            r"substituid\w*|problema\w*|manuten\w*|bloqueio|icloud|"
            r"restric\w*|condic\w*|estado)\b",
            normalized,
        )
    )
    if _is_payment_methods_question(text) and not has_device_detail:
        return False

    has_catalog_context = _has_recent_catalog_product_context(history)
    buyer_context = re.search(
        r"\b(?:interesse\s+em\s+compr\w*|antes\s+de\s+fechar|"
        r"(?:quero|vou|pretendo|gostaria\s+de)\s+compr\w*)\b",
        normalized,
    )
    state_question = re.search(
        r"\b(?:foi|foram|teve|tiveram|funcion\w*|"
        r"problema\w*|manuten\w*|trocad\w*|substituid\w*|bloqueio|"
        r"icloud|restric\w*)\b",
        normalized,
    )
    named_product_state_question = bool(
        _has_product_reference(normalized)
        and re.search(r"\b(?:tem|possui|esta|e|vem)\b", normalized)
        and re.search(r"\b(?:bateria|tela|display|vidro|pecas?)\b", normalized)
        and re.search(r"\b(?:trocad\w*|substituid\w*)\b", normalized)
    )
    box_inclusion_question = bool(
        _has_product_reference(normalized)
        and is_box_inclusion_question(normalized)
    )
    if (
        not buyer_context
        and not (has_catalog_context and state_question)
        and not named_product_state_question
        and not box_inclusion_question
    ):
        return False

    if box_inclusion_question:
        return True

    detail_groups = (
        re.search(r"\b(?:bateria|tela|display|vidro|pecas?)\b", normalized),
        re.search(
            r"\b(?:face\s+id|c[aâ]meras?|alto\s+falante|microfone|"
            r"carregamento|botoes?|funcion\w*)\b",
            normalized,
        ),
        re.search(
            r"\b(?:ja\s+foi|trocad\w*|substituid\w*|problema\w*|manuten\w*)\b",
            normalized,
        ),
        re.search(r"\b(?:bloqueio|icloud|restric\w*)\b", normalized),
    )
    return sum(bool(group) for group in detail_groups) >= 2 or bool(
        has_catalog_context and state_question and any(detail_groups)
    )


def _has_sealed_reference(normalized: str) -> bool:
    return (
        _has_new_condition_reference(normalized)
        or "encomenda" in normalized
        or _is_seminovo_exclusion(normalized)
    )


def _is_seminovo_exclusion(normalized: str) -> bool:
    seminovo_term = r"(?:seminov[oa]s?|semi[\s-]+nov[oa]s?|usad[oa]s?)"
    return bool(
        re.search(
            rf"\b(?:sem\s+(?:ser\s+)?|nao\s+(?:(?:quero|aceito|procuro|busco|sendo|ser)\s+)?)"
            rf"{seminovo_term}\b",
            normalized,
        )
    )


def _has_new_condition_reference(normalized: str) -> bool:
    without_semi_novo = re.sub(r"\bsemi[\s-]+nov[oa]s?\b", " ", normalized)
    return bool(re.search(r"\b(?:lacrad\w*|nov[oa]s?)\b", without_semi_novo))


def _has_seminovo_reference(normalized: str) -> bool:
    if _is_seminovo_exclusion(normalized):
        return False
    return any(
        marker in normalized
        for marker in ("seminovo", "seminovos", "semi novo", "semi novos", "usado", "usados")
    ) or bool(re.search(r"\bsemis?\b", normalized))


def _has_explicit_sealed_condition(normalized: str) -> bool:
    """Recognize a direct sealed condition, excluding fulfillment wording."""
    return _has_new_condition_reference(normalized) or _is_seminovo_exclusion(normalized)


def _is_neutral_encomenda_availability(normalized: str) -> bool:
    """Keep "para encomenda" from hiding an explicitly available used device."""
    return bool(re.search(r"\bencomendas?\b", normalized)) and not (
        _has_explicit_sealed_condition(normalized) or _has_seminovo_reference(normalized)
    )


def _availability_catalog_query(query: str) -> str:
    normalized = _normalize(query)
    if _is_neutral_encomenda_availability(normalized):
        query = re.sub(r"\bencomendas?\b", " ", query, flags=re.IGNORECASE)
    # Mercado Phone stores the product type as singular "Celular". Keep a
    # generic plural request searchable before the later budget filter runs.
    return re.sub(r"\bcelulares\b", "celular", query, flags=re.IGNORECASE)


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
    # "doto" is a common WhatsApp typo for "foto"; keep the correction local
    # to photo intent so it cannot alter unrelated catalog queries.
    has_photo_word = any(
        word in normalized for word in ("foto", "fotos", "doto", "imagem", "imagens")
    )
    has_photo_abbreviation = bool(re.search(r"\bfts?\b", normalized))
    if not has_photo_word and not has_photo_abbreviation:
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


def _has_explicit_photo_device_reference(text: str) -> bool:
    normalized = _normalize(text)
    return bool(
        _catalog_families(normalized)
        or _has_product_reference(normalized)
        or _requested_capacity_keys(text)
        or re.search(
            r"\b(?:celular(?:es)?|aparelhos?|smartphones?|telefones?)\b",
            normalized,
        )
    )


def _strip_historical_accessory_terms_from_photo_query(
    query: str,
    current_query: str,
    request_text: str,
    image_description: str | None = None,
) -> str:
    """Use visual device context without replacing an accessory target."""
    explicitly_names_device = _has_explicit_photo_device_reference(request_text)
    if image_description and not explicitly_names_device:
        normalized_visual = _normalize(image_description)
        visual_subject = re.sub(
            r"^(?:descricao visual da imagem recebida\s*:\s*)",
            "",
            normalized_visual,
        )
        visual_subject = re.sub(
            r"^(?:foto|imagem)(?:\s+(?:mostra|mostrando|de|do|da))?\s*",
            "",
            visual_subject,
        )
        visual_subject = re.sub(r"^(?:um|uma|o|a)\s+", "", visual_subject)
        image_describes_accessory = bool(
            re.match(
                r"(?:capinhas?|capas?|cases?|peliculas?|fontes?|cabos?|"
                r"carregadores?|protetores?(?:\s+de\s+camera)?|suportes?)\b",
                visual_subject,
            )
        )
        explicitly_names_device = (
            not image_describes_accessory
            and _has_explicit_photo_device_reference(image_description)
        )
    if not explicitly_names_device:
        return query
    if not current_query or not query.endswith(current_query):
        return query

    history_context = query[: -len(current_query)]
    if not history_context.strip():
        return query

    history_context = re.sub(
        r"\b(?:pel[ií]culas?|capas?|capinhas?|cases?|fontes?|cabos?|"
        r"carregadores?|protetores?|suportes?)\b",
        " ",
        history_context,
        flags=re.IGNORECASE,
    )
    history_context = re.sub(r"[ \t]+", " ", history_context)
    history_context = re.sub(r" *\n *", "\n", history_context)
    history_context = re.sub(r"\n{2,}", "\n", history_context).strip()
    return "\n".join(part for part in (history_context, current_query) if part)


def _is_broad_promotion_photo_request(text: str) -> bool:
    """Recognize a category-level photo request for promotional iPhones."""
    normalized = _normalize(text)
    return bool(
        _is_photo_request(text)
        and re.search(r"\b(?:promoc\w*|ofert\w*|liquidac\w*)\b", normalized)
        and re.search(r"\biphones?\b", normalized)
        and not _requested_iphone_model_keys(normalized)
        and not _is_accessory_catalog_request(normalized)
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
    "Não aceitamos mais link de pagamento nem pagamento por cartão de crédito online. "
    "Aceitamos PIX, dinheiro, cartão de débito e cartão de crédito na máquina física. "
    "O cartão de crédito pode ser parcelado em até 18 vezes."
)


PAYMENT_METHODS_REPLY = (
    "Sim 😊 Aceitamos PIX, dinheiro, cartão de débito e cartão de crédito. "
    "PIX, dinheiro e cartão de débito têm pagamento integral à vista, sem taxas. "
    "O cartão de crédito pode ser parcelado em até 18 vezes na máquina física, com juros da máquina "
    "variáveis conforme o valor passado no cartão e a quantidade de parcelas."
)


PAYMENT_ONLY_CREDIT_REPLY = (
    "Sim. A única forma de parcelamento é no cartão de crédito, em até 18 vezes "
    "na máquina física. PIX, dinheiro e cartão de débito são pagamentos à vista, "
    "sem taxas; no crédito parcelado há juros da máquina, variáveis conforme o valor "
    "passado no cartão e a quantidade de parcelas. Não parcelamos no boleto ou no PIX."
)


BOLETO_INSTALLMENT_REPLY = (
    "Não parcelamos no boleto. O parcelamento é somente no cartão de crédito, "
    "em até 18 vezes na máquina física."
)


def _is_credit_only_installment_question(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or "parcel" not in normalized:
        return False
    has_only_marker = bool(re.search(r"\b(?:apenas|somente|so)\b", normalized))
    has_credit_marker = bool(
        re.search(r"\b(?:cartao\s+de\s+credito|credito)\b", normalized)
    )
    return has_only_marker and has_credit_marker


def _is_boleto_payment_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or not re.search(r"\bboletos?\b", normalized):
        return False
    if re.search(
        r"\b(?:nao|nunca|jamais)\s+(?:quero|queria|pretendo)\s+"
        r"(?:(?:usar|pagar|trabalhar)(?:\s+(?:com|no|o))?\s+)?boletos?\b",
        normalized,
    ):
        return False
    if re.search(
        r"\b(?:nao|nunca|jamais)\b.{0,35}\b(?:no|por|via|pelo|em)\s+boleto\b",
        normalized,
    ):
        return False
    has_installment_question = _requested_installments(text) is not None or bool(
        re.search(r"\bparcel\w*\b", normalized)
    )
    has_payment_intent = bool(
        re.search(
            r"\b(?:aceit\w*|compr\w*|aprov\w*|pag\w*|quero|queria|gostaria|"
            r"posso|pode|tem|teria|consegu\w*)\b",
            normalized,
        )
        or re.search(r"\btrabalh\w*\s+com\s+(?:o\s+)?boleto\b", normalized)
    )
    has_store_sale_intent = bool(
        re.search(
            r"\b(?:(?:voce|voces)\s+|(?:a|na)\s+loja\s+)?vend(?:e|em|emos|endo)\b",
            normalized,
        )
    ) and not _is_warranty_request(text)
    return has_installment_question or has_payment_intent or has_store_sale_intent


_SHORT_IPHONE_MODEL_SELECTION_RE = re.compile(
    r"^iphone\s+\d{1,2}(?:\s+(?:pro\s+max|pro|max|plus|mini|air|e))?"
    r"(?:\s+\d{2,4}\s*gb)?\s*[?!.]*$",
    re.IGNORECASE,
)


def _is_contextual_boleto_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    if not re.fullmatch(r"(?:e\s+)?como\s+funciona[?!.]*", _normalize(text)):
        return False

    customer_messages_after_boleto = 0
    for entry in reversed((history or [])[-8:]):
        if entry.get("role") != "user":
            continue
        customer_message = entry.get("content", "")
        if _is_boleto_payment_request(customer_message):
            return customer_messages_after_boleto <= 1
        customer_messages_after_boleto += 1
        if (
            customer_messages_after_boleto > 1
            or not _SHORT_IPHONE_MODEL_SELECTION_RE.fullmatch(
                _normalize(customer_message)
            )
        ):
            return False
    return False


def _is_payment_methods_question(text: str) -> bool:
    normalized = _normalize(text)
    if (
        not normalized
        or _is_payment_link_request(text)
        or (
            _is_explicit_catalog_price_request(text)
            and not _is_independent_payment_methods_request(text)
        )
    ):
        return False
    has_cash_method = bool(re.search(r"\b(?:pix|dinheiro|debito)\b", normalized))
    two_card_payment_question = bool(
        re.search(
            r"\b(?:passar|usar|pagar|dividir)\s+(?:(?:em|com)\s+)?"
            r"(?:dois|duas|2)\s+cartoes?\b",
            normalized,
        )
    )
    if any(
        marker in normalized
        for marker in (
            "simulacao",
            "simular",
            "quanto fica",
            "em quantas",
        )
    ):
        return False
    if (
        "parcel" in normalized
        and not has_cash_method
        and not _is_credit_only_installment_question(text)
        and not two_card_payment_question
    ):
        return False
    has_method = bool(re.search(r"\b(?:pix|dinheiro|debito|credito|cartao|cartoes)\b", normalized))
    has_generic_payment_method_phrase = bool(
        re.search(r"\b(?:forma[s]?|metodo[s]?|modelo[s]?)\s+de\s+pagamento\b", normalized)
    )
    has_short_payment_question = bool(
        re.fullmatch(
            r"(?:e\s+)?(?:pagamento|pagamento\s+como\s+funciona|"
            r"como\s+funciona(?:\s+o)?\s+pagamento)[?!.]*",
            normalized,
        )
    )
    if not has_method and not has_generic_payment_method_phrase and not has_short_payment_question:
        return False
    if has_cash_method and any(marker in normalized for marker in ("taxa", "taxas", "tarifa", "juros", "parcel")):
        return True
    has_request = any(
        phrase in normalized
        for phrase in (
            "forma de pagamento",
            "formas de pagamento",
            "metodo de pagamento",
            "metodos de pagamento",
            "aceita",
            "aceitam",
            "posso pagar",
            "pagar com",
            "pagamento",
            "completar o valor",
            "dar o valor",
        )
    )
    return (
        has_request
        or two_card_payment_question
        or _is_credit_only_installment_question(text)
        or normalized in {"pix", "dinheiro", "debito", "cartao", "cartao de debito"}
    )


def _is_cash_discount_question(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    has_cash_method = bool(
        re.search(r"\b(?:pix|dinheiro|debito|a\s+vista)\b", normalized)
    )
    has_discount_marker = bool(
        re.search(r"\b(?:desconto|desconta|descontar|abatimento|promocao)\b", normalized)
    )
    has_price_equivalence_marker = bool(
        re.search(r"\bmesmo\s+(?:valor|preco)\b", normalized)
    )
    return has_cash_method and (has_discount_marker or has_price_equivalence_marker)


def _is_price_validity_question(text: str) -> bool:
    normalized = _normalize(text)
    if (
        not normalized
        or _is_explicit_catalog_price_request(text)
        or not re.search(r"\b(?:valor|preco|precos|cotacao)\b", normalized)
    ):
        return False
    return any(
        phrase in normalized
        for phrase in (
            "vale mais",
            "vale ainda",
            "ainda vale",
            "continua valendo",
            "continua esse valor",
            "continua o valor",
            "valor atual",
            "preco atual",
            "valor valido",
            "preco valido",
            "valor mudou",
            "preco mudou",
            "mudou o valor",
            "mudou o preco",
            "valor aumentou",
            "preco aumentou",
            "aumentou o valor",
            "aumentou o preco",
            "subiu o valor",
            "subiu o preco",
            "ainda esta valido",
            "ainda esta valendo",
        )
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
        "cartao de credito online",
        "cartao online",
        "pagamento online",
        "cartao por pagamento online",
        "passar cartao por pagamento online",
        "passar cartao online",
        "credito online",
        "pagamento online por cartao",
        "pagamento online com cartao",
        "pagar online com cartao",
        "pagar com cartao online",
        "pagar por cartao online",
        "cartao de credito pela internet",
        "cartao pela internet",
        "pagamento pela internet",
    )
    return any(phrase in normalized for phrase in phrases)


def _has_payment_link_installment_context(history: list[dict[str, str]] | None) -> bool:
    """Recognize a prior assistant answer that already selected link payment."""
    for entry in reversed(history or []):
        if entry.get("role") != "assistant":
            continue
        normalized = _normalize(entry.get("content", ""))
        if not normalized or "link de pagamento" not in normalized:
            continue
        has_installment_table = bool(
            re.search(r"\b(?:1[0-2]|[1-9])\s*x\s+de\b", normalized)
        )
        if has_installment_table or "simulacao do parcelamento pelo link" in normalized:
            return True
    return False


def _is_payment_link_followup(text: str, history: list[dict[str, str]] | None) -> bool:
    """Keep link mode for a short installment question after its link table."""
    if _is_payment_link_request(text) or not _has_payment_link_installment_context(history):
        return False
    return (
        _requested_installments(text) is not None
        or _is_installment_rate_question(text)
        or _is_full_installment_request(text)
    )


def _is_installment_rate_question(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or _is_payment_link_request(text):
        return False
    if any(
        marker in normalized
        for marker in ("taxa de entrega", "taxa do frete", "frete", "motoboy", "sedex", "entrega")
    ):
        return False
    return any(
        marker in normalized
        for marker in ("taxa", "taxas", "juros", "tarifa")
    )


def _is_no_interest_installment_question(text: str) -> bool:
    normalized = _normalize(text)
    if not re.search(r"\bsem\s+juros?\b", normalized):
        return False
    return not re.search(r"\b(?:pix|dinheiro|debito)\b", normalized)


def _is_installment_fee_inclusion_question(text: str) -> bool:
    normalized = _normalize(text)
    if not _is_installment_rate_question(text) or _is_no_interest_installment_question(text):
        return False
    return bool(
        re.search(
            r"\b(?:isso|esse|essa|esses|essas|estes|estas|os\s+valores|as\s+parcelas|"
            r"a\s+tabela|o\s+valor|esse\s+total|essa\s+parcela)\b",
            normalized,
        )
    )


def _has_recent_card_installment_simulation(
    history: list[dict[str, str]] | None,
) -> bool:
    for entry in reversed(history or []):
        role = entry.get("role")
        normalized = _normalize(entry.get("content", ""))
        if role == "assistant":
            if (
                "parcelamento do " in normalized
                and "valores calculados para pagamento no cartao de credito" in normalized
                and "total r$" in normalized
            ):
                return True
            if (
                "taxa de juros da maquina" in normalized
                and "acrescimo varia" in normalized
                and "quantidade de parcelas" in normalized
            ):
                continue
            return False
        if role == "user" and not _is_installment_rate_question(entry.get("content", "")):
            return False
    return False


def _is_full_installment_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if re.search(r"\b(?:[1-9]|1[0-8])\s*x\b", normalized):
        return False
    phrases = (
        "como fica o parcelamento",
        "como funciona o parcelamento",
        "como funciona as parcelas",
        "como funcionam as parcelas",
        "como fica parcelado",
        "quanto fica parcelado",
        "quanto fica o parcelamento",
        "quais sao as parcelas",
        "quais as parcelas",
        "quantas vezes",
        "me passa o parcelamento",
        "simulacao de parcelamento",
        "simular parcelamento",
    )
    return any(phrase in normalized for phrase in phrases)


def _is_generic_installment_count_question(text: str) -> bool:
    normalized = _normalize(text)
    asks_how_installments_work = bool(
        re.fullmatch(
            r"como funciona(?:m)? as parcelas(?: de (?:voces|vcs))?[?!.]*",
            normalized,
        )
    )
    return bool(
        normalized
        and ("quantas vezes" in normalized or asks_how_installments_work)
        and re.search(r"\bparcel\w*\b", normalized)
        and not _has_product_reference(normalized)
        and not _is_payment_link_request(text)
        and not _is_boleto_payment_request(text)
    )


def _is_specific_installment_request(text: str) -> bool:
    normalized = _normalize(text)
    return _requested_installments(text) is not None and bool(
        re.search(r"\b(?:quanto|valor|parcela|parcelado|parcelamento|fica)\b", normalized)
    )


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


def _parse_budget_amount(raw_value: str) -> float | None:
    """Parse a price ceiling, accepting comma-separated thousands from customers."""
    # Brazilian currency uses two decimal places; three digits after a comma in
    # a budget ("3,200") therefore indicates a thousands separator.
    if re.fullmatch(r"\d+,\d{3}", raw_value or ""):
        return float(raw_value.replace(",", ""))
    return _parse_brl_amount(raw_value)


def _extract_budget_limit(text: str) -> float | None:
    """Extract a maximum price from a natural-language budget request."""
    normalized = _normalize(text)
    marker_pattern = re.compile(
        r"\b(?:ate(?!\s+la\b)|no maximo(?: de)?|maximo(?: de)?|na faixa de|faixa de|"
        r"orcamento(?: de)?|em torno de|cerca de|por volta de)\b",
    )
    iphone_model_range = re.search(
        r"\b(?P<start>1[0-9])\s+(?P<range_marker>ate)\s+"
        r"(?:(?:o|a)\s+)?(?:iphones?\s*)?(?P<end>1[0-9])\b",
        normalized,
    )
    latest_budget_limit = None
    for marker in marker_pattern.finditer(normalized):
        amount_match = re.search(
            r"(?:r\$\s*)?(?P<value>\d+(?:[.,]\d+)?)(?:\s*(?P<scale>mil|k))?",
            normalized[marker.end() :],
        )
        if not amount_match:
            continue
        amount_start = marker.end() + amount_match.start("value")
        if (
            iphone_model_range is not None
            and re.search(r"\biphones?\b", normalized)
            and marker.start() == iphone_model_range.start("range_marker")
            and amount_start == iphone_model_range.start("end")
            and amount_match.group("value") == iphone_model_range.group("end")
            and not amount_match.group("scale")
        ):
            # "do 14 até o 16" names a model range; its endpoint is not a
            # sixteen-real budget limit. Keep looking for a later price marker.
            continue

        amount = _parse_budget_amount(amount_match.group("value"))
        if amount is None:
            continue
        # Do not treat delivery deadlines, installment counts, or quantities as
        # prices. This matters when a previous catalog answer is part of the
        # follow-up context, for example: "entrega em até 1 semana".
        amount_end = marker.end() + amount_match.end()
        prefix = normalized[:amount_start]
        suffix = normalized[amount_end:]
        # A model generation is commonly written immediately after "iPhone" or
        # followed by its variant, as in "orçamento do iPhone 15 Pro Max". It is
        # a product reference, not a fifteen-real price limit.
        if re.search(r"\biphones?\s*$", prefix) or re.match(
            r"\s*(?:pro(?:\s+max)?|max|plus|mini|air|e)\b", suffix
        ):
            continue
        if re.match(
            r"\s*(?:x\b|semanas?\b|dias?\b|horas?\b|mes(?:es)?\b|"
            r"vez(?:es)?\b|parcelas?\b|unidades?\b|aparelhos?\b|"
            r"celulares?\b|telefones?\b|iphones?\b|gb\b|tb\b|%)",
            suffix,
        ):
            continue
        if amount_match.group("scale") and amount < 1000:
            amount *= 1000
        latest_budget_limit = amount
    return latest_budget_limit


def _extract_latest_budget_limit(text: str) -> float | None:
    """Return the latest ceiling from newline-delimited customer context."""
    latest_limit = None
    for customer_message in (text or "").splitlines():
        amount = _extract_budget_limit(customer_message)
        if amount is not None:
            latest_limit = amount
    return latest_limit


def _requested_device_quantity(text: str) -> int | None:
    normalized = _normalize(text)
    patterns = (
        r"\b(?:preciso|necessito|quero|vou comprar)\s+(?:de\s+)?"
        r"(?P<count>\d{1,2})\s+(?:aparelhos?|celulares?|telefones?|iphones?|unidades?)\b",
        r"\b(?P<count>\d{1,2})\s+(?:aparelhos?|celulares?|telefones?|iphones?|unidades?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            count = int(match.group("count"))
            if count > 0:
                return count
    return None


_ENTRY_AMOUNT_WORD_MULTIPLIERS = {
    "um": 1,
    "uma": 1,
    "dois": 2,
    "duas": 2,
    "tres": 3,
    "quatro": 4,
    "cinco": 5,
    "seis": 6,
    "sete": 7,
    "oito": 8,
    "nove": 9,
    "dez": 10,
}
_ENTRY_AMOUNT_WORD_MULTIPLIER_PATTERN = (
    r"(?:\d+(?:[.,]\d+)?|" + "|".join(_ENTRY_AMOUNT_WORD_MULTIPLIERS) + r")"
)


def _parse_entry_amount(raw_value: str) -> float | None:
    normalized_value = _normalize(raw_value)
    spoken_thousands = re.fullmatch(
        rf"(?:(?P<multiplier>{_ENTRY_AMOUNT_WORD_MULTIPLIER_PATTERN})\s+)?"
        r"mil(?:\s+reais?)?",
        normalized_value,
    )
    if not spoken_thousands:
        return _parse_brl_amount(raw_value)

    multiplier = spoken_thousands.group("multiplier")
    if not multiplier:
        return 1000.0
    if multiplier in _ENTRY_AMOUNT_WORD_MULTIPLIERS:
        return float(_ENTRY_AMOUNT_WORD_MULTIPLIERS[multiplier] * 1000)
    numeric_multiplier = _parse_brl_amount(multiplier)
    return numeric_multiplier * 1000 if numeric_multiplier is not None else None


def _extract_entry_amount(text: str) -> float | None:
    normalized = _normalize(text)
    if "entrada" not in normalized and "sinal" not in normalized:
        return None

    numeric_amount = r"(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?"
    spoken_thousands = (
        rf"(?:(?:{_ENTRY_AMOUNT_WORD_MULTIPLIER_PATTERN})\s+)?mil(?:\s+reais?)?"
    )
    amount = rf"(?:{numeric_amount}|{spoken_thousands})"
    patterns = (
        rf"\b(?:entrada|sinal)\s*(?:(?:a|à)\s+vista\s*)?(?:de|no valor de|:)??\s*(?:r\$\s*)?({amount})",
        rf"\b(?:r\$\s*)?({amount})\s*(?:de\s+)?(?:entrada|sinal)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if match:
            return _parse_entry_amount(match.group(1))
    return None


def _requested_installments(text: str) -> int | None:
    normalized = _normalize(text)
    for pattern in (r"\b(1[0-8]|[1-9])\s*x\b", r"\b(1[0-8]|[1-9])\s+vezes\b"):
        match = re.search(pattern, normalized)
        if match:
            return int(match.group(1))
    return None


def _extract_installment_budget(text: str) -> tuple[int, float] | None:
    """Extract a maximum monthly payment paired with an installment count."""
    normalized = _normalize(text)
    amount_pattern = r"(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{1,2})?"
    count_pattern = r"(?P<count>1[0-8]|[1-9])\s*(?:x|parcelas?|vezes)\b"
    candidates: list[tuple[int, int, float]] = []
    for count_match in re.finditer(count_pattern, normalized):
        amount_match = re.match(
            rf"\s*(?:(?:de|por)\s*)?(?:(?:ate|no\s+maximo|maximo)\s*)?"
            rf"(?:(?:uns?|mais\s+ou\s+menos)\s*)?(?:r\$\s*)?"
            rf"(?P<amount>{amount_pattern})\b",
            normalized[count_match.end() :],
        )
        if amount_match:
            amount = _parse_brl_amount(amount_match.group("amount"))
            if amount is not None:
                candidates.append(
                    (count_match.start(), int(count_match.group("count")), amount)
                )

    reverse_pattern = re.compile(
        rf"\b(?:ate|no\s+maximo|maximo)\s*"
        rf"(?:(?:uns?|mais\s+ou\s+menos)\s*)?(?:r\$\s*)?"
        rf"(?P<amount>{amount_pattern})\s*(?:reais?\s*)?(?:em|no)?\s*"
        rf"{count_pattern}",
    )
    for reverse_match in reverse_pattern.finditer(normalized):
        amount = _parse_brl_amount(reverse_match.group("amount"))
        if amount is not None:
            candidates.append(
                (
                    reverse_match.start(),
                    int(reverse_match.group("count")),
                    amount,
                )
            )
    if not candidates:
        return None
    _position, count, amount = max(candidates, key=lambda candidate: candidate[0])
    return count, amount


def _is_installment_budget_options_request(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Recognize a product-selection request constrained by a monthly ceiling."""
    request_context = _customer_product_request_context_query(text, history)
    if _extract_installment_budget(request_context) is None:
        return False
    normalized_context = _normalize(request_context)
    normalized_current = _normalize(text)
    if not _has_product_reference(normalized_context):
        return False

    asks_for_options = bool(
        re.search(
            r"\b(?:qual|quais)\s+(?:(?:sao|as?)\s+)?"
            r"(?:opcoes?|alternativas?|modelos?|aparelhos?)\b",
            normalized_current,
        )
    )
    has_monthly_ceiling = bool(
        re.search(r"\b(?:ate|no\s+maximo|maximo|limite|orcamento|teto)\b", normalized_context)
    )
    has_purchase_intent = bool(
        re.search(r"\b(?:quero|queria|procuro|procurando|busco|buscando)\b", normalized_context)
    )
    asks_to_calculate = bool(
        re.search(r"\b(?:simula\w*|calcula\w*|quanto\s+(?:fica|custa))\b", normalized_current)
    )
    return asks_for_options or (has_monthly_ceiling and has_purchase_intent and not asks_to_calculate)


def _last_requested_installments(history: list[dict[str, str]] | None) -> int | None:
    for item in reversed(history or []):
        if item.get("role") != "user":
            continue
        requested = _requested_installments(item.get("content", ""))
        if requested is not None:
            return requested
    return None


def _is_both_unit_installment_request(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    recent_user_messages = [
        item.get("content", "").strip()
        for item in (history or [])
        if item.get("role") == "user" and item.get("content", "").strip()
    ][-8:]
    messages = [*recent_user_messages, text]
    explicitly_both = any(
        re.search(
            r"\b(?:os\s+dois|as\s+duas|esses?\s+dois|essas?\s+duas|"
            r"ambos|ambas|cada\s+um|cada\s+uma)\b",
            _normalize(message),
        )
        for message in messages
    )
    has_installment_context = bool(
        _has_installment_model_prompt(history)
        or _requested_installments(text) is not None
        or _last_requested_installments(history) is not None
        or _is_full_installment_request(text)
    )
    return explicitly_both and has_installment_context


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
        re.search(r"\b(?:iphones?|ipads?|macbooks?|airpods?|apple\s+watch)\b", normalized)
        or re.search(r"\b\d{1,2}\s*(?:e|pro|max|air|mini|plus)\b", normalized)
    )


def _has_installment_rate_prompt(history: list[dict[str, str]] | None) -> bool:
    return any(
        entry.get("role") == "assistant"
        and (
            "taxas do cartao na maquina fisica" in _normalize(entry.get("content", ""))
            or "cartao de credito pode ser parcelado somente na maquina fisica"
            in _normalize(entry.get("content", ""))
        )
        and "qual modelo" in _normalize(entry.get("content", ""))
        for entry in (history or [])
    )


def _is_rate_model_followup(text: str, history: list[dict[str, str]] | None) -> bool:
    if (
        _is_photo_request(text)
        or _is_installment_rate_question(text)
        or not _has_installment_rate_prompt(history)
    ):
        return False
    return _has_installment_product_context(text)


def _has_installment_model_prompt(history: list[dict[str, str]] | None) -> bool:
    """Recognize the assistant asking for the product to simulate."""
    for entry in reversed(history or []):
        if entry.get("role") != "assistant":
            continue
        normalized = _normalize(entry.get("content", ""))
        if not normalized or "capacidade" not in normalized:
            continue
        if not re.search(r"\bqu(?:al|ais)\b.{0,48}\b(?:modelos?|aparelhos?)\b", normalized):
            continue
        if any(marker in normalized for marker in ("simul", "parcel", "vezes")):
            return True
    return False


def _history_installment_condition(
    text: str,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Carry a customer's latest condition choice into a product follow-up."""
    normalized = _normalize(text)
    if _has_explicit_sealed_condition(normalized) or _has_seminovo_reference(normalized):
        return None

    current_models = _requested_iphone_model_keys(text)
    if not current_models:
        return None

    def history_model_keys(content: str) -> tuple[tuple[int | str, str], ...]:
        models = _requested_iphone_model_keys(content)
        if models:
            return models
        if _catalog_family(content) != "iphone":
            return ()
        fallback = _model_key(content)
        return (fallback,) if fallback is not None else ()

    history_models: tuple[tuple[int | str, str], ...] = ()
    selected_condition: str | None = None
    for item in history or []:
        content = item.get("content", "")
        item_models = history_model_keys(content)
        if item_models and item.get("role") == "user":
            history_models = item_models
            selected_condition = None
        elif item_models and not history_models:
            history_models = item_models
        if item.get("role") != "user":
            continue
        if _has_explicit_sealed_condition(_normalize(content)):
            selected_condition = "novo lacrado" if set(current_models) & set(history_models) else None
        elif _has_seminovo_reference(_normalize(content)):
            selected_condition = "seminovo" if set(current_models) & set(history_models) else None

    return selected_condition


def _is_installment_model_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Continue an installment prompt unless the customer explicitly requests photos."""
    normalized = _normalize(text)
    refers_to_prior_photos = bool(
        re.search(r"\b(?:esses?|essas?|os?|as?)\s+(?:dois|duas)\s+", normalized)
        and re.search(r"\b(?:iphones?|aparelhos?|celulares?)\b", normalized)
        and re.search(r"\b(?:foto|fotos|imagem|imagens)\b", normalized)
        and not re.search(
            r"\b(?:manda|mande|mandar|envia|envie|enviar|mostra|mostrar|quero\s+ver|"
            r"tem|possui|disponivel)\b",
            normalized,
        )
    )
    return (
        (not _is_photo_request(text) or refers_to_prior_photos)
        and _has_installment_model_prompt(history)
        and _has_installment_product_context(text)
    )


def _format_ambiguous_installment_decision(result: dict[str, Any]) -> AgentDecision | None:
    candidates = result.get("candidatos") or []
    lines: list[str] = []
    references: list[str] = []
    for candidate in candidates[:3]:
        if not isinstance(candidate, dict):
            continue
        reference = str(candidate.get("referencia") or "")
        if reference and reference not in references:
            references.append(reference)
        name = str(candidate.get("nome") or "Produto")
        color = candidate.get("cor")
        capacity = candidate.get("capacidade")
        battery = candidate.get("saude_bateria")
        price = candidate.get("preco_brl")
        details = [name]
        if color:
            details.append(str(color))
        if capacity:
            details.append(str(capacity))
        if battery is not None:
            details.append(f"bateria {float(battery):g}%")
        if price is not None:
            details.append(format_brl(float(price)))
        lines.append(" — ".join(details))
    if not lines:
        return None
    return AgentDecision(
        reply=(
            "Encontrei mais de uma unidade compatível:\n"
            + "\n".join(f"• {line}" for line in lines)
            + "\nQual delas você quer simular?"
        ),
        product_references=references,
        confidence="medium",
    )


def _strip_catalog_history_constraints(value: str) -> str:
    cleaned = re.sub(
        r"\b(?:lacrados?|nov[oa]s?|encomendas?|seminovos?|usados?|entregas?|pagamentos?|"
        r"parcel\w*|taxas?|juros|garantia|reserv\w*|endereco|horario|"
        r"nota\s+fiscal|catalogo)\b",
        " ",
        _normalize(value),
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _has_recent_specific_installment_selection(
    history: list[dict[str, str]] | None,
) -> bool:
    """Keep a customer's specific model choice across later delivery questions."""
    for item in reversed(history or []):
        if item.get("role") != "user":
            continue
        selection = item.get("content", "").strip()
        if not selection or is_trade_in_request(selection):
            continue
        models = _requested_iphone_model_keys(selection)
        if not models:
            continue
        if len(models) != 1:
            return False
        normalized = _normalize(selection)
        return bool(
            len(_requested_capacity_keys(selection)) == 1
            or _requested_battery_health(selection) is not None
            or _requested_catalog_colors(selection, [])
            or _has_explicit_sealed_condition(normalized)
            or _has_seminovo_reference(normalized)
        )
    return False


def _requested_installment_product_keys(value: str) -> tuple[str, ...]:
    """Return specific product models for catalog families with installment pricing."""
    families = _catalog_families(value)
    if len(families) != 1:
        return ()

    family = families[0]
    if family == "iphone":
        return tuple(str(model) for model in _requested_iphone_model_keys(value))
    if family == "ipad":
        return _requested_ipad_model_keys(value)

    patterns = {
        "macbook": (
            r"\bmacbooks?\s+(?:air|pro|neo)(?:\s+m\d+)?"
            r"(?:\s+\d{2}(?:\s*(?:inch|in|pol))?)?\b"
        ),
        "airpods": (
            r"\bair\s*pods?\s+(?:pro(?:\s+\d+)?|max(?:\s+\d+)?|"
            r"\d+(?:\s+(?:anc|usb\s*c))?)\b"
        ),
        "apple_watch": (
            r"\bapple\s+watch\s+(?:(?:series|se|ultra)\s+\d+|se|ultra)"
            r"(?:\s+\d{2}\s*mm)?\b"
        ),
    }
    pattern = patterns.get(family)
    if not pattern:
        return ()
    return tuple(
        dict.fromkeys(
            re.sub(r"\s+", " ", match.group(0)).strip()
            for match in re.finditer(pattern, _normalize(value))
        )
    )


def _customer_installment_price_reference(text: str) -> float | None:
    """Recognize a price the customer is referring to in a follow-up."""
    normalized = _normalize(text)
    if not re.search(
        r"\br\s*\$|\bsem\s+juros?\b|\b(?:preco|valor|custa|custou)\b",
        normalized,
    ):
        return None

    amount_pattern = re.compile(
        r"(?<!\w)(?:r\s*\$\s*)?"
        r"(?P<amount>\d{1,3}(?:[.,]\d{3})*(?:,\d{2})?|\d{4,}(?:[.,]\d{2})?)"
        r"(?!\w)",
        flags=re.IGNORECASE,
    )
    for match in reversed(list(amount_pattern.finditer(text or ""))):
        amount = _parse_brl_amount(match.group("amount"))
        if amount is not None:
            return amount
    return None


def _selected_installment_offer_context(
    history: list[dict[str, str]] | None,
) -> str | None:
    """Resolve a quoted catalog price to the matching offer in recent history."""
    price_reference: tuple[int, float] | None = None
    for index in range(len(history or []) - 1, -1, -1):
        item = history[index]
        if item.get("role") != "user":
            continue
        amount = _customer_installment_price_reference(item.get("content", ""))
        if amount is not None:
            price_reference = (index, amount)
            break
    if price_reference is None:
        return None

    price_message_index, selected_price = price_reference
    if any(
        item.get("role") == "user"
        and _has_product_reference(_normalize(item.get("content", "")))
        for item in (history or [])[price_message_index + 1 :]
    ):
        return None

    price_pattern = re.compile(
        r"\br\s*\$\s*(?P<amount>\d{1,3}(?:[.,]\d{3})*(?:,\d{2})?|\d{4,}(?:[.,]\d{2})?)",
        flags=re.IGNORECASE,
    )
    for item in reversed((history or [])[:price_message_index]):
        if item.get("role") != "assistant":
            continue
        content = item.get("content", "")
        segments = re.split(r"\n+|(?<=[.!?])\s+|[;|]\s*|(?=•)", content)
        matching_segments: list[str] = []
        for segment in segments:
            if not _has_installment_product_context(segment):
                continue
            for match in price_pattern.finditer(segment):
                amount = _parse_brl_amount(match.group("amount"))
                if amount is not None and abs(amount - selected_price) < 0.01:
                    normalized_segment = _normalize(segment)
                    if normalized_segment not in {
                        _normalize(candidate) for candidate in matching_segments
                    }:
                        matching_segments.append(segment.strip())
        if matching_segments:
            return matching_segments[0] if len(matching_segments) == 1 else None
    return None


def _installment_context_query(
    text: str,
    history: list[dict[str, str]] | None,
    *,
    strip_assistant_constraints: bool = False,
) -> str:
    # An explicit product in the current message is the strongest context.
    # Do not append an older catalog answer: its last listed model can make a
    # follow-up such as "Do iPhone 12" inherit an unrelated iPhone XR.
    current = text.strip()
    current_capacity_keys = _requested_capacity_keys(current)
    current_capacity_hint = (
        f"capacidade solicitada: {current_capacity_keys[0]}"
        if len(current_capacity_keys) == 1
        else ""
    )
    product_reference_text = _without_installment_count_list(_normalize(current))
    current_families = _catalog_families(product_reference_text)
    latest_history_families = next(
        (
            families
            for item in reversed(history or [])
            if item.get("content", "").strip()
            for families in (_catalog_families(item.get("content", "")),)
            if families
        ),
        (),
    )
    changed_product_family = bool(
        current_families
        and latest_history_families
        and current_families != latest_history_families
    )
    has_specific_product = bool(
        _requested_installment_product_keys(product_reference_text)
        or _requested_iphone_model_keys(product_reference_text)
        or (current_capacity_keys and _has_product_reference(product_reference_text))
        or changed_product_family
    )
    if has_specific_product or _is_case_accessory_request(current):
        history_condition = _history_installment_condition(current, history)
        return "\n".join(
            part for part in (current_capacity_hint, current, history_condition) if part
        ).strip()

    selected_offer = _selected_installment_offer_context(history)
    if selected_offer:
        return "\n".join(
            part for part in (current_capacity_hint, selected_offer, current) if part
        ).strip()

    # A customer may select one unit from a broad offer, ask about delivery,
    # then ask for installments. Keep that selection ahead of the older list.
    for item in reversed(history or []):
        if item.get("role") != "user":
            continue
        customer_context = item.get("content", "").strip()
        if not customer_context or is_trade_in_request(customer_context):
            continue
        if len(_requested_iphone_model_keys(customer_context)) != 1:
            continue
        normalized_selection = _normalize(customer_context)
        has_selection_detail = (
            len(_requested_capacity_keys(customer_context)) == 1
            or _requested_battery_health(customer_context) is not None
            or bool(_requested_catalog_colors(customer_context, []))
            or _has_explicit_sealed_condition(normalized_selection)
            or _has_seminovo_reference(normalized_selection)
        )
        if not has_selection_detail:
            continue
        history_condition = _history_installment_condition(customer_context, history)
        selected_capacity_keys = _requested_capacity_keys(customer_context)
        assistant_context = next(
            (
                entry.get("content", "")
                for entry in reversed(history or [])
                if entry.get("role") == "assistant"
                and re.search(
                    r"\b(?:iphone|ipad|produto)\w*\b",
                    _normalize(entry.get("content", "")),
                )
            ),
            "",
        )
        assistant_capacity_keys = _requested_capacity_keys(assistant_context)
        selection_capacity_hint = current_capacity_hint
        if not selection_capacity_hint and len(selected_capacity_keys) == 1:
            selection_capacity_hint = f"capacidade solicitada: {selected_capacity_keys[0]}"
        elif not selection_capacity_hint and len(assistant_capacity_keys) == 1:
            selection_capacity_hint = f"capacidade solicitada: {assistant_capacity_keys[0]}"
        return "\n".join(
            part
            for part in (
                selection_capacity_hint,
                customer_context,
                history_condition,
                current,
            )
            if part
        ).strip()

    for item in reversed(history or []):
        assistant_context = item.get("content", "")
        # Word boundaries keep "antecipado" from matching the substring "ipad".
        if item.get("role") == "assistant" and re.search(
            r"\b(?:iphone|ipad|produto)\w*\b",
            _normalize(assistant_context),
        ):
            assistant_context = _strip_unavailable_catalog_claims(assistant_context)
            if strip_assistant_constraints:
                assistant_context = _strip_catalog_history_constraints(assistant_context)
            # Keep a unique capacity from the selected offer/table ahead of catalog ranking.
            assistant_capacity_keys = _requested_capacity_keys(assistant_context)
            assistant_capacity_hint = (
                f"capacidade solicitada: {assistant_capacity_keys[0]}"
                if not current_capacity_hint and len(assistant_capacity_keys) == 1
                else ""
            )
            return "\n".join(
                part
                for part in (
                    current_capacity_hint or assistant_capacity_hint,
                    assistant_context,
                    current,
                )
                if part
            ).strip()
    previous_user_text = [
        item.get("content", "").strip()
        for item in (history or [])
        if item.get("role") == "user" and item.get("content", "").strip()
    ]
    return "\n".join(
        part for part in (current_capacity_hint, *previous_user_text[-4:], current) if part
    ).strip()


def _multi_model_installment_queries(
    text: str,
    history: list[dict[str, str]] | None,
) -> tuple[str, ...]:
    """Split a multi-model installment request while keeping each model's details."""
    normalized = _normalize(text)
    requested_models = _requested_iphone_model_keys(normalized)
    if len(requested_models) <= 1:
        return ()

    mentions = []
    for match in _MODEL_PATTERN.finditer(normalized):
        raw_number = match.group("number") or match.group("legacy")
        if not raw_number:
            continue
        number: int | str = int(raw_number) if raw_number.isdigit() else raw_number.upper()
        variant = " ".join((match.group("variant") or "").split()).lower()
        mentions.append(((number, variant), match.start(), match.end()))
    if tuple(model for model, _start, _end in mentions) != requested_models:
        return ()

    separator_pattern = re.compile(r"\b(?:e|ou|or|and)\b|[,/;]")
    queries: list[str] = []
    segment_start = 0
    for index, (_model, _start, end) in enumerate(mentions[:-1]):
        next_start = mentions[index + 1][1]
        separators = list(separator_pattern.finditer(normalized, end, next_start))
        if not separators:
            return ()
        separator = separators[-1]
        boundary_start = separator.start()
        boundary_end = separator.end()
        segment = normalized[segment_start:boundary_start].strip()
        if _requested_iphone_model_keys(segment) != (requested_models[index],):
            return ()
        queries.append(segment)
        segment_start = boundary_end

    final_segment = normalized[segment_start:].strip()
    if _requested_iphone_model_keys(final_segment) != (requested_models[-1],):
        return ()
    queries.append(final_segment)

    history_condition = _history_installment_condition(text, history)
    if history_condition:
        queries = [f"{query}\n{history_condition}" for query in queries]
    return tuple(queries)


def _photo_alternative_models_context(
    text: str,
    entries: list[tuple[int, str | None, str]],
) -> str | None:
    """Use only the latest offered alternatives for a plural photo follow-up."""
    normalized = _normalize(text)
    if not _is_photo_request(text) or not re.search(r"\b(?:deles|delas)\b", normalized):
        return None

    latest_assistant = next(
        (content for _index, role, content in reversed(entries) if role == "assistant"),
        "",
    )
    alternative_marker = re.search(r"\balternativas?\b", latest_assistant, re.IGNORECASE)
    if alternative_marker is None:
        return None

    offer = latest_assistant[alternative_marker.end() :]
    first_model = re.search(r"\biphone\s+(?:\d{1,2}|xr)\b", offer, re.IGNORECASE)
    if first_model is None:
        return None

    offer = offer[first_model.start() :]
    candidates = re.split(
        r"\s+(?:ou|e)\s+(?=(?:(?:o|a)\s+)?iphone\s+(?:\d{1,2}|xr)\b)",
        offer,
        flags=re.IGNORECASE,
    )
    candidate_lines = [
        re.sub(r"^\s*(?:o|a)\s+(?=iphone\b)", "", candidate, flags=re.IGNORECASE).strip()
        for candidate in candidates
    ]
    model_keys = [
        models[0]
        for candidate in candidate_lines
        if len(models := _requested_iphone_model_keys(candidate)) == 1
    ]
    if len(candidate_lines) < 2 or len(set(model_keys)) < 2:
        return None
    return "\n".join(candidate_lines)


def _product_context_query(
    text: str,
    history: list[dict[str, str]] | None,
    *,
    strip_assistant_constraints: bool = False,
) -> str:
    """Preserva a referência do produto atual ao consultar o histórico."""
    current = text.strip()
    normalized = _normalize(current)
    pending_variant_context = _pending_iphone_variant_price_context(current, history)
    if pending_variant_context:
        return pending_variant_context
    if _has_product_reference(normalized):
        return current

    def is_specific_product_answer(content: str) -> bool:
        answer = _normalize(content)
        return bool(
            _has_product_reference(answer)
            and any(
                marker in answer
                for marker in ("r$", "bateria", "disponivel", "disponibilidade", "capacidade", "gb")
            )
            and not any(marker in answer for marker in ("lista completa", "novos lacrados por encomenda"))
        )

    entries = [
        (index, item.get("role"), item.get("content", "").strip())
        for index, item in enumerate(history or [])
        if item.get("content", "").strip()
    ]
    alternative_models_context = _photo_alternative_models_context(text, entries)
    if alternative_models_context:
        return f"{alternative_models_context}\n{current}".strip()

    anchors = [
        index
        for index, role, content in entries
        if role == "user"
        and (
            _has_product_reference(_normalize(content))
            or _extract_bare_catalog_model_reference(content)
        )
    ]
    anchor_index = anchors[-1] if anchors else 0

    image_anchor = any(
        index == anchor_index and role == "user" and _is_image_history_entry(content)
        for index, role, content in entries
    )

    parts: list[str] = []
    product_answer_index: int | None = None
    if not image_anchor:
        for index, role, content in reversed(entries):
            if index >= anchor_index and role == "assistant" and is_specific_product_answer(content):
                context_content = re.sub(
                    r"\b(?:pelicula|capa|capinha|case|fonte|cabo|carregador|protetor|suporte)s?\b",
                    " ",
                    content,
                ).strip()
                context_content = _strip_unavailable_catalog_claims(context_content)
                context_content = "\n".join(
                    re.sub(r"\s+", " ", line).strip()
                    for line in context_content.splitlines()
                    if line.strip()
                )
                if strip_assistant_constraints:
                    context_content = _strip_catalog_history_constraints(context_content)
                if context_content and context_content not in parts:
                    parts.append(context_content)
                    product_answer_index = index
                break
    if product_answer_index is not None:
        anchor_content = next(
            (
                content
                for index, role, content in entries
                if index == anchor_index and role == "user"
            ),
            "",
        )
        anchor_models = _requested_iphone_model_keys(anchor_content)
        preserve_anchor = bool(
            anchor_content
            and _has_product_reference(_normalize(anchor_content))
            and len(anchor_models) <= 1
        )
        pre_answer_user_start = anchor_index if preserve_anchor else anchor_index + 1
        parts.extend(
            content
            for index, role, content in entries
            if pre_answer_user_start <= index < product_answer_index
            and role == "user"
            and content not in parts
        )
    user_context_start = (
        product_answer_index + 1 if product_answer_index is not None else anchor_index
    )
    for index, role, content in entries:
        if index >= user_context_start and role == "user" and content not in parts:
            parts.append(content)
    parts.append(current)
    return "\n".join(parts[-6:]).strip()


def _pending_iphone_variant_price_context(
    text: str,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Resolve a variant named in a short customer follow-up before a price question."""
    if not history or not _is_catalog_price_followup(text):
        return None

    entries = [
        (index, entry.get("role"), entry.get("content", "").strip())
        for index, entry in enumerate(history)
        if entry.get("content", "").strip()
    ]
    latest_assistant_index = max(
        (index for index, role, _content in entries if role == "assistant"),
        default=-1,
    )
    pending_users = [
        content
        for index, role, content in entries
        if role == "user" and index > latest_assistant_index
    ]
    if len(pending_users) < 2:
        return None

    variant_message = _normalize(pending_users[-1])
    if not re.fullmatch(r"(?:(?:e|ou)\s+)?(?:(?:o|a)\s+)?pro\s+max[?!.,]*", variant_message):
        return None

    base_request = pending_users[-2]
    models = _requested_iphone_model_keys(base_request)
    if (
        len(models) != 1
        or models[0][1]
        or not isinstance(models[0][0], int)
        or not _is_product_availability_request(base_request)
        or is_trade_in_request(base_request)
    ):
        return None

    generation = models[0][0]
    residual_request = re.sub(
        rf"\b(?:iphone\s*)?{generation}\b",
        " ",
        base_request,
        flags=re.IGNORECASE,
    )
    residual_request = re.sub(r"[\s?!.,;]+$", "", residual_request).strip()
    residual_request = re.sub(
        r"\b(?:o|a|um|uma)\s*$",
        "",
        residual_request,
        flags=re.IGNORECASE,
    ).strip()

    parts = [f"iPhone {generation} Pro Max"]
    if residual_request:
        parts.append(residual_request)
    parts.append(text.strip())
    return "\n".join(parts)


def _pending_multi_model_product_request_context(
    text: str,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Keep all iPhone models requested before the bot answers the batch."""
    entries = [
        (index, entry.get("role"), entry.get("content", "").strip())
        for index, entry in enumerate(history or [])
        if entry.get("content", "").strip()
    ]
    latest_assistant_index = max(
        (index for index, role, _content in entries if role == "assistant"),
        default=-1,
    )
    pending_users = [
        (index, content)
        for index, role, content in entries
        if role == "user" and index > latest_assistant_index
    ]
    current = text.strip()
    current_messages = [
        (len(entries) + offset, line.strip())
        for offset, line in enumerate(current.splitlines())
        if line.strip()
    ] or [(len(entries), current)]
    requested_messages: list[tuple[int, str]] = []
    previous_message_was_product_request = False

    def is_owned_device_context(content: str) -> bool:
        normalized = _normalize(content)
        return bool(
            re.search(
                r"^(?:(?:e|ou|mas)\s+)?(?:(?:o|a)\s+)?(?:meu|minha|meus|minhas)\b"
                r"|^(?:atualmente\s+)?(?:eu\s+)?(?:tenho|possuo|uso)\s+"
                r"(?:(?:um|uma|o|a)\s+)?(?:(?:meu|minha)\s+)?"
                r"(?:iphone|ipad|mac(?:book)?|airpods?|apple\s+watch)\b"
                r"|^(?:vendo|vou\s+vender|troco|ofereco)\s+"
                r"(?:(?:o|a)\s+)?(?:meu|minha)\b",
                normalized,
            )
        )

    def has_negated_purchase_intent(content: str) -> bool:
        return bool(
            re.search(
                r"\bnao\s+(?:quero|queria|gostaria|vou|pretendo)\s+(?:de\s+)?"
                r"(?:comprar|ver|saber|conhecer)\b",
                _normalize(content),
            )
        )

    def request_segments(content: str) -> list[str]:
        request_intent_start = (
            r"(?:(?:voc[eê]s?|vcs|a\s+loja)\s+)?"
            r"(?:(?:sabe|sabem)(?:\s+se)?\s+)?"
            r"(?:(?:voc[eê]s?|vcs|a\s+loja)\s+)?"
            r"(?:(?:o|a)\s+)?(?:gostaria|queria|quero|procuro|procurando|preciso|necessito|"
            r"possibilidade|quanto|qual|quais|pre[cç]o|valor|custa|t[eê]m|teria|"
            r"vende|possui|disponivel)\b"
        )
        segments = re.split(
            r"\s+\b(?:mas|por[eé]m|contudo|no\s+entanto)\b\s+"
            rf"|\?\s*(?={request_intent_start})"
            rf"|[,;]\s*(?={request_intent_start})"
            rf"|\s+\be\b\s+(?={request_intent_start})",
            content,
            flags=re.IGNORECASE,
        )
        if len(segments) > 1 and any(
            is_owned_device_context(segment)
            or is_trade_in_request(segment)
            or has_negated_purchase_intent(segment)
            for segment in segments
        ):
            return [segment.strip() for segment in segments if segment.strip()]
        return [content]

    for index, content in [*pending_users, *current_messages]:
        for segment in request_segments(content):
            has_model_reference = bool(
                _has_product_reference(_normalize(segment))
                or _extract_bare_catalog_model_reference(segment)
            )
            normalized_segment = _normalize(segment)
            is_owned_context = is_owned_device_context(segment)
            is_trade_context = is_trade_in_request(normalized_segment)
            is_negated_purchase = has_negated_purchase_intent(segment)
            has_catalog_purchase_intent = not is_negated_purchase and any(
                phrase in normalized_segment
                for phrase in (
                    "gostaria de ver",
                    "gostaria de saber mais",
                    "gostaria de informacoes sobre",
                    "queria ver",
                    "quero ver",
                    "quero comprar",
                    "queria comprar",
                    "gostaria de comprar",
                    "possibilidade de comprar",
                    "procuro",
                    "procurando",
                    "preciso",
                    "necessito",
                )
            )
            has_stock_request_opening = bool(
                re.search(
                    r"^(?:(?:oi|ola|bom\s+dia|boa\s+tarde|boa\s+noite)[,! ]+)?"
                    r"(?:(?:voce|voces|vcs|a\s+loja)\s+)?"
                    r"(?:(?:sabe|sabem)\s+se\s+)?"
                    r"(?:(?:voce|voces|vcs|a\s+loja)\s+)?"
                    r"(?:tem|teria|vende|vendem|possui|possuem)\b",
                    normalized_segment,
                )
            )
            has_availability_detail = bool(
                re.search(
                    r"\b(?:disponivel|disponibilidade|em\s+estoque|estoque|"
                    r"a\s+venda|encomenda)\b",
                    normalized_segment,
                )
            )
            has_availability_question = bool(
                has_availability_detail
                and (
                    "?" in segment
                    or re.search(r"\b(?:qual|quais|sabe\s+se|voce|voces|vcs)\b", normalized_segment)
                )
            )
            has_request_style_availability = not (
                is_owned_context or is_trade_context or is_negated_purchase
            ) and (has_stock_request_opening or has_availability_question)
            explicit_product_request = has_model_reference and not (
                is_owned_context or is_negated_purchase
            ) and (
                _is_explicit_catalog_price_request(segment)
                or _is_catalog_buyer_interest(segment)
                or has_catalog_purchase_intent
                or has_request_style_availability
            )
            is_followup_model_request = bool(
                has_model_reference
                and not (is_owned_context or is_trade_context or is_negated_purchase)
                and previous_message_was_product_request
                and re.match(r"^(?:e|ou|tambem|mais)\b", _normalize(segment))
            )
            if explicit_product_request or is_followup_model_request:
                requested_messages.append((index, segment))
                previous_message_was_product_request = True
            else:
                previous_message_was_product_request = False

    candidate_messages = requested_messages
    current_models = ()
    if (
        _has_product_reference(_normalize(current))
        or _extract_bare_catalog_model_reference(current)
    ):
        current_models = _requested_iphone_model_keys(current)

    requested_models: list[tuple[int | str, str]] = []
    model_message_indexes: list[int] = []
    for index, content in candidate_messages:
        if not (
            _has_product_reference(_normalize(content))
            or _extract_bare_catalog_model_reference(content)
        ):
            continue
        models = _requested_iphone_model_keys(content)
        if not models:
            continue
        model_message_indexes.append(index)
        for model in models:
            if model not in requested_models:
                requested_models.append(model)

    all_referenced_models = {
        model
        for _index, content in [*pending_users, *current_messages]
        if _has_product_reference(_normalize(content))
        or _extract_bare_catalog_model_reference(content)
        for model in _requested_iphone_model_keys(content)
    }
    contains_excluded_model = bool(all_referenced_models - set(requested_models))
    if (
        not requested_models
        or (len(requested_models) <= 1 and not contains_excluded_model)
        or (len(set(model_message_indexes)) <= 1 and not contains_excluded_model)
        or (set(requested_models).issubset(current_models) and not contains_excluded_model)
    ):
        return None

    def model_label(model: tuple[int | str, str]) -> str:
        generation, variant = model
        generation_label = (
            str(generation).upper() if isinstance(generation, str) else str(generation)
        )
        return " ".join(part for part in ("iPhone", generation_label, variant.title()) if part)

    first_model_message = min(model_message_indexes)
    context_messages = [
        content
        for index, content in requested_messages
        if index < len(entries)
        if index >= first_model_message
    ]
    requested_current_context = "\n".join(
        content
        for index, content in requested_messages
        if index >= len(entries) and index >= first_model_message
    )
    if requested_current_context and (
        not context_messages or requested_current_context != context_messages[-1]
    ):
        context_messages.append(requested_current_context)
    model_header = " / ".join(model_label(model) for model in requested_models)
    return "\n".join([model_header, *context_messages]).strip()


def _customer_product_request_context_query(
    text: str,
    history: list[dict[str, str]] | None,
) -> str:
    """Keep customer requests within the current product conversation."""
    current = text.strip()
    pending_multi_model_context = _pending_multi_model_product_request_context(text, history)
    if pending_multi_model_context:
        return pending_multi_model_context

    user_entries = [
        (index, entry.get("content", "").strip())
        for index, entry in enumerate(history or [])
        if entry.get("role") == "user" and entry.get("content", "").strip()
    ]
    if not user_entries:
        return current

    current_families = set(_catalog_families(current))
    if not current_families:
        for _index, content in reversed(user_entries):
            if _has_product_reference(_normalize(content)) or _extract_bare_catalog_model_reference(content):
                current_families = set(_catalog_families(content))
                if current_families:
                    break

    anchor_index: int | None = None
    for index, content in reversed(user_entries):
        content_families = set(_catalog_families(content))
        if current_families and content_families and not current_families.intersection(content_families):
            break
        if _has_product_reference(_normalize(content)) or _extract_bare_catalog_model_reference(content):
            anchor_index = index
            break
    if anchor_index is None:
        return current

    relevant_user_messages = [
        content
        for index, content in user_entries
        if index >= anchor_index
        and (
            not current_families
            or not _catalog_families(content)
            or current_families.intersection(_catalog_families(content))
        )
    ]
    return "\n".join([*relevant_user_messages, current]).strip()


def _customer_product_condition_context_query(
    text: str,
    history: list[dict[str, str]] | None,
) -> str:
    """Carry prior conditions unless the customer states a new one now."""
    current = text.strip()
    normalized_current = _normalize(current)
    if _has_explicit_sealed_condition(normalized_current) or _has_seminovo_reference(
        normalized_current
    ):
        return current
    return _customer_product_request_context_query(text, history)


def _extract_bare_catalog_model_reference(text: str) -> str | None:
    """Turn follow-ups such as "a bateria do 15" into an explicit model."""
    pattern = re.compile(
        r"\b(?:do|da|dos|das|de|no|na|o|a|um|uma|esse|essa|este|esta)\s+"
        r"(?:iphone\s*)?(?P<number>\d{1,2})"
        r"(?P<variant>\s*(?:e|pro\s+max|pro|max|plus|mini|air))?\b",
        flags=re.IGNORECASE,
    )
    matches = []
    for match in pattern.finditer(text or ""):
        # A thousands separator lets the first digit of "1.000" or "1,500"
        # look like a bare iPhone generation after a preposition ("faixa de").
        # Ignore that component while preserving an actual model earlier in
        # the same request, such as "o 15 na faixa de 1.000 reais".
        amount_tail = (text or "")[match.end("number") :]
        if re.match(r"\s*[.,]\s*\d{3}(?!\d)", amount_tail):
            continue
        if re.match(r"\s*%|\s+por\s+cento\b", amount_tail, flags=re.IGNORECASE):
            continue
        if re.match(
            r"\s*(?:(?:a|de)\s+)?saude(?:\s+da)?\s+bateria\b",
            _normalize(amount_tail),
            flags=re.IGNORECASE,
        ):
            continue
        matches.append(match)
    if not matches:
        return None
    match = matches[-1]
    number = match.group("number")
    variant = " ".join((match.group("variant") or "").split()).lower()
    if variant == "e":
        return f"iPhone {number}e"
    if variant:
        return f"iPhone {number} {variant}"
    return f"iPhone {number}"


_BARE_MODEL_CATALOG_FOLLOWUP_RE = re.compile(
    r"^e\s+(?:o|a|um|uma|no|na|do|da|dos|das)?\s*(?:iphone\s*)?\d{1,2}"
    r"(?:\s+(?:pro\s+max|pro|max|plus|mini|air))?"
    r"(?:[, ]+(?:meu\s+amigo|minha\s+amiga))?\s*[?!.,]*$",
    re.IGNORECASE,
)
_BARE_VARIANT_CATALOG_FOLLOWUP_RE = re.compile(
    r"^(?:iphone\s*)?\d{1,2}\s+(?:pro\s+max|pro|max|plus|mini|air)\s*[?!.,]*$",
    re.IGNORECASE,
)


def _is_bare_model_catalog_followup(
    text: str | None,
    history: list[dict[str, str]] | None,
) -> bool:
    """Recognize a short model follow-up after a catalog answer."""
    normalized = _normalize(text)
    is_model_followup = bool(
        _BARE_MODEL_CATALOG_FOLLOWUP_RE.fullmatch(normalized)
        or _BARE_VARIANT_CATALOG_FOLLOWUP_RE.fullmatch(normalized)
    )
    return bool(
        history
        and normalized
        and is_model_followup
        and _has_recent_catalog_product_context(history)
    )


def _is_catalog_model_confirmation(
    text: str | None,
    history: list[dict[str, str]] | None,
) -> bool:
    """Recognize a short model confirmation after the bot asks which model."""
    normalized = _normalize(text)
    match = re.fullmatch(
        r"(?:iphone\s*)?(?P<number>\d{1,2})"
        r"(?:\s+(?P<variant>pro\s+max|pro|max|plus|mini|air|e))?\s+mesmo[.!?]*",
        normalized,
    )
    if not match or not history:
        return False

    latest_assistant = next(
        (
            _normalize(entry.get("content", ""))
            for entry in reversed(history)
            if entry.get("role") == "assistant" and entry.get("content", "").strip()
        ),
        "",
    )
    if not re.search(r"\b(?:quis dizer|outro modelo|qual modelo)\b", latest_assistant):
        return False

    latest_user = next(
        (
            entry.get("content", "")
            for entry in reversed(history)
            if entry.get("role") == "user" and entry.get("content", "").strip()
        ),
        "",
    )
    if not _has_product_reference(_normalize(latest_user)):
        return False

    generation = int(match.group("number"))
    requested_models = _requested_iphone_model_keys(latest_user)
    if match.group("variant"):
        variant = _normalize(match.group("variant"))
        return (generation, variant) in requested_models
    return any(model[0] == generation for model in requested_models)


def _is_bare_model_availability_request(text: str) -> bool:
    """Recognize a shorthand model only when the customer asks about sale/stock."""
    normalized = _normalize(text)
    if (
        not normalized
        or _has_product_reference(normalized)
        or _extract_bare_catalog_model_reference(normalized) is None
    ):
        return False
    return bool(
        re.search(
            r"\b(?:tem|teria|disponivel|disponibilidade|estoque|vende|vender|possui|"
            r"valor|valores|preco|precos|custa)\b"
            r"|\bquanto\s+(?:custa|fica)\b",
            normalized,
        )
    )


_BARE_MODEL_SELECTION_REPLY_RE = re.compile(
    r"^(?:iphone\s*)?(?P<number>\d{1,2})"
    r"(?:\s*(?P<e>e)|\s+(?P<variant>pro\s+max|pro|max|plus|mini|air))?[?!.,]*$",
    re.IGNORECASE,
)


def _bare_model_selection_reply(text: str) -> str | None:
    normalized = _normalize(text).strip()
    match = _BARE_MODEL_SELECTION_REPLY_RE.fullmatch(normalized)
    if not match:
        return None
    model = f"iPhone {match.group('number')}"
    if match.group("e"):
        return f"{model}e"
    variant = " ".join((match.group("variant") or "").split())
    return f"{model} {variant}".strip()


def _is_product_interest_prompt(text: str) -> bool:
    normalized = _normalize(text)
    asks_interest = bool(
        re.search(r"\binteresse\b", normalized)
        and re.search(r"\b(?:modelo|produto|iphone|aparelho)\b", normalized)
    )
    asks_model = bool(
        re.search(r"\bqual\s+(?:modelo|produto|iphone|aparelho)\b", normalized)
        and re.search(r"\b(?:procura|procurando|busca|interessa)\b", normalized)
    )
    asks_specific = bool(
        re.search(r"\balgum\s+(?:modelo|produto|iphone|aparelho)\s+especifico\b", normalized)
    )
    return asks_interest or asks_model or asks_specific


_CATALOG_BUYER_INTEREST_RE = re.compile(
    r"\b(?:fiquei|estou|to|tô)\s+interessad[oa]\b"
    r"|\bme\s+interessei\s+(?:no|na|em|nesse|nessa|neste|nesta)\b"
    r"|\btenho\s+(?:o\s+)?interesse\s+(?:no|na|em|nesse|nessa|neste|nesta)\b",
    re.IGNORECASE,
)
_CATALOG_BUYER_INTEREST_NEGATION_RE = re.compile(
    r"\b(?:nao|nunca)\s+(?:fiquei|estou|to|tô|me\s+interessei|"
    r"tenho\s+(?:o\s+)?interesse)\b",
    re.IGNORECASE,
)
_CATALOG_BUYER_TRADE_IN_SIGNAL_RE = re.compile(
    r"\b(?:troca|trocar|troco|vender|avaliar|avaliacao|"
    r"parte\s+do\s+pagamento)\b",
    re.IGNORECASE,
)


def _is_catalog_buyer_interest(text: str | None) -> bool:
    normalized = _normalize(text or "")
    return bool(
        normalized
        and _CATALOG_BUYER_INTEREST_RE.search(normalized)
        and not _CATALOG_BUYER_INTEREST_NEGATION_RE.search(normalized)
        and not _CATALOG_BUYER_TRADE_IN_SIGNAL_RE.search(normalized)
        and not is_trade_in_request(normalized)
    )


def _product_interest_default_condition(text: str) -> str | None:
    normalized = _normalize(text)
    has_seminovo = _has_seminovo_reference(normalized)
    has_sealed = _has_explicit_sealed_condition(normalized)
    if has_seminovo and has_sealed:
        return None
    if has_sealed:
        return "lacrado"
    return "seminovo"


def _product_interest_model_context(
    text: str,
    history: list[dict[str, str]] | None,
) -> tuple[str, str | None] | None:
    """Resolve a bare model selection and its immediate generic price follow-up."""
    model = _bare_model_selection_reply(text)
    if model:
        if not history or history[-1].get("role") != "assistant":
            return None
        prompt = history[-1].get("content", "")
        if not _is_product_interest_prompt(prompt):
            return None
        return model, _product_interest_default_condition(prompt)

    normalized = _normalize(text)
    if _has_product_reference(normalized) or not re.search(
        r"\b(?:valor(?:es)?|precos?|quanto|custa)\b", normalized
    ):
        return None

    latest_user_index = next(
        (
            index
            for index in range(len(history or []) - 1, -1, -1)
            if history[index].get("role") == "user"
        ),
        None,
    )
    if latest_user_index is None:
        return None
    model = _bare_model_selection_reply(history[latest_user_index].get("content", ""))
    if not model:
        return None

    previous_assistant = next(
        (
            history[index].get("content", "")
            for index in range(latest_user_index - 1, -1, -1)
            if history[index].get("role") == "assistant"
        ),
        "",
    )
    if not _is_product_interest_prompt(previous_assistant):
        return None
    return model, _product_interest_default_condition(previous_assistant)


_CATALOG_BATTERY_UNIT_SELECTION_RE = re.compile(
    r"^\s*(?:(?:quero|queria|vou\s+querer|prefiro|escolho|escolheria|"
    r"fico\s+com|vou\s+ficar\s+com)\s+)?"
    r"(?:(?:a|o|uma|um|essa|esse|esta|este)\s+)?(?:de\s+)?"
    r"(?P<health>\d{1,3}(?:[.,]\d+)?)\s*(?:%|por\s+cento)?"
    r"(?:\s+(?:(?:a|de)\s+)?saude(?:\s+da)?\s+bateria)?[.!?]*\s*$",
    re.IGNORECASE,
)


def _catalog_battery_unit_selection_query(
    text: str,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Resolve a short battery-health choice against the recent product offer."""
    match = _CATALOG_BATTERY_UNIT_SELECTION_RE.fullmatch(_normalize(text))
    if not match or not history or history[-1].get("role") != "assistant":
        return None

    try:
        requested_health = float(match.group("health").replace(",", "."))
    except ValueError:
        return None
    if not 0 <= requested_health <= 100:
        return None

    latest_offer = history[-1].get("content", "")
    offered_healths: list[float] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\r?\n", latest_offer):
        normalized_sentence = _normalize(sentence)
        if not re.search(r"\bbateria\b", normalized_sentence):
            continue
        for health_match in re.finditer(
            r"\b(\d{1,3}(?:[.,]\d+)?)\s*(?:%|por\s+cento)(?!\w)",
            normalized_sentence,
        ):
            try:
                offered_health = float(health_match.group(1).replace(",", "."))
            except ValueError:
                continue
            if 0 <= offered_health <= 100:
                offered_healths.append(round(offered_health, 2))

    if offered_healths.count(round(requested_health, 2)) != 1:
        return None

    latest_seminovo = _has_seminovo_reference(latest_offer)
    latest_sealed = _has_explicit_sealed_condition(latest_offer)
    if latest_seminovo and latest_sealed:
        return None
    latest_condition = (
        "seminovo" if latest_seminovo else "lacrado" if latest_sealed else None
    )

    recent_entries = list(enumerate(history[max(0, len(history) - 8) :], start=max(0, len(history) - 8)))
    context: tuple[tuple[int | str, str], str, str] | None = None
    for context_index, entry in reversed(recent_entries):
        if entry.get("role") != "assistant":
            continue
        candidate_offer = entry.get("content", "")
        models = _requested_iphone_model_keys(candidate_offer)
        capacities = _requested_capacity_keys(candidate_offer)
        candidate_seminovo = _has_seminovo_reference(candidate_offer)
        candidate_sealed = _has_explicit_sealed_condition(candidate_offer)
        if (
            len(models) != 1
            or len(capacities) != 1
            or candidate_seminovo == candidate_sealed
        ):
            continue

        candidate_condition = "seminovo" if candidate_seminovo else "lacrado"
        if latest_condition and latest_condition != candidate_condition:
            continue

        context_is_stale = False
        for _user_index, user_entry in recent_entries:
            if not (context_index < _user_index < len(history) - 1):
                continue
            if user_entry.get("role") != "user":
                continue
            user_content = user_entry.get("content", "")
            user_families = _catalog_families(user_content)
            user_models = _requested_iphone_model_keys(user_content)
            user_capacities = _requested_capacity_keys(user_content)
            if (
                (user_families and user_families != ("iphone",))
                or (
                    _has_product_reference(_normalize(user_content))
                    and user_models
                    and user_models != models
                )
                or (user_capacities and user_capacities != capacities)
            ):
                context_is_stale = True
                break
        if context_is_stale:
            continue

        context = (models[0], capacities[0], latest_condition or candidate_condition)
        break

    if context is None:
        return None

    (model_number, variant), capacity, condition = context
    model_label = f"iPhone {model_number}"
    if variant:
        model_label += f" {variant}"
    return (
        f"{model_label} {capacity.upper()} {condition} "
        f"bateria {requested_health:g}%"
    )


def _battery_detail_context_query(text: str, history: list[dict[str, str]] | None) -> str | None:
    normalized = _normalize(text)
    health_reference = None
    if _is_battery_detail_request(text):
        health_match = re.search(
            r"\b(?:esse|essa|este|esta)\s+de\s+"
            r"(?P<health>\d{1,3}(?:[.,]\d+)?)\s*(?:%|por\s+cento)?\s+"
            r"(?:e|__copula__|tem|esta|possui)\s+(?:a\s+)?bateria\b",
            normalized,
        )
        if health_match:
            try:
                health_reference = float(health_match.group("health").replace(",", "."))
            except ValueError:
                return None
            if not 0 <= health_reference <= 100:
                return None

            if not history or history[-1].get("role") != "assistant":
                return None
            latest_offer = history[-1].get("content", "")
            models = _requested_iphone_model_keys(latest_offer)
            capacities = _requested_capacity_keys(latest_offer)
            has_seminovo = _has_seminovo_reference(latest_offer)
            has_sealed = _has_explicit_sealed_condition(latest_offer)
            if len(models) != 1 or len(capacities) != 1 or has_seminovo == has_sealed:
                return None

            offered_healths: list[float] = []
            for sentence in re.split(r"(?<=[.!?])\s+|\r?\n", latest_offer):
                normalized_sentence = _normalize(sentence)
                if not re.search(r"\bbateria\b", normalized_sentence):
                    continue
                for match in re.finditer(
                    r"\b(\d{1,3}(?:[.,]\d+)?)\s*(?:%|por\s+cento)(?!\w)",
                    normalized_sentence,
                ):
                    try:
                        offered_healths.append(
                            float(match.group(1).replace(",", "."))
                        )
                    except ValueError:
                        continue
            if round(health_reference, 2) not in {
                round(value, 2) for value in offered_healths
            }:
                return None

            (number, variant) = models[0]
            model_label = f"iPhone {number}"
            if variant:
                model_label += f" {variant}"
            condition = "seminovo" if has_seminovo else "lacrado"
            return (
                f"{model_label} {capacities[0].upper()} {condition} "
                f"bateria {health_reference:g}%"
            )

    model_reference = _extract_bare_catalog_model_reference(text)
    has_explicit_family = bool(
        re.search(r"\b(?:iphone|ipad|macbook|airpods?|apple\s+watch)\b", normalized)
    )
    if model_reference is None and not has_explicit_family:
        return None

    user_context = [
        entry.get("content", "").strip()
        for entry in (history or [])
        if entry.get("role") == "user" and entry.get("content", "").strip()
    ]
    parts = [*user_context[-4:], text.strip()]
    if model_reference:
        parts.append(model_reference)
    return "\n".join(part for part in parts if part).strip()


def _strip_unavailable_catalog_claims(value: str) -> str:
    """Keep unavailable alternatives from becoming targets after a positive listing."""
    pattern = re.compile(
        r"\b(?:n[aã]o\s+(?:temos?|encontr\w*|localiz\w*|h[aá]|"
        r"exist\w*|disponibiliz\w*)|indispon[ií]vel)\b"
        r"[^.!?\r\n]*(?:[.!?]|(?=\r?\n)|$)",
        flags=re.IGNORECASE,
    )

    def positive_offer_suffix(claim: str) -> str | None:
        def is_positive_offer(candidate: str) -> bool:
            normalized = _normalize(candidate)
            if not normalized or re.search(r"\b(?:nao|nem|indisponivel)\b", normalized):
                return False
            has_offer_detail = bool(
                re.search(
                    r"r\$|\b(?:bateria|bat|\d+\s*(?:gb|tb)|disponivel|"
                    r"disponibilidade|encontrei|temos|tenho)\b",
                    normalized,
                )
            )
            return _has_product_reference(normalized) and has_offer_detail

        for clause in claim.split(";"):
            contrast = re.search(
                r"\b(?:mas|por[eé]m|contudo|entretanto)\b",
                clause,
                re.IGNORECASE,
            )
            if contrast:
                suffix = clause[contrast.start():]
                if is_positive_offer(clause[contrast.end():]):
                    return suffix
            if is_positive_offer(clause):
                return clause
        return None

    def remove_if_following_listing(match: re.Match[str]) -> str:
        prefix = _normalize(value[: match.start()])
        has_positive_listing = _has_product_reference(prefix) and bool(
            re.search(
                r"r\$|\b(?:bateria|bat|\d+\s*(?:gb|tb)|disponivel|"
                r"disponibilidade|encontrei|temos|tenho)\b",
                prefix,
            )
        )
        if not has_positive_listing:
            return match.group(0)
        suffix = positive_offer_suffix(match.group(0))
        return f" {suffix}" if suffix else " "

    return pattern.sub(remove_if_following_listing, value)


def _is_standalone_photo_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    normalized = _normalize(text)
    photo_confirmation = is_photo_offer_confirmation(text, history)
    if normalized not in {"foto", "fotos", "imagem", "imagens"} and not photo_confirmation:
        return False
    if not history:
        return False
    query = _product_context_query(text, history)
    has_product_context = _has_product_reference(_normalize(query))
    has_photo_offer = photo_confirmation or any(
        entry.get("role") == "assistant"
        and any(word in _normalize(entry.get("content", "")) for word in ("foto", "imagem"))
        for entry in history
    )
    return has_product_context and has_photo_offer


def _has_photo_request_in_history(
    history: list[dict[str, str]] | None,
    *,
    current_text: str | None = None,
) -> bool:
    """Return whether a photo request is still the active customer intent."""
    current_families = set(_catalog_families(current_text or ""))
    catalog_request = re.compile(
        r"\b(?:tem|teria|teriam|temos|valor|valores|preco|precos|precinhos?|"
        r"quanto|custa|disponivel|disponibilidade|estoque|possui|previsao)\b"
    )
    condition_only = {
        "lacrado",
        "lacrada",
        "novo",
        "nova",
        "seminovo",
        "seminova",
        "semi novo",
        "semi nova",
        "usado",
        "usada",
    }

    for entry in reversed(history or []):
        if entry.get("role") != "user":
            continue
        content = entry.get("content", "").strip()
        if not content:
            continue
        if _is_photo_request(content):
            photo_families = set(_catalog_families(content))
            return not bool(
                current_families
                and photo_families
                and current_families.isdisjoint(photo_families)
            )

        normalized = _normalize(content)
        if normalized in condition_only:
            continue
        if catalog_request.search(normalized):
            return False
    return False


def _is_photo_context_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Keep a previous photo request active while the customer clarifies it."""
    if not _has_photo_request_in_history(history, current_text=text):
        return False
    normalized = _normalize(text)
    if not normalized:
        return False
    if _has_installment_model_prompt(history) and (
        re.search(r"\b(?:simul\w*|sublim\w*|calcul\w*|parcel\w*)\b", normalized)
        or re.search(r"\b(?:1[0-8]|[1-9])\s*(?:x|vezes)\b", normalized)
    ):
        return False
    if normalized in {
        "usado",
        "usada",
        "seminovo",
        "seminova",
        "semi novo",
        "semi nova",
    }:
        return True
    if _requested_capacity_keys(text):
        return True
    if re.fullmatch(
        r"(?:eu\s+)?vi\s+que\s+(?:voce|voces|vc)\s+tem",
        normalized,
    ) or re.fullmatch(
        r"(?:eu\s+)?vi\s+(?:ali\s+)?que\s+tem\s+(?:um\s+)?"
        r"(?:iphone\s*)?\d{1,2}"
        r"(?:\s+(?:pro\s+max|pro|max|plus|mini|air|e))?"
        r"(?:\s+[a-z]+)?",
        normalized,
    ):
        return True
    if _requested_iphone_model_keys(text):
        # A customer can extend a photo request with another explicit model
        # without repeating "foto", e.g. "e do 14 pro max e do 15 pro max".
        # Keep availability/price questions on their normal route instead of
        # treating them as a request to send the previous photo context.
        return not bool(
            re.search(
                r"\b(?:tem|teria|disponivel|disponibilidade|estoque|vende|"
                r"vender|possui|valor|valores|preco|precos|custa|quanto)\b",
                normalized,
            )
        )
    return False


def _is_capacity_availability_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Recognize a stock question that follows a previous photo exchange."""
    if not _is_photo_context_followup(text, history):
        return False
    normalized = _normalize(text)
    if not _requested_capacity_keys(text) or any(
        marker in normalized for marker in ("foto", "imagem")
    ):
        return False
    return bool(
        re.search(
            r"\b(?:tem|teria|disponivel|disponibilidade|estoque|vende|vender|possui)\b",
            normalized,
        )
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


def _sort_product_availability_items(items: list[Any]) -> list[Any]:
    """Keep availability sections intact and order each model's capacities ascending."""
    seminovos = sort_capacity_variants(item for item in items if not _is_sealed_item(item))
    pronta_entrega = sort_capacity_variants(
        item
        for item in items
        if _is_sealed_item(item) and not _is_made_to_order_sealed_item(item)
    )
    por_encomenda = sort_capacity_variants(
        item for item in items if _is_made_to_order_sealed_item(item)
    )
    return [*seminovos, *pronta_entrega, *por_encomenda]


def _format_product_availability(
    items: list[Any],
    *,
    header: str | None = None,
    installment_budget: tuple[int, float] | None = None,
    installment_quotes: dict[int, dict[str, Any]] | None = None,
    entry_amount: float | None = None,
) -> str:
    if not items:
        return "No momento não localizei esse produto no catálogo."
    items = _sort_product_availability_items(items)

    model_names = [
        str(getattr(item, "name", None) or "").strip()
        for item in items
        if str(getattr(item, "name", None) or "").strip()
    ]
    normalized_model_names = {_normalize(name) for name in model_names}
    if len(normalized_model_names) == 1 and model_names:
        model = model_names[0]
    elif model_names and all("iphone" in name for name in normalized_model_names):
        model = "iPhone"
    else:
        model = "produto"
    if header is not None:
        resolved_header = header
    elif installment_budget is not None:
        installment_count, monthly_limit = installment_budget
        entry_text = (
            f" e entrada de {format_brl(entry_amount)}"
            if entry_amount is not None
            else " sem entrada"
        )
        resolved_header = (
            f"Sim 😊 Encontrei estas opções que cabem em até "
            f"{installment_count}x de {format_brl(monthly_limit)}{entry_text}:"
        )
    else:
        resolved_header = f"Sim 😊 Encontrei estas opções de {model} disponíveis:"

    def item_line(item: Any) -> str:
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
        item_name = str(getattr(item, "name", None) or "produto").strip()
        line = f"• {item_name} — {color} — {capacity} — {condition} — {price_text} | Bat: {battery_text}"
        quote = (installment_quotes or {}).get(id(item))
        if quote:
            line += (
                f" — {quote['vezes']}x de "
                f"{format_brl(float(quote['valor_parcela_brl']))}"
            )
            if entry_amount is None:
                line += f" (total no cartão {format_brl(float(quote['valor_total_brl']))})"
            else:
                line += f" + entrada de {format_brl(entry_amount)}"
        return line

    groups = (
        ("📱 Seminovos disponíveis para pronta entrega:", [item for item in items if not _is_sealed_item(item)]),
        (
            "📦 Lacrados disponíveis para pronta entrega:",
            [
                item
                for item in items
                if _is_sealed_item(item) and not _is_made_to_order_sealed_item(item)
            ],
        ),
        ("📦 Novos lacrados por encomenda:", [item for item in items if _is_made_to_order_sealed_item(item)]),
    )
    populated_groups = [group for _heading, group in groups if group]
    if len(populated_groups) > 1 or groups[-1][1]:
        formatted_lines = [resolved_header]
        for heading, group in groups:
            if not group:
                continue
            formatted_lines.extend(["", heading])
            formatted_lines.extend(item_line(item) for item in group)
        if groups[-1][1]:
            formatted_lines.extend(
                [
                    "",
                    "Os lacrados são por encomenda, com prazo de 1 semana. Em qualquer envio, "
                    "inclusive por motoboy ou Sedex, o pagamento deve ser antecipado antes do "
                    "despacho; na retirada na loja, o pagamento é feito na hora.",
                ]
            )
        if installment_budget is not None:
            formatted_lines.extend(
                [
                    "",
                    "Parcelas calculadas para pagamento no cartão de crédito na máquina física.",
                ]
            )
        return "\n".join(formatted_lines)

    lines = [resolved_header]
    for item in items:
        lines.append(item_line(item))
    if installment_budget is not None:
        lines.extend(
            [
                "",
                "Parcelas calculadas para pagamento no cartão de crédito na máquina física.",
            ]
        )
    return "\n".join(lines)


def _unavailable_catalog_color_header(
    *,
    selected: list[Any],
    requested_models: tuple[tuple[int, str], ...],
    requested_ipad_models: tuple[str, ...],
    requested_families: set[str],
    requested_catalog_colors: tuple[str, ...],
    requested_capacities: set[str],
    has_available_requested_color: bool = False,
    requested_iphone_variant: str | None = None,
    requested_apple_watch_models: tuple[tuple[str, str, str | None], ...] = (),
) -> str:
    if requested_apple_watch_models:
        watch_labels = []
        for family, number, size in requested_apple_watch_models:
            family_label = {"series": "Series", "se": "SE", "ultra": "Ultra"}[family]
            label = f"Apple Watch {family_label} {number}"
            if size:
                label += f" {size} mm"
            watch_labels.append(label)
        model_name = " e ".join(watch_labels)
    elif len(requested_models) == 1:
        model_name = str(getattr(selected[0], "name", None) or "esse modelo").strip()
        model_name = model_name.title().replace("Iphone", "iPhone")
    elif len(requested_ipad_models) == 1:
        ipad_model = requested_ipad_models[0]
        model_name = f"iPad {ipad_model.title()}"
    elif not requested_models and requested_iphone_variant:
        model_name = f"iPhone {requested_iphone_variant.title()}"
    elif not requested_models and len(requested_families) == 1:
        family = next(iter(requested_families))
        model_name = {
            "iphone": "iPhone",
            "ipad": "iPad",
            "macbook": "MacBook",
            "apple_watch": "Apple Watch",
            "apple watch": "Apple Watch",
            "airpods": "AirPods",
        }.get(family, "aparelho")
    else:
        model_name = "aparelho"
    color_label = " e ".join(requested_catalog_colors)
    capacity_labels = [
        re.sub(
            r"(\d+(?:[.,]\d+)?)(gb|tb)",
            lambda match: f"{match.group(1)} {match.group(2).upper()}",
            capacity,
            flags=re.IGNORECASE,
        )
        for capacity in requested_capacities
    ]
    capacity_text = f" de {' e '.join(capacity_labels)}" if capacity_labels else ""
    if has_available_requested_color:
        if len(requested_catalog_colors) == 1:
            missing_color_text = f"na cor {color_label}"
        else:
            missing_color_text = f"nas cores {color_label}"
        return (
            f"Não localizei o {model_name} {missing_color_text}{capacity_text}. "
            "Encontrei estas opções nas outras cores solicitadas:"
        )
    if len(requested_catalog_colors) > 1:
        return (
            f"No momento, não encontrei o {model_name} nas cores {color_label}{capacity_text}. "
            "Encontrei estas opções em outras cores:"
        )
    return (
        f"No momento, o {model_name} {color_label}{capacity_text} não está disponível. "
        "Encontrei estas opções em outras cores:"
    )


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
    lacrados_pronta_entrega = result.get("lacrados_pronta_entrega") or []
    lacrados = result.get("lacrados") or []
    if seminovos:
        lines.extend(["", "📱 Seminovos disponíveis para venda:"])
        lines.extend(grouped_lines(seminovos))
    if lacrados_pronta_entrega:
        lines.extend(["", "📦 Lacrados disponíveis para pronta entrega:"])
        lines.extend(grouped_lines(lacrados_pronta_entrega))
    if lacrados:
        lines.extend(["", "📦 Novos lacrados por encomenda:"])
        lines.extend(grouped_lines(lacrados))
        lines.append(
            "\nOs lacrados são por encomenda, com prazo de 1 semana. Em qualquer envio, "
            "inclusive por motoboy ou Sedex, o pagamento deve ser antecipado antes do "
            "despacho; na retirada na loja, o pagamento é feito na hora."
        )
    if not seminovos and not lacrados_pronta_entrega and not lacrados:
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
        """Lista seminovos, lacrados a pronta entrega e lacrados por encomenda."""
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
        finder = getattr(cache, "find_product_photos", None)
        if callable(finder):
            try:
                selected = await finder(product_query)
            except Exception:
                selected = None
            if selected is None:
                return json.dumps({"encontrado": False, "produtos": []}, ensure_ascii=False)
            urls = list(getattr(selected, "photo_urls", []) or [])[:MAX_PRODUCT_PHOTOS]
            products = [
                {
                    "nome": selected.name,
                    "capacidade": selected.capacity,
                    "fotos": urls,
                }
            ] if urls else []
            return json.dumps({"encontrado": bool(products), "produtos": products}, ensure_ascii=False)

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
        if is_non_apple_trade_in_request(text) and not is_parts_buyback_request(text):
            return AgentDecision(reply=NON_APPLE_TRADE_IN_REPLY, confidence="high")
        image_availability_models = _explicit_image_availability_query(
            text,
            image_description,
        )
        if image_availability_models is not None:
            if image_availability_models:
                decision = await self._try_product_availability(
                    f"{text} {image_availability_models}",
                    history=history,
                )
                if decision is not None:
                    return protect_customer_decision(decision)
            return protect_customer_decision(
                AgentDecision(
                    reply=CATALOG_BUYER_DETAILS_REPLY,
                    handoff=True,
                    handoff_reason=CATALOG_BUYER_DETAILS_REASON,
                    confidence="high",
                )
            )
        if (
            image_description
            and _has_product_reference(_normalize(image_description))
            and _is_catalog_buyer_interest(text)
            and not trade_in_em_andamento(history)
        ):
            if (
                _is_payment_methods_question(text)
                and not _is_explicit_catalog_price_request(text)
            ):
                payment_methods_decision = self._try_payment_methods(text)
                if payment_methods_decision is not None:
                    return protect_customer_decision(payment_methods_decision)
            catalog_interest_decision = await self._try_product_availability(
                text,
                history=history,
                image_description=image_description,
                allow_buyer_interest_with_image=True,
            )
            if catalog_interest_decision is not None:
                return protect_customer_decision(catalog_interest_decision)
            return protect_customer_decision(
                AgentDecision(
                    reply=CATALOG_BUYER_DETAILS_REPLY,
                    handoff=True,
                    handoff_reason=CATALOG_BUYER_DETAILS_REASON,
                    confidence="high",
                )
            )

        combined_request = " ".join(part for part in (text, image_description) if part)
        if is_parts_buyback_request(combined_request):
            # Recent Apple catalog context can disambiguate a bare model number
            # and its battery details from a request to sell a loose battery.
            if (
                not is_purchase_without_trade_in_request(combined_request)
                and is_trade_in_context_request(combined_request, history)
            ):
                return AgentDecision(
                    reply=TRADE_IN_FORM,
                    handoff=True,
                    handoff_reason=TRADE_IN_REASON,
                    confidence="high",
                )
            return AgentDecision(reply=PARTS_BUYBACK_REPLY, confidence="high")
        if is_non_apple_trade_in_request(combined_request):
            return AgentDecision(reply=NON_APPLE_TRADE_IN_REPLY, confidence="high")
        # A purchase deadline can follow a store visit invitation in history.
        # Keep it in the buying flow unless the customer asks to schedule.
        if (
            is_contextual_generic_device_purchase_request(text, history)
            and not _is_visit_request(text)
            and not _is_reservation_request(text)
        ):
            return AgentDecision(
                reply=PURCHASE_WITHOUT_TRADE_IN_REPLY,
                confidence="high",
            )
        current_day_decision = self._try_current_day_information(text)
        if current_day_decision is not None:
            if _is_visit_request(text) or _is_reservation_request(text):
                visit_decision = self._try_visit_scheduling(text, history)
                if visit_decision is not None:
                    return protect_customer_decision(visit_decision)
            return protect_customer_decision(current_day_decision)
        catalog_price_recall_decision = await self._try_catalog_price_recall(combined_request)
        if catalog_price_recall_decision is not None:
            return protect_customer_decision(catalog_price_recall_decision)
        if _is_catalog_price_negotiation(combined_request, history):
            return protect_customer_decision(
                AgentDecision(
                    reply=CATALOG_PRICE_NEGOTIATION_REPLY,
                    handoff=True,
                    handoff_reason=CATALOG_PRICE_NEGOTIATION_REASON,
                    confidence="high",
                )
            )
        if (
            _is_contextual_boleto_followup(text, history)
            and not trade_in_em_andamento(history)
            and not is_trade_in_context_request(combined_request, history)
        ):
            return protect_customer_decision(
                AgentDecision(
                    reply=self.faq.get("pagamento") or PAYMENT_METHODS_REPLY,
                    confidence="high",
                )
            )
        if _is_catalog_purchase_advice_question(combined_request, history):
            return protect_customer_decision(
                AgentDecision(
                    reply=CATALOG_BUYER_DETAILS_REPLY,
                    handoff=True,
                    handoff_reason=CATALOG_BUYER_DETAILS_REASON,
                    confidence="high",
                )
            )
        if (
            _is_catalog_buyer_details_question(combined_request, history)
            and not _is_store_hours_request(combined_request)
            and not _is_full_installment_request(text)
            and not _is_specific_installment_request(text)
            and not (_is_warranty_request(text) and _is_payment_methods_question(text))
            and not (
                _is_delivery_or_pickup_request(combined_request, history)
                and not _has_catalog_condition_signal(combined_request)
            )
            and not trade_in_em_andamento(history)
            and not is_trade_in_context_request(combined_request, history)
        ):
            return protect_customer_decision(
                AgentDecision(
                    reply=CATALOG_BUYER_DETAILS_REPLY,
                    handoff=True,
                    handoff_reason=CATALOG_BUYER_DETAILS_REASON,
                    confidence="high",
                )
            )
        if (
            _is_device_condition_question(combined_request)
            and not trade_in_em_andamento(history)
            and not is_trade_in_context_request(combined_request, history)
        ):
            return protect_customer_decision(
                AgentDecision(
                    reply=CONDITION_HANDOFF_REPLY,
                    handoff=True,
                    handoff_reason=CONDITION_HANDOFF_REASON,
                    confidence="high",
                )
            )
        # OCR may append catalog copy that masks a clear offer in the customer's text.
        # A combined fallback is limited to explicit buyback cues in customer text,
        # so catalog descriptions cannot create trade-in intent on their own.
        text_trade_in_context = is_trade_in_context_request(text, history)
        combined_trade_in_context = is_trade_in_context_request(
            combined_request, history
        )
        if (
            (
                text_trade_in_context
                and not is_purchase_without_trade_in_request(text)
            )
            or (
                combined_trade_in_context
                and has_explicit_trade_in_cue(text)
                and not is_non_apple_trade_in_request(text)
                and not is_purchase_without_trade_in_request(text)
            )
        ):
            return AgentDecision(
                reply=TRADE_IN_FORM,
                handoff=True,
                handoff_reason=TRADE_IN_REASON,
                confidence="high",
            )
        if trade_in_em_andamento(history) and (
            is_completed_trade_in_form(text) or is_trade_in_negotiation(text)
        ):
            return AgentDecision(
                reply=TRADE_IN_NEGOTIATION_REPLY,
                handoff=True,
                handoff_reason=TRADE_IN_REASON,
                confidence="high",
            )

        if _is_technical_assistance_request(combined_request):
            return protect_customer_decision(
                AgentDecision(
                    reply=TECHNICAL_ASSISTANCE_REPLY,
                    handoff=True,
                    handoff_reason=TECHNICAL_ASSISTANCE_REASON,
                    confidence="high",
                )
            )

        if _is_unresolved_iphone_model_selection(combined_request):
            is_first_iphone = _is_affirmative_first_iphone_statement(combined_request)
            return AgentDecision(
                reply=(
                    "Que legal! 😊 Como é seu primeiro iPhone, posso te ajudar a escolher. "
                    "Você já tem algum modelo em mente ou quer me contar seu uso e faixa de valor?"
                    if is_first_iphone
                    else "Claro 😊 Qual modelo de iPhone você tem em mente? "
                    "Aí consulto as opções lacradas disponíveis para você."
                ),
                confidence="high",
            )

        if _is_installment_budget_options_request(text, history):
            installment_budget_decision = await self._try_product_availability(
                text,
                history=history,
                image_description=image_description,
            )
            if installment_budget_decision is not None:
                return protect_customer_decision(installment_budget_decision)

        installment_count_decision = await self._try_generic_installment_count_question(
            text,
            history,
            image_description=image_description,
        )
        if installment_count_decision is not None:
            return protect_customer_decision(installment_count_decision)

        combined_catalog_installment_decision = await self._try_combined_catalog_installment(
            text,
            history,
            image_description=image_description,
        )
        if combined_catalog_installment_decision is not None:
            return protect_customer_decision(combined_catalog_installment_decision)

        payment_link_decision = await self._try_payment_link(text, history)
        if payment_link_decision is not None:
            return protect_customer_decision(payment_link_decision)

        price_policy_decision = self._try_price_policy(text)
        if price_policy_decision is not None:
            return protect_customer_decision(price_policy_decision)

        combined_warranty_payment_decision = await self._try_combined_warranty_payment(
            text,
            history,
        )
        if combined_warranty_payment_decision is not None:
            return protect_customer_decision(combined_warranty_payment_decision)

        boleto_installment_decision = self._try_boleto_installment(text)
        if boleto_installment_decision is not None:
            return protect_customer_decision(boleto_installment_decision)

        delivery_payment_decision = self._try_delivery_payment(text, history)
        if delivery_payment_decision is not None:
            return protect_customer_decision(delivery_payment_decision)

        combined_catalog_payment_decision = await self._try_combined_catalog_payment_methods(
            text,
            history,
        )
        if combined_catalog_payment_decision is not None:
            return protect_customer_decision(combined_catalog_payment_decision)

        payment_methods_decision = self._try_payment_methods(text)
        if payment_methods_decision is not None:
            return protect_customer_decision(payment_methods_decision)

        delivery_pickup_decision = self._try_delivery_or_pickup(text, history)
        if delivery_pickup_decision is not None:
            return protect_customer_decision(delivery_pickup_decision)

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

        store_hours_decision = self._try_store_hours(text)
        if store_hours_decision is not None:
            return protect_customer_decision(store_hours_decision)

        installment_rate_decision = self._try_installment_rate_question(text, history=history)
        if installment_rate_decision is not None:
            return protect_customer_decision(installment_rate_decision)

        accessory_inclusion_decision = self._try_accessory_inclusion_information(text, history)
        if accessory_inclusion_decision is not None:
            return protect_customer_decision(accessory_inclusion_decision)

        rate_followup_decision = await self._try_rate_model_followup(text, history)
        if rate_followup_decision is not None:
            return protect_customer_decision(rate_followup_decision)

        model_installment_decision = await self._try_model_installment_followup(text, history)
        if model_installment_decision is not None:
            return protect_customer_decision(model_installment_decision)

        battery_installment_selection = await self._try_battery_installment_selection(
            text,
            history,
        )
        if battery_installment_selection is not None:
            return protect_customer_decision(battery_installment_selection)

        case_accessory_decision = self._try_case_accessory_information(text)
        if case_accessory_decision is not None:
            return protect_customer_decision(case_accessory_decision)

        if _is_catalog_budget_objection(text, history):
            return AgentDecision(
                reply=CATALOG_BUDGET_OBJECTION_REPLY,
                confidence="high",
            )

        catalog_product_decision = await self._try_catalog_product_reference(
            text,
            image_description=image_description,
        )
        if catalog_product_decision is not None:
            return protect_customer_decision(catalog_product_decision)

        battery_detail_decision = await self._try_battery_detail(text, history)
        if battery_detail_decision is not None:
            return protect_customer_decision(battery_detail_decision)

        availability_decision = await self._try_available_products(text)
        if availability_decision is not None:
            return protect_customer_decision(availability_decision)

        product_availability_decision = await self._try_product_availability(
            text,
            history=history,
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

        full_installment_decision = await self._try_full_installment_table(
            text,
            history,
            image_description=image_description,
        )
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
            decision = _ensure_trade_in_form_before_handoff(
                decision,
                text,
                history,
                image_description=image_description,
            )
            return protect_customer_decision(self._sanitize_image_urls(decision))
        except Exception as exc:
            return AgentDecision(
                reply="Vou encaminhar sua mensagem para um atendente confirmar essa informação.",
                handoff=True,
                handoff_reason=f"Falha no agente: {type(exc).__name__}",
                confidence="low",
            )

    def _try_installment_rate_question(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
    ) -> AgentDecision | None:
        if not _is_installment_rate_question(text):
            return None
        if (
            _is_installment_fee_inclusion_question(text)
            and _has_recent_card_installment_simulation(history)
        ):
            return AgentDecision(
                reply=(
                    "Sim. Os valores parcelados da simulação já incluem a taxa de juros da "
                    "máquina. O preço à vista aparece separado, e o total varia conforme "
                    "a quantidade de parcelas."
                ),
                confidence="high",
            )
        return AgentDecision(
            reply=format_installment_rates(
                ask_for_model=not _is_no_interest_installment_question(text)
            ),
            confidence="high",
        )

    async def _try_generic_installment_count_question(
        self,
        text: str,
        history: list[dict[str, str]] | None,
        *,
        image_description: str | None = None,
    ) -> AgentDecision | None:
        if not _is_generic_installment_count_question(text):
            return None

        if _is_payment_link_followup(text, history):
            return await self._try_payment_link(text, history)

        product_context = "\n".join(
            part for part in (text, image_description) if part and part.strip()
        )
        contextual_query = _installment_context_query(product_context, history)
        requested_families = _catalog_families(contextual_query)
        single_product_key = _requested_installment_product_keys(contextual_query)
        family_has_model_key_without_capacity = requested_families in (
            ("macbook",),
            ("airpods",),
            ("apple_watch",),
        )
        has_single_product = (
            len(single_product_key) == 1
            and (
                len(_requested_capacity_keys(contextual_query)) == 1
                or (
                    requested_families == ("iphone",)
                    and _has_recent_specific_installment_selection(history)
                )
                or family_has_model_key_without_capacity
            )
        )
        if has_single_product:
            installment = await self._try_full_installment_table(
                text,
                history,
                image_description=image_description,
            )
            if installment is not None:
                return installment

        return AgentDecision(reply=PAYMENT_ONLY_CREDIT_REPLY, confidence="high")

    async def _try_rate_model_followup(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        if not _is_rate_model_followup(text, history):
            return None
        specific = await self._try_specific_installment(text, history)
        if specific is not None:
            return specific
        return await self._try_full_installment_table(text, history)

    async def _try_model_installment_followup(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        if not _is_installment_model_followup(text, history):
            return None
        return await self._try_full_installment_table(text, history)

    async def _try_battery_installment_selection(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        if not history or history[-1].get("role") != "assistant":
            return None

        offer = _normalize(history[-1].get("content", ""))
        asks_which_unit = bool(
            re.search(r"\b(?:qual\s+delas|qual\s+das\s+duas|qual\s+unidade)\b", offer)
        )
        asks_to_simulate = bool(re.search(r"\b(?:simul\w*|parcel\w*)\b", offer))
        if not asks_which_unit or not asks_to_simulate:
            return None

        query = _catalog_battery_unit_selection_query(text, history)
        if not query:
            return None

        method = getattr(self.cache, "simulate_all_installments", None)
        if not callable(method):
            return None
        try:
            result = await method(query)
        except Exception:
            return None
        if result.get("encontrado"):
            return AgentDecision(
                reply=format_installment_table(result),
                confidence="high",
            )
        if result.get("ambiguo"):
            return _format_ambiguous_installment_decision(result)

        models = _requested_iphone_model_keys(query)
        if models:
            number, variant = models[0]
            model = f"iPhone {number}" + (f" {variant.title()}" if variant else "")
        else:
            model = "essa unidade"
        battery = _requested_battery_health(query)
        battery_label = f" com bateria {battery:g}%" if battery is not None else ""
        return AgentDecision(
            reply=(
                f"Não encontrei preço confirmado para simular o {model}{battery_label} "
                "no momento."
            ),
            confidence="medium",
        )

    async def _try_payment_link(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        if not (_is_payment_link_request(text) or _is_payment_link_followup(text, history)):
            return None
        reply = self.faq.get("link_pagamento") or PAYMENT_LINK_REPLY
        return AgentDecision(
            reply=self._append_delivery_or_pickup_info(reply, text),
            confidence="high",
        )

    def _try_boleto_installment(self, text: str) -> AgentDecision | None:
        if not _is_boleto_payment_request(text):
            return None
        normalized = _normalize(text)
        has_installment_question = _requested_installments(text) is not None or bool(
            re.search(r"\bparcel\w*\b", normalized)
        )
        reply = BOLETO_INSTALLMENT_REPLY
        if not has_installment_question:
            reply = self.faq.get("pagamento") or BOLETO_INSTALLMENT_REPLY
        return AgentDecision(
            reply=self._append_delivery_or_pickup_info(reply, text),
            confidence="high",
        )

    def _try_payment_methods(self, text: str) -> AgentDecision | None:
        if not _is_payment_methods_question(text):
            return None

        if _is_credit_only_installment_question(text):
            reply = PAYMENT_ONLY_CREDIT_REPLY
        else:
            reply = self.faq.get("pagamento") or PAYMENT_METHODS_REPLY
        normalized = _normalize(text)
        if re.search(
            r"\b(?:dois|duas|2)\s+cartoes?(?:\s+de\s+credito)?\b",
            normalized,
        ) or "mais de um cartao" in normalized:
            reply = (
                "Sim 😊 Você pode usar dois cartões de crédito na mesma compra e completar o valor "
                f"com PIX, dinheiro ou cartão de débito. {reply}"
            )
        return AgentDecision(
            reply=self._append_delivery_or_pickup_info(reply, text),
            confidence="high",
        )

    async def _try_combined_catalog_payment_methods(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        if (
            not _is_explicit_catalog_price_request(text)
            or not _is_independent_payment_methods_request(text)
            or not _is_payment_methods_question(text)
        ):
            return None

        catalog_decision = await self._try_product_availability(text, history=history)
        payment_decision = self._try_payment_methods(text)
        if catalog_decision is None or payment_decision is None:
            return None
        if catalog_decision.handoff:
            return catalog_decision

        return AgentDecision(
            reply=f"{catalog_decision.reply.rstrip()}\n\n{payment_decision.reply.strip()}",
            product_references=catalog_decision.product_references,
            image_urls=catalog_decision.image_urls,
            confidence="high",
        )

    async def _try_combined_warranty_payment(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        payment_decision = self._try_payment_methods(text)
        if payment_decision is None:
            payment_decision = self._try_boleto_installment(text)
        if payment_decision is None:
            return None
        warranty_decision = await self._try_warranty(text, history)
        if warranty_decision is None:
            return None
        return AgentDecision(
            reply=f"{warranty_decision.reply.rstrip()} {payment_decision.reply.lstrip()}",
            confidence="high",
        )

    def _try_price_policy(self, text: str) -> AgentDecision | None:
        if _is_cash_discount_question(text):
            reply = self.faq.get("desconto_pix") or (
                "Não há desconto no PIX; PIX, dinheiro e cartão de débito têm "
                "pagamento integral à vista, sem taxas."
            )
        elif _is_price_validity_question(text):
            reply = self.faq.get("preco") or (
                "Os preços podem ser alterados sem aviso prévio. A confirmação "
                "deve ser feita no momento do atendimento."
            )
        else:
            return None
        return AgentDecision(
            reply=self._append_delivery_or_pickup_info(reply, text),
            confidence="high",
        )

    def _append_delivery_or_pickup_info(self, reply: str, text: str) -> str:
        if not _is_delivery_or_pickup_request(text):
            return reply
        information = _delivery_or_pickup_reply(self.faq, text)
        if not information:
            return reply
        return f"{reply}\n\n{information}"

    def _try_delivery_payment(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
    ) -> AgentDecision | None:
        if _is_delivery_quote_followup(text, history):
            return AgentDecision(
                reply=(
                    "Obrigado! Vou encaminhar seu endereço para um atendente confirmar "
                    "a taxa e o prazo da entrega e orientar como concluir o pagamento antes do envio."
                ),
                handoff=True,
                handoff_reason=DELIVERY_FEE_HANDOFF_REASON,
                confidence="high",
            )

        delivery_fee_request = _is_delivery_fee_request(text, history)
        acknowledge_cannot_visit = (
            _is_visit_unavailability_message(text) and _has_delivery_context(history)
        )
        if not acknowledge_cannot_visit and not _is_delivery_payment_question(text):
            return None
        return AgentDecision(
            reply=_delivery_payment_reply(
                self.faq,
                acknowledge_cannot_visit=acknowledge_cannot_visit,
            ),
            handoff=delivery_fee_request,
            handoff_reason=DELIVERY_FEE_HANDOFF_REASON if delivery_fee_request else None,
            confidence="high",
        )

    def _try_delivery_or_pickup(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
    ) -> AgentDecision | None:
        delivery_fee_request = _is_delivery_fee_request(text, history)
        delivery_followup = _is_delivery_followup_request(text, history)
        pickup_schedule_request = _is_pickup_schedule_request(text)
        if (
            not (
                _is_delivery_or_pickup_request(text, history)
                or delivery_followup
                or delivery_fee_request
                or pickup_schedule_request
            )
            or _is_explicit_human_request(text)
            or _is_physical_store_request(text)
        ):
            return None
        if delivery_fee_request:
            return AgentDecision(
                reply=DELIVERY_FEE_HANDOFF_REPLY,
                handoff=True,
                handoff_reason=DELIVERY_FEE_HANDOFF_REASON,
                confidence="high",
            )
        if pickup_schedule_request:
            return AgentDecision(
                reply=_pickup_schedule_reply(self.faq, text),
                confidence="high",
            )
        reply = _delivery_or_pickup_reply(self.faq, text, force_delivery=delivery_followup)
        if not reply:
            return None
        return AgentDecision(reply=reply, confidence="high")

    async def _try_available_products(self, text: str) -> AgentDecision | None:
        variant_list_request = _is_variant_catalog_list_request(text)
        sealed_iphone_only = _is_sealed_iphone_list_request(text)
        sealed_only = _is_sealed_catalog_list_request(text) or sealed_iphone_only
        if not (_is_available_list_request(text) or sealed_only or sealed_iphone_only):
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
        if sealed_only:
            lacrados_pronta_entrega = result.get("lacrados_pronta_entrega") or []
            lacrados = result.get("lacrados") or []
            if not lacrados_pronta_entrega and not lacrados:
                capacity_no_match = _capacity_list_no_match_reply(text, sealed_only=True)
                if capacity_no_match:
                    return AgentDecision(reply=capacity_no_match, confidence="medium")
                if _is_new_model_list_request(text):
                    return AgentDecision(
                        reply=(
                            "No momento, não localizei modelos novos/lacrados disponíveis no catálogo. "
                            "Se quiser, posso verificar um modelo específico ou outra capacidade."
                        ),
                        confidence="medium",
                    )
                return None
            result = {
                "encontrado": True,
                "seminovos": [],
                "lacrados_pronta_entrega": lacrados_pronta_entrega,
                "lacrados": lacrados,
            }
        if _is_pronta_entrega_only_request(text):
            result = {**result, "lacrados": []}
        if variant_list_request:
            normalized = _normalize(text)

            def is_requested_variant(entry: dict[str, Any]) -> bool:
                model = _model_key(entry.get("nome", ""))
                return model is not None and model[1] == "pro max"

            result = {
                **result,
                "seminovos": [
                    entry for entry in result.get("seminovos", []) if is_requested_variant(entry)
                ],
                "lacrados_pronta_entrega": [
                    entry
                    for entry in result.get("lacrados_pronta_entrega", [])
                    if is_requested_variant(entry)
                ],
                "lacrados": [
                    entry for entry in result.get("lacrados", []) if is_requested_variant(entry)
                ],
            }
            if _has_seminovo_reference(normalized) and not _has_sealed_reference(normalized):
                result["lacrados_pronta_entrega"] = []
                result["lacrados"] = []
            elif _has_sealed_reference(normalized) and not _has_seminovo_reference(normalized):
                result["seminovos"] = []
        if _is_generic_iphone_list_request(text) or sealed_iphone_only:
            normalized = _normalize(text)
            other_family_requested = bool(
                re.search(r"\b(?:ipads?|mac(?:books?)?|airpods?|apple\s+watch)\b", normalized)
            )
            if not other_family_requested:
                def is_iphone_entry(entry: dict[str, Any]) -> bool:
                    entry_text = " ".join(
                        str(entry.get(field) or "")
                        for field in ("nome", "capacidade", "cor", "condicao")
                    )
                    return _catalog_family(entry_text) == "iphone"

                result = {
                    **result,
                    "seminovos": [
                        entry for entry in result.get("seminovos", []) if is_iphone_entry(entry)
                    ],
                    "lacrados_pronta_entrega": [
                        entry
                        for entry in result.get("lacrados_pronta_entrega", [])
                        if is_iphone_entry(entry)
                    ],
                    "lacrados": [
                        entry for entry in result.get("lacrados", []) if is_iphone_entry(entry)
                    ],
                }
        requested_capacity_keys = _requested_capacity_keys(text)
        requested_capacities = set(requested_capacity_keys)
        if requested_capacities:
            def has_requested_capacity(entry: dict[str, Any]) -> bool:
                capacities = _requested_capacity_keys(
                    str(entry.get("capacidade") or entry.get("nome") or "")
                )
                return bool(requested_capacities.intersection(capacities))

            result = {
                **result,
                "seminovos": [
                    entry for entry in result.get("seminovos", []) if has_requested_capacity(entry)
                ],
                "lacrados_pronta_entrega": [
                    entry
                    for entry in result.get("lacrados_pronta_entrega", [])
                    if has_requested_capacity(entry)
                ],
                "lacrados": [
                    entry for entry in result.get("lacrados", []) if has_requested_capacity(entry)
                ],
            }
            is_category_iphone_list = (
                _is_generic_iphone_list_request(text) or sealed_iphone_only
            )
            if is_category_iphone_list and not any(
                result.get(key)
                for key in ("seminovos", "lacrados_pronta_entrega", "lacrados")
            ):
                capacity_no_match = _capacity_list_no_match_reply(text, sealed_only=sealed_only)
                if capacity_no_match:
                    return AgentDecision(reply=capacity_no_match, confidence="medium")
        return AgentDecision(reply=_format_available_products(result), confidence="high")

    def _try_case_accessory_information(self, text: str) -> AgentDecision | None:
        if not _is_case_accessory_request(text):
            return None
        return AgentDecision(
            reply=self.faq.get("capinhas") or CASE_ACCESSORY_REPLY,
            confidence="high",
        )

    def _try_accessory_inclusion_information(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
    ) -> AgentDecision | None:
        if not _is_accessory_inclusion_question(text):
            return None

        normalized = _normalize(text)
        if not _has_sealed_reference(normalized) and not _has_seminovo_reference(normalized):
            for entry in reversed(history or []):
                if entry.get("role") != "assistant":
                    continue
                context = _normalize(entry.get("content", ""))
                if not _has_product_reference(context) or not re.search(r"\br\$\s*\d", context):
                    continue
                has_sealed_condition = _has_sealed_reference(context)
                has_seminovo_condition = _has_seminovo_reference(context)
                if has_sealed_condition != has_seminovo_condition:
                    normalized = context
                    break

        if _has_sealed_reference(normalized) and not _has_seminovo_reference(normalized):
            reply = self.faq.get("lacrados")
        elif _has_seminovo_reference(normalized) and not _has_sealed_reference(normalized):
            reply = self.faq.get("seminovos")
        else:
            reply = self.faq.get("acessórios")
        return AgentDecision(
            reply=reply
            or (
                "Aparelhos seminovos acompanham cabo e fonte novos, homologados pela Anatel. "
                "Aparelhos novos lacrados acompanham apenas o cabo original que já vem dentro da caixa."
            ),
            confidence="high",
        )

    async def _try_battery_detail(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        if not _is_battery_detail_request(text) or _is_product_availability_request(
            text,
            history=history,
        ):
            return None
        query = _battery_detail_context_query(text, history)
        if not query:
            return None
        try:
            candidates = await self.cache.search(query, limit=300)
        except Exception:
            return None

        candidates = [
            item
            for item in candidates
            if _is_device_item(item)
            and (getattr(item, "source", None) != "mercado_phone" or _is_available_item(item))
            and not _is_sealed_item(item)
        ]
        requested_health = _requested_battery_health(query)
        if requested_health is not None:
            candidates = [
                item
                for item in candidates
                if _requested_battery_health(
                    f"bateria {getattr(item, 'battery_health', '')}%"
                )
                == requested_health
            ]
        if not candidates:
            alternative = await self._try_unavailable_seminew_alternative(
                query,
                requested_label=(
                    _extract_bare_catalog_model_reference(text)
                    or _extract_bare_catalog_model_reference(query)
                ),
            )
            if alternative is not None:
                return alternative
            requested_label = (
                _extract_bare_catalog_model_reference(text)
                or _extract_bare_catalog_model_reference(query)
                or "esse aparelho"
            )
            return AgentDecision(
                reply=(
                    f"Não localizei o {requested_label} disponível no estoque no momento. "
                    "Não consigo confirmar a saúde da bateria sem uma unidade cadastrada."
                ),
                confidence="medium",
            )

        if requested_health is not None:
            best_score = max(_catalog_score(query, item) for item in candidates)
            selected_candidates = [
                item for item in candidates if _catalog_score(query, item) == best_score
            ]
        else:
            selected = max(
                candidates,
                key=lambda item: (
                    _catalog_score(query, item),
                    _normalize(str(getattr(item, "name", "") or "")),
                    _normalize(str(getattr(item, "capacity", "") or "")),
                ),
            )
            selected_candidates = [selected]
        selected = selected_candidates[0]
        label_parts = [str(getattr(selected, "name", "produto") or "produto")]
        for value in (
            getattr(selected, "capacity", None),
            getattr(selected, "color", None) or getattr(selected, "colors", None),
        ):
            if value and str(value).strip() not in label_parts:
                label_parts.append(str(value).strip())
        label = " ".join(label_parts)
        battery = getattr(selected, "battery_health", None)
        if battery is None:
            battery_reply = "A saúde da bateria não está informada no cadastro."
        else:
            battery_reply = f"A saúde cadastrada da bateria é {_format_battery(battery)}."
        if len(selected_candidates) > 1 and requested_health is not None:
            label = (
                f"as {len(selected_candidates)} opções de {label} com bateria "
                f"{_format_battery(requested_health)}"
            )
            originality_reply = (
                "O cadastro não informa se as baterias são originais ou se já foram "
                "trocadas, então não consigo confirmar essa parte por aqui."
            )
            battery_reply = ""
        else:
            originality_reply = (
                "O cadastro não informa se a bateria é original ou se já foi trocada, "
                "então não consigo confirmar essa parte por aqui."
            )
        return AgentDecision(
            reply=(
                f"Sobre {label}: "
                f"{(battery_reply + ' ') if battery_reply else ''}{originality_reply}"
            ),
            product_references=[
                str(getattr(item, "external_id", "")) for item in selected_candidates
            ],
            confidence="high",
        )

    async def _try_catalog_product_reference(
        self,
        text: str,
        *,
        image_description: str | None = None,
    ) -> AgentDecision | None:
        product_id = _extract_catalog_product_id(_current_catalog_context(text, image_description))
        if product_id is None:
            return None

        method = getattr(self.cache, "get", None)
        if not callable(method):
            return AgentDecision(
                reply="Vou encaminhar esse link do catálogo para um atendente confirmar o aparelho.",
                handoff=True,
                handoff_reason="Consulta do produto do catálogo indisponível",
                confidence="low",
            )

        try:
            item = await method(product_id)
        except Exception:
            return AgentDecision(
                reply="Não consegui consultar esse aparelho agora. Vou encaminhar sua mensagem para um atendente confirmar.",
                handoff=True,
                handoff_reason="Falha ao consultar produto do catálogo",
                confidence="low",
            )

        if item is None:
            return AgentDecision(
                reply="Não localizei o aparelho indicado por esse link do catálogo. Pode confirmar o link ou o código do estoque?",
                confidence="medium",
            )

        return AgentDecision(
            reply=_format_product_availability([item]),
            product_references=[product_id],
            confidence="high",
        )

    async def _try_unavailable_lacrado_alternative(
        self,
        query: str,
        *,
        requested_budget: float | None = None,
        condition_query: str | None = None,
    ) -> AgentDecision | None:
        normalized_query = _normalize(condition_query if condition_query is not None else query)
        if not _has_explicit_sealed_condition(normalized_query):
            return None

        method = getattr(self.cache, "list_available_products", None)
        if not callable(method):
            return None
        try:
            result = await method()
        except Exception:
            return None

        seminovos = result.get("seminovos") or []
        lacrados_pronta_entrega = result.get("lacrados_pronta_entrega") or []
        lacrados = result.get("lacrados") or []
        requested_family = _catalog_family(query)
        requested_models = _requested_iphone_model_keys(query)

        def entry_text(entry: dict[str, Any]) -> str:
            return " ".join(
                str(entry.get(field) or "")
                for field in ("nome", "capacidade", "cor", "condicao")
            )

        def same_family(entry: dict[str, Any]) -> bool:
            return requested_family is None or _catalog_family(entry_text(entry)) == requested_family

        def same_requested_model(entry: dict[str, Any]) -> bool:
            if not requested_models or not same_family(entry):
                return False
            return _catalog_family(entry_text(entry)) == "iphone" and _model_key(
                entry.get("nome", "")
            ) in requested_models

        def within_budget(entry: dict[str, Any]) -> bool:
            if requested_budget is None:
                return True
            prices = entry.get("precos_brl") or []
            if not isinstance(prices, (list, tuple)):
                prices = [prices]
            for price in prices:
                try:
                    if float(price) <= requested_budget:
                        return True
                except (TypeError, ValueError):
                    continue
            return False

        available_seminovos = [entry for entry in seminovos if same_family(entry) and within_budget(entry)]
        matching_seminovos = [entry for entry in available_seminovos if same_requested_model(entry)]
        other_seminovos = [entry for entry in available_seminovos if not same_requested_model(entry)]
        available_lacrados_pronta_entrega = [
            entry
            for entry in lacrados_pronta_entrega
            if same_family(entry) and within_budget(entry)
        ]
        matching_lacrados_pronta_entrega = [
            entry
            for entry in available_lacrados_pronta_entrega
            if same_requested_model(entry)
        ]
        other_lacrados_pronta_entrega = [
            entry
            for entry in available_lacrados_pronta_entrega
            if not same_requested_model(entry)
        ]
        available_lacrados = [entry for entry in lacrados if same_family(entry) and within_budget(entry)]
        matching_lacrados = [entry for entry in available_lacrados if same_requested_model(entry)]
        other_lacrados = [
            entry for entry in available_lacrados if not same_requested_model(entry)
        ]
        alternatives = {
            "seminovos": [*matching_seminovos, *other_seminovos],
            "lacrados_pronta_entrega": [
                *matching_lacrados_pronta_entrega,
                *other_lacrados_pronta_entrega,
            ],
            "lacrados": [*matching_lacrados, *other_lacrados],
        }
        if not any(alternatives.values()):
            return AgentDecision(
                reply=(
                    "N\u00e3o localizei esse modelo novo/lacrado nem na lista de lacrados por encomenda "
                    "nem entre os lacrados dispon\u00edveis no estoque. "
                    "Tamb\u00e9m n\u00e3o encontrei outra op\u00e7\u00e3o cadastrada para sugerir agora. "
                    "Se quiser, me diga outro modelo ou capacidade."
                ),
                confidence="medium",
            )

        return AgentDecision(
            reply=(
                "N\u00e3o localizei esse modelo novo/lacrado nem na lista de lacrados por encomenda "
                "nem entre os lacrados dispon\u00edveis no estoque. "
                "Para voc\u00ea escolher outra op\u00e7\u00e3o, seguem alternativas cadastradas:\n\n"
                + _format_available_products(alternatives)
            ),
            confidence="high",
        )


    async def _try_unavailable_seminew_alternative(
        self,
        query: str,
        *,
        requested_label: str | None = None,
    ) -> AgentDecision | None:
        if not _has_installment_product_context(query):
            return None

        if _has_explicit_sealed_condition(_normalize(query)):
            return None

        method = getattr(self.cache, "list_available_products", None)
        if not callable(method):
            return None
        try:
            result = await method()
        except Exception:
            return None

        requested_families = _catalog_families(query)
        if any(family in {"ipad", "macbook"} for family in requested_families):
            family_labels = {
                "iphone": ("iPhone", "iPhones"),
                "ipad": ("iPad", "iPads"),
                "macbook": ("MacBook", "MacBooks"),
                "airpods": ("AirPods", "AirPods"),
                "apple_watch": ("Apple Watch", "Apple Watches"),
            }
            single_family = len(requested_families) == 1
            family_name, family_plural = (
                family_labels[requested_families[0]] if single_family else (None, None)
            )

            def is_requested_family_entry(entry: dict[str, Any]) -> bool:
                return _catalog_family(str(entry.get("nome") or "")) in requested_families

            family_options = {
                "seminovos": [
                    entry
                    for entry in result.get("seminovos") or []
                    if is_requested_family_entry(entry)
                ],
                "lacrados_pronta_entrega": [
                    entry
                    for entry in result.get("lacrados_pronta_entrega") or []
                    if is_requested_family_entry(entry)
                ],
                "lacrados": [
                    entry
                    for entry in result.get("lacrados") or []
                    if is_requested_family_entry(entry)
                ],
            }
            new_family_available = any(
                family_options[section]
                for section in ("lacrados_pronta_entrega", "lacrados")
            )
            if not any(family_options.values()):
                if single_family:
                    reply = (
                        f"No momento, não localizei opções de {family_name} seminovo disponíveis no sistema. "
                        f"Também não encontrei {family_plural} novos disponíveis no catálogo. "
                        "Se quiser, posso verificar outro modelo ou capacidade."
                    )
                else:
                    reply = (
                        "No momento, não localizei opções seminovas das famílias solicitadas no sistema. "
                        "Também não encontrei produtos novos dessas famílias no catálogo. "
                        "Se quiser, posso verificar outro modelo ou capacidade."
                    )
                return AgentDecision(
                    reply=reply,
                    confidence="medium",
                )

            formatted_family = _format_available_products(family_options).removeprefix(
                "📋 Lista completa de produtos disponíveis:\n"
            )
            if family_options["seminovos"]:
                intro = (
                    f"Não localizei a opção de {family_name} seminovo solicitada. "
                    f"Encontrei estes {family_plural} seminovos"
                    if single_family
                    else "Não localizei todas as opções seminovas solicitadas. "
                    "Encontrei estes produtos seminovos"
                )
                if new_family_available:
                    intro += " e novos disponíveis para você escolher:\n\n"
                else:
                    intro += " disponíveis para você escolher:\n\n"
            else:
                intro = (
                    f"No momento, não localizei opções de {family_name} seminovo disponíveis no sistema. "
                    f"Para você escolher, seguem as opções de {family_plural} novos disponíveis:\n\n"
                    if single_family
                    else "No momento, não localizei opções seminovas das famílias solicitadas. "
                    "Para você escolher, seguem as opções novas disponíveis:\n\n"
                )
            return AgentDecision(
                reply=intro + formatted_family,
                confidence="medium",
            )

        seminovos = result.get("seminovos") or []
        if not seminovos:
            return None
        unavailable_reply = (
            f"No momento, n\u00e3o localizei o {requested_label} dispon\u00edvel no estoque."
            if requested_label
            else "No momento, n\u00e3o localizei esse produto seminovo dispon\u00edvel no sistema."
        )
        return AgentDecision(
            reply=(
                unavailable_reply + " "
                "Algum outro modelo tamb\u00e9m interessaria? Para facilitar, segue a lista "
                "dos seminovos dispon\u00edveis para voc\u00ea escolher:\n\n"
                + _format_available_products({"seminovos": seminovos, "lacrados": []})
            ),
            confidence="medium",
        )

    async def _try_catalog_price_recall(self, text: str) -> AgentDecision | None:
        remembered_price = catalog_price_recall_amount(text)
        if remembered_price is None:
            return None

        try:
            candidates = await self.cache.search("iphone", limit=300)
        except Exception:
            return None

        priced_candidates: list[tuple[float, Any]] = []
        for item in candidates:
            if not _is_device_item(item):
                continue
            if getattr(item, "source", None) == "mercado_phone" and not _is_available_item(item):
                continue
            try:
                price = float(getattr(item, "price_brl", None))
            except (TypeError, ValueError):
                continue
            priced_candidates.append((price, item))

        if not priced_candidates:
            return None

        distance, selected = min(
            (
                (abs(price - remembered_price), item)
                for price, item in priced_candidates
            ),
            key=lambda pair: (
                pair[0],
                -_catalog_score("iphone", pair[1]),
                _normalize(str(getattr(pair[1], "name", "") or "")),
            ),
        )
        if distance > max(100.0, remembered_price * 0.10):
            return None

        reference = str(getattr(selected, "external_id", "") or "")
        return AgentDecision(
            reply=_format_product_availability([selected]),
            product_references=[reference] if reference else [],
            confidence="high",
        )

    async def _try_product_availability(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
        *,
        image_description: str | None = None,
        allow_buyer_interest_with_image: bool = False,
    ) -> AgentDecision | None:
        battery_unit_selection_query = _catalog_battery_unit_selection_query(
            text,
            history,
        )
        ipad_model_alternative_price_followup = _is_ipad_model_alternative_price_followup(
            text,
            history,
        )
        # A short clarification after a photo request is still a photo
        # selection, even when it contains availability wording such as
        # "tem um 11 verde". Let the photo resolver preserve that context.
        capacity_availability_followup = _is_capacity_availability_followup(text, history)
        if (
            (
                _is_photo_context_followup(text, history)
                or is_photo_offer_confirmation(text, history)
            )
            and not (
                _is_catalog_price_followup(text)
                and _has_recent_catalog_product_context(history)
            )
            and not capacity_availability_followup
            and battery_unit_selection_query is None
        ):
            return None
        seminovo_trade_in_query = _seminovo_trade_in_catalog_target_query(text)
        current_query = seminovo_trade_in_query or _current_catalog_context(
            text,
            image_description,
        )
        if battery_unit_selection_query is not None:
            current_query = battery_unit_selection_query
        installment_budget_context = (
            _customer_product_request_context_query(text, history)
            if _is_installment_budget_options_request(text, history)
            else None
        )
        product_interest_context = _product_interest_model_context(text, history)
        selected_model = product_interest_context[0] if product_interest_context else None
        default_condition = product_interest_context[1] if product_interest_context else None
        customer_request_context = installment_budget_context or _customer_product_request_context_query(
            text,
            history,
        )
        current_request_budget = _extract_latest_budget_limit(text)
        is_current_cheapest_request = _is_cheapest_catalog_request(text)
        latest_customer_message = next(
            (
                entry.get("content", "").strip()
                for entry in reversed(history or [])
                if entry.get("role") == "user" and entry.get("content", "").strip()
            ),
            "",
        )
        history_entries = history or []
        latest_customer_anchor_index = max(
            (
                index
                for index, entry in enumerate(history_entries)
                if entry.get("role") == "user"
                and (
                    _has_product_reference(_normalize(entry.get("content", "")))
                    or _extract_bare_catalog_model_reference(entry.get("content", ""))
                )
            ),
            default=-1,
        )
        newer_generic_catalog_list_index = max(
            (
                index
                for index, entry in enumerate(history_entries)
                if index > latest_customer_anchor_index
                and entry.get("role") == "user"
                and (
                    _is_available_list_request(entry.get("content", ""))
                    or _is_sealed_catalog_list_request(entry.get("content", ""))
                )
                and _has_recent_catalog_product_context(
                    history_entries[index + 1 :],
                    skip_acknowledgements=True,
                )
            ),
            default=None,
        )
        newer_generic_catalog_list = newer_generic_catalog_list_index is not None
        current_request_has_catalog_anchor = bool(
            _has_product_reference(_normalize(text))
            or _extract_bare_catalog_model_reference(text)
        )
        customer_affordability_request = is_current_cheapest_request or (
            current_request_budget is not None
            and not _has_product_reference(_normalize(text))
            and not _extract_bare_catalog_model_reference(text)
            and _is_cheapest_catalog_request(latest_customer_message)
        )
        customer_request_has_catalog_anchor = bool(
            _catalog_families(customer_request_context)
            or _has_product_reference(_normalize(customer_request_context))
            or _extract_bare_catalog_model_reference(customer_request_context)
        ) and (not newer_generic_catalog_list or current_request_has_catalog_anchor)
        selection_context_used = battery_unit_selection_query is not None or bool(
            selected_model
            and not _has_product_reference(_normalize(current_query))
            and not is_current_cheapest_request
        )
        if selection_context_used and battery_unit_selection_query is None:
            normalized_text = _normalize(text)
            selected_conditions: list[str] = []
            if _has_seminovo_reference(normalized_text):
                selected_conditions.append("seminovo")
            if _has_explicit_sealed_condition(normalized_text):
                selected_conditions.append("lacrado")
            if not selected_conditions and default_condition:
                selected_conditions.append(default_condition)
            selection_query = " ".join([selected_model, *selected_conditions])
            current_query = (
                selection_query
                if _bare_model_selection_reply(text)
                else "\n".join((selection_query, current_query)).strip()
            )
        if (
            (
                _is_bare_model_availability_request(current_query)
                or _is_bare_model_catalog_followup(current_query, history)
            )
            and len(_requested_iphone_model_keys(current_query)) <= 1
        ):
            # Expand shorthand only for a single model. In a batched request
            # such as "um 13 ou 14", prefixing the first bare number would
            # make the explicit-model parser discard the other alternative.
            bare_model = _extract_bare_catalog_model_reference(current_query)
            if bare_model:
                current_query = f"{bare_model} {current_query}".strip()
        query = current_query
        if installment_budget_context:
            query = installment_budget_context
        elif (
            history
            and not _has_product_reference(_normalize(current_query))
            and not _is_accessory_catalog_request(current_query)
            and not _is_case_accessory_request(current_query)
            and (
                _is_catalog_followup(current_query)
                or _is_catalog_availability_confirmation(current_query, history)
                or _is_catalog_model_confirmation(current_query, history)
                or capacity_availability_followup
                or battery_unit_selection_query is not None
                or _is_contextual_pronta_entrega_followup(current_query, history)
                or customer_affordability_request
            )
        ):
            if newer_generic_catalog_list_index is not None:
                product_context_history = history_entries[newer_generic_catalog_list_index:]
                while product_context_history and _CATALOG_CONTEXT_ACKNOWLEDGEMENT_RE.fullmatch(
                    _normalize(product_context_history[-1].get("content", ""))
                ):
                    product_context_history = product_context_history[:-1]
            else:
                product_context_history = history
            query = (
                customer_request_context
                if customer_affordability_request
                and customer_request_has_catalog_anchor
                else _product_context_query(
                    current_query,
                    product_context_history,
                    strip_assistant_constraints=not (
                        customer_affordability_request
                        and not customer_request_has_catalog_anchor
                    ),
                )
            )
        if ipad_model_alternative_price_followup:
            query = _customer_product_request_context_query(text, history)
        if capacity_availability_followup:
            # Photo metadata from the previous turn must not veto the current
            # stock question or remain an active intent marker.
            query = re.sub(r"\b(?:foto|fotos|imagem|imagens)\b", " ", query)
            query = re.sub(r"\s+", " ", query).strip()
        if _is_other_color_request(text) or _is_catalog_color_list_request(text):
            query = _strip_catalog_color_references(
                query,
                list(getattr(self.cache, "items", []) or []),
            )
        if not allow_buyer_interest_with_image and not installment_budget_context:
            pending_multi_model_context = _pending_multi_model_product_request_context(
                text,
                history,
            )
            if pending_multi_model_context:
                query = pending_multi_model_context
        visual_search_query = (
            _visual_catalog_search_query(
                image_description,
                list(getattr(self.cache, "items", []) or []),
            )
            if allow_buyer_interest_with_image
            else None
        )
        if allow_buyer_interest_with_image and visual_search_query is None:
            return None
        catalog_search_query = visual_search_query or query
        if (
            not visual_search_query
            and _is_catalog_price_followup(text)
            and _has_recent_catalog_product_context(history)
        ):
            contextual_models = _requested_iphone_model_keys(query)
            latest_customer_model: tuple[int | str, str] | None = None
            for entry in reversed(history or []):
                if entry.get("role") != "user":
                    continue
                customer_models = _requested_iphone_model_keys(entry.get("content", ""))
                if len(customer_models) == 1:
                    latest_customer_model = customer_models[0]
                    break
            if latest_customer_model is not None:
                contextual_models = (latest_customer_model,)
            if len(contextual_models) == 1 and _catalog_families(query) == ("iphone",):
                number, variant = contextual_models[0]
                model = "iPhone " + str(number).upper()
                if variant:
                    model += " " + variant.title()
                search_parts = [model]

                current_or_contextual_capacities = (
                    _requested_capacity_keys(text) or _requested_capacity_keys(query)
                )
                for capacity in current_or_contextual_capacities:
                    capacity_match = re.fullmatch(
                        r"(?P<number>\d+(?:\.\d+)?)(?P<unit>gb|tb)",
                        capacity,
                    )
                    if capacity_match:
                        search_parts.append(
                            f"{capacity_match.group('number')} {capacity_match.group('unit').upper()}"
                        )

                customer_context = _customer_product_request_context_query(text, history)
                customer_normalized = _normalize(customer_context)
                if _has_seminovo_reference(customer_normalized):
                    search_parts.append("seminovo")
                elif _has_explicit_sealed_condition(customer_normalized):
                    search_parts.append("lacrado")
                customer_battery = _requested_battery_health(customer_context)
                if customer_battery is not None:
                    search_parts.extend(("bateria", f"{customer_battery:g}%"))
                customer_colors = _requested_catalog_colors(
                    customer_context,
                    list(getattr(self.cache, "items", []) or []),
                )
                if len(customer_colors) == 1:
                    search_parts.append(customer_colors[0])
                search_parts.append("valor")
                catalog_search_query = " ".join(search_parts)
                query = catalog_search_query
        catalog_filter_query = (
            _current_catalog_context(text, visual_search_query)
            if visual_search_query
            else query
        )
        if not (
            _is_product_availability_request(
                query,
                history=history,
                allow_buyer_interest_with_image=allow_buyer_interest_with_image,
                buyer_interest_text=text,
            )
            or _is_contextual_pronta_entrega_followup(text, history)
            or ipad_model_alternative_price_followup
            or battery_unit_selection_query is not None
            or (
                is_current_cheapest_request
                and _has_recent_catalog_product_context(
                    history,
                    skip_acknowledgements=True,
                )
            )
        ):
            return None

        requested_budget = None
        if not installment_budget_context:
            requested_budget = current_request_budget
            if requested_budget is None and not allow_buyer_interest_with_image:
                requested_budget = _extract_latest_budget_limit(customer_request_context)
        requested_quantity = _requested_device_quantity(
            text if allow_buyer_interest_with_image else query
        )
        customer_condition_context = (
            seminovo_trade_in_query
            or _customer_product_condition_context_query(text, history)
        )
        pronta_entrega_only = _is_pronta_entrega_only_request(
            text
        ) or _is_pronta_entrega_only_request(customer_request_context)
        if selection_context_used:
            condition_query = current_query
        else:
            condition_query = customer_condition_context
        try:
            candidates = await self.cache.search(
                _availability_catalog_query(catalog_search_query),
                limit=300,
            )
        except Exception:
            alternative = await self._try_unavailable_lacrado_alternative(
                catalog_filter_query,
                requested_budget=requested_budget,
                condition_query=condition_query,
            )
            if alternative is not None:
                return alternative
            return None

        public_candidates = [
            item
            for item in candidates
            if (
                _is_sealed_accessory_item(item)
                if _is_accessory_catalog_request(catalog_filter_query)
                else _is_device_item(item)
            )
            and (getattr(item, "source", None) != "mercado_phone" or _is_available_item(item))
        ]
        public_candidates = [
            item
            for item in public_candidates
            if _matches_requested_model(catalog_filter_query, item)
        ]
        if pronta_entrega_only:
            public_candidates = [
                item
                for item in public_candidates
                if getattr(item, "source", None) == "mercado_phone"
            ]
        if requested_budget is not None:
            within_budget: list[Any] = []
            for item in public_candidates:
                price = getattr(item, "price_brl", None)
                try:
                    if price is not None and float(price) <= requested_budget:
                        within_budget.append(item)
                except (TypeError, ValueError):
                    continue
            public_candidates = within_budget

        requested_capacities = _requested_capacity_keys(text) or _requested_capacity_keys(
            catalog_filter_query
        )
        requested_families = _catalog_families(catalog_filter_query)
        requested_apple_watch_models = (
            _requested_apple_watch_model_keys(text, allow_bare=True)
            if "apple_watch" in requested_families
            else ()
        )
        if requested_apple_watch_models:
            public_candidates = [
                item
                for item in public_candidates
                if _matches_requested_apple_watch_model(
                    requested_apple_watch_models,
                    item,
                )
            ]
        requested_ipad_models = _requested_ipad_model_keys(catalog_filter_query)
        requested_models = _requested_iphone_model_keys(catalog_filter_query)
        requested_model_segments = _requested_model_segments(customer_request_context)
        model_attribute_items = [
            *public_candidates,
            *list(getattr(self.cache, "items", []) or []),
            *list(getattr(getattr(self.cache, "sealed_cache", None), "items", []) or []),
        ]
        requested_model_scopes = {}
        if len(requested_models) > 1:
            requested_model_scopes = _requested_model_attribute_scopes(
                requested_model_segments,
                model_attribute_items,
            )

        visual_context = image_description or ""
        image_history_index: int | None = None
        if not visual_context:
            for index in range(len(history or []) - 1, -1, -1):
                entry = (history or [])[index]
                content = entry.get("content", "").strip()
                if entry.get("role") == "user" and _is_image_history_entry(content):
                    visual_context = content
                    image_history_index = index
                    break
        visual_model_segments = (
            _visual_iphone_model_segments(visual_context)
            if visual_context
            else {}
        )
        visual_model_scope_applied = False
        visual_model_scoped_colors: tuple[str, ...] = ()
        has_later_user_colors = False
        if (
            len(requested_models) == 1
            and not _is_other_color_request(text)
            and not _is_catalog_color_list_request(text)
        ):
            selected_model = requested_models[0]
            selected_visual_segments = visual_model_segments.get(selected_model, ())
            later_user_messages = [
                entry.get("content", "").strip()
                for index, entry in enumerate(history or [])
                if image_history_index is not None
                and index > image_history_index
                and entry.get("role") == "user"
                and entry.get("content", "").strip()
                and not _is_image_history_entry(entry.get("content", ""))
            ]
            has_later_user_colors = any(
                _requested_catalog_colors(message, model_attribute_items)
                for message in later_user_messages
            )
            has_later_user_capacities = any(
                _requested_capacity_keys(message)
                for message in later_user_messages
            )
            if selected_visual_segments:
                visual_scopes = _requested_model_attribute_scopes(
                    {selected_model: selected_visual_segments},
                    model_attribute_items,
                ).get(selected_model, ())
                explicit_text_colors = _requested_catalog_colors(text, model_attribute_items)
                explicit_text_capacities = _requested_capacity_keys(text)
                scoped_visual_attributes = []
                scoped_colors: list[str] = []
                for segment, capacities, colors in visual_scopes:
                    if explicit_text_capacities or has_later_user_capacities:
                        capacities = ()
                    if (
                        explicit_text_colors
                        or has_later_user_colors
                        or explicit_text_capacities
                        or has_later_user_capacities
                    ):
                        colors = ()
                    if capacities or colors:
                        scoped_visual_attributes.append((segment, capacities, colors))
                    for color in colors:
                        if color not in scoped_colors:
                            scoped_colors.append(color)
                requested_model_scopes = (
                    {selected_model: tuple(scoped_visual_attributes)}
                    if scoped_visual_attributes
                    else {}
                )
                visual_model_scope_applied = True
                visual_model_scoped_colors = tuple(scoped_colors)

        def matches_model_attributes(
            item: Any,
            capacities: tuple[str, ...],
            colors: tuple[str, ...],
        ) -> bool:
            capacity = _capacity_key(
                getattr(item, "capacity", None) or getattr(item, "name", "")
            )
            return (
                (not capacities or capacity in capacities)
                and (not colors or _item_matches_catalog_colors(item, colors))
            )

        def matches_requested_model_scope(item: Any) -> bool:
            model = _model_key(getattr(item, "name", ""))
            model_scopes = requested_model_scopes.get(model, ())
            if not model_scopes:
                return True
            return any(
                matches_model_attributes(item, capacities, colors)
                for _segment, capacities, colors in model_scopes
            )

        if requested_model_scopes:
            public_candidates = [
                item for item in public_candidates if matches_requested_model_scope(item)
            ]
        requested_pro_max_without_generation = bool(
            not requested_models
            and re.search(r"\bpro\s+max\b", _normalize(catalog_filter_query))
        )
        if requested_pro_max_without_generation:
            public_candidates = [
                item
                for item in public_candidates
                if re.search(
                    r"\bpro\s+max\b",
                    _normalize(str(getattr(item, "name", "") or "")),
                )
            ]
        scoped_capacity = _trailing_model_capacity_scope(
            text,
            _requested_iphone_model_keys(text),
        )
        if scoped_capacity is None and not _requested_capacity_keys(text):
            scoped_capacity = _trailing_model_capacity_scope(
                catalog_filter_query,
                requested_models,
            )
        if scoped_capacity is not None:
            scoped_model, scoped_capacity_key = scoped_capacity
            public_candidates = [
                item
                for item in public_candidates
                if _model_key(getattr(item, "name", "")) != scoped_model
                or _capacity_key(getattr(item, "capacity", None) or getattr(item, "name", ""))
                == scoped_capacity_key
            ]
        elif requested_capacities and not requested_model_scopes:
            public_candidates = [
                item
                for item in public_candidates
                if _capacity_key(getattr(item, "capacity", None) or getattr(item, "name", ""))
                in requested_capacities
            ]

        normalized_query = _normalize(condition_query)
        requested_conditions: set[str] = set()
        has_explicit_sealed_condition = _has_explicit_sealed_condition(normalized_query)
        if has_explicit_sealed_condition:
            requested_conditions.add("lacrado")
        if _has_seminovo_reference(normalized_query):
            requested_conditions.add("seminovo")
        condition_was_requested = bool(requested_conditions)
        single_condition_requested = len(requested_conditions) == 1
        if condition_was_requested:
            public_candidates = [
                item
                for item in public_candidates
                if ("lacrado" if _is_sealed_item(item) else "seminovo") in requested_conditions
            ]

        installment_budget = (
            _extract_installment_budget(customer_request_context)
            if installment_budget_context
            else None
        )
        entry_amount = _extract_entry_amount(customer_request_context) if installment_budget else None
        installment_quotes: dict[int, dict[str, Any]] = {}
        if installment_budget is not None:
            installment_count, monthly_limit = installment_budget
            matching_installments: list[Any] = []
            for item in public_candidates:
                try:
                    result = (
                        simulate_installment_with_entry(item, entry_amount, installment_count)
                        if entry_amount is not None
                        else simulate_installment(item, installment_count)
                    )
                    monthly_payment = float(result.get("valor_parcela_brl"))
                except (TypeError, ValueError, OverflowError):
                    continue
                if result.get("encontrado") and monthly_payment <= monthly_limit:
                    matching_installments.append(item)
                    installment_quotes[id(item)] = result
            public_candidates = matching_installments

        color_catalog_items = [
            *public_candidates,
            *list(getattr(self.cache, "items", []) or []),
            *list(getattr(getattr(self.cache, "sealed_cache", None), "items", []) or []),
        ]
        exploring_colors = _is_other_color_request(text) or _is_catalog_color_list_request(text)
        requested_catalog_colors: tuple[str, ...] = ()
        if not exploring_colors:
            requested_catalog_colors = _latest_catalog_color_request(
                text,
                "",
                color_catalog_items,
            )
            if not requested_catalog_colors and image_description:
                requested_catalog_colors = _latest_catalog_color_request(
                    image_description,
                    "",
                    color_catalog_items,
                )
            if not requested_catalog_colors:
                requested_catalog_colors = _latest_catalog_color_request(
                    "",
                    customer_request_context,
                    color_catalog_items,
                )
        if (
            visual_model_scope_applied
            and not _requested_catalog_colors(text, color_catalog_items)
            and not has_later_user_colors
        ):
            requested_catalog_colors = visual_model_scoped_colors
        color_either_or_request = (
            not exploring_colors
            and _is_catalog_color_either_or_request(
                text,
                image_description,
                customer_request_context,
                color_catalog_items,
            )
        )
        single_catalog_model_request = (
            len(requested_models) <= 1
            and len(requested_ipad_models) <= 1
            and len(requested_families) <= 1
        )
        requested_color_unavailable = False
        unavailable_requested_colors: tuple[str, ...] = ()
        has_available_requested_color = False
        if single_catalog_model_request and requested_catalog_colors:
            matching_color_candidates = [
                item
                for item in public_candidates
                if _item_matches_catalog_colors(item, requested_catalog_colors)
            ]
            available_requested_colors = tuple(
                color
                for color in requested_catalog_colors
                if any(
                    _item_matches_catalog_colors(item, (color,))
                    for item in public_candidates
                )
            )
            unavailable_requested_colors = tuple(
                color
                for color in requested_catalog_colors
                if color not in available_requested_colors
            )
            has_available_requested_color = bool(available_requested_colors)
            requested_color_unavailable = bool(
                public_candidates
                and unavailable_requested_colors
                and not (color_either_or_request and has_available_requested_color)
            )
            if matching_color_candidates:
                public_candidates = matching_color_candidates

        if not public_candidates:
            if installment_budget is not None:
                installment_count, monthly_limit = installment_budget
                entry_text = (
                    f" e entrada de {format_brl(entry_amount)}"
                    if entry_amount is not None
                    else " sem entrada"
                )
                return AgentDecision(
                    reply=(
                        f"Não encontrei aparelhos com preço confirmado que caibam em "
                        f"{installment_count}x de até {format_brl(monthly_limit)}{entry_text}. "
                        "Posso procurar opções com uma entrada ou com uma parcela mensal maior?"
                    ),
                    confidence="medium",
                )
            if _is_bare_model_catalog_followup(text, history):
                requested_label = _extract_bare_catalog_model_reference(text)
                if requested_label:
                    return AgentDecision(
                        reply=(
                            f"No momento, não localizei o {requested_label} disponível no estoque. "
                            "Posso verificar outro modelo ou capacidade?"
                        ),
                        confidence="medium",
                    )
            if requested_apple_watch_models:
                watch_labels = []
                for family, number, size in requested_apple_watch_models:
                    family_label = {
                        "series": "Series",
                        "se": "SE",
                        "ultra": "Ultra",
                    }[family]
                    label = f"Apple Watch {family_label} {number}"
                    if size:
                        label += f" {size} mm"
                    watch_labels.append(label)
                requested_watch_label = " e ".join(watch_labels)
                if pronta_entrega_only:
                    reply = (
                        f"Não localizei {requested_watch_label} a pronta entrega no estoque "
                        "no momento. Se quiser, posso consultar opções por encomenda ou outros modelos."
                    )
                else:
                    reply = (
                        f"No momento, não localizei {requested_watch_label} disponível no catálogo. "
                        "Posso verificar outra cor ou tamanho?"
                    )
                return AgentDecision(reply=reply, confidence="medium")
            if not pronta_entrega_only:
                alternative = await self._try_unavailable_lacrado_alternative(
                    catalog_filter_query,
                    requested_budget=requested_budget,
                    condition_query=condition_query,
                )
                if alternative is not None:
                    return alternative
                if requested_budget is None:
                    alternative = await self._try_unavailable_seminew_alternative(
                        catalog_filter_query
                    )
                    if alternative is not None:
                        return alternative
            capacity_text = (
                f" {', '.join(value.upper() for value in requested_capacities)}"
                if requested_capacities
                else ""
            )
            if pronta_entrega_only:
                return AgentDecision(
                    reply=(
                        "Não localizei esse modelo a pronta entrega no estoque no momento. "
                        "Se quiser, posso consultar opções por encomenda ou outros modelos."
                    ),
                    confidence="medium",
                )
            if requested_budget is not None:
                return AgentDecision(
                    reply=(
                        f"Não localizei aparelhos disponíveis até {format_brl(requested_budget)}{capacity_text}. "
                        "Posso procurar em uma faixa maior ou em outro modelo?"
                    ),
                    confidence="medium",
                )
            return AgentDecision(
                reply=(
                    f"No momento não localizei uma opção cadastrada{capacity_text} para esse produto. "
                    "Pode me informar outro modelo ou capacidade?"
                ),
                confidence="medium",
            )

        cheapest_request = is_current_cheapest_request
        has_explicit_catalog_attributes = (
            _requested_battery_health(catalog_filter_query) is not None
            or bool(requested_catalog_colors)
            or _has_requested_catalog_color(catalog_filter_query, public_candidates)
        )
        broad_request = (
            requested_budget is not None
            or (
                installment_budget is not None
                and not has_explicit_catalog_attributes
            )
            or requested_quantity is not None
            or _is_broad_airpods_request(catalog_filter_query)
            or len(requested_families) > 1
            or len(requested_ipad_models) > 1
            or _requested_iphone_model_floor(catalog_filter_query) is not None
        )
        def price_sort_key(item: Any) -> tuple[float, str, str, str]:
            numeric_price = _confirmed_catalog_price(item)
            if numeric_price is None:
                numeric_price = float("inf")
            return (
                numeric_price,
                _normalize(str(getattr(item, "name", "") or "")),
                _normalize(str(getattr(item, "capacity", "") or "")),
                _normalize(str(getattr(item, "color", None) or getattr(item, "colors", "") or "")),
            )

        if requested_color_unavailable:
            selected = sorted(public_candidates, key=price_sort_key)
        elif cheapest_request:
            priced_candidates = [
                item for item in public_candidates if _confirmed_catalog_price(item) is not None
            ]
            if not priced_candidates:
                return AgentDecision(
                    reply=(
                        "No momento não localizei nenhum aparelho com preço confirmado para comparar. "
                        "Posso verificar outra opção?"
                    ),
                    confidence="medium",
                )
            if len(requested_models) > 1 and len(_requested_model_segments(text)) > 1:
                selected = []
                selected_ids: set[str] = set()
                for requested_model in requested_models:
                    model_candidates = [
                        item
                        for item in priced_candidates
                        if _model_key(getattr(item, "name", "")) == requested_model
                    ]
                    model_scopes = requested_model_scopes.get(requested_model, ())
                    selection_scopes = model_scopes or (("", (), ()),)
                    for _segment, capacities, colors in selection_scopes:
                        scoped_candidates = [
                            item
                            for item in model_candidates
                            if matches_model_attributes(item, capacities, colors)
                        ]
                        if not scoped_candidates:
                            continue
                        selected_item = min(scoped_candidates, key=price_sort_key)
                        external_id = str(getattr(selected_item, "external_id", "") or "")
                        if external_id not in selected_ids:
                            selected.append(selected_item)
                            selected_ids.add(external_id)
            else:
                selected = sorted(priced_candidates, key=price_sort_key)[:1]
        elif broad_request:
            selected = sorted(public_candidates, key=price_sort_key)
        else:
            scored = [
                (_catalog_score(catalog_search_query, item), item)
                for item in public_candidates
            ]
            best_score = max(score for score, _item in scored)
            if best_score <= 0:
                if not pronta_entrega_only:
                    alternative = await self._try_unavailable_lacrado_alternative(
                        catalog_filter_query,
                        requested_budget=requested_budget,
                        condition_query=condition_query,
                    )
                    if alternative is not None:
                        return alternative
                    if requested_budget is None:
                        alternative = await self._try_unavailable_seminew_alternative(
                            catalog_filter_query
                        )
                        if alternative is not None:
                            return alternative
                if pronta_entrega_only:
                    return AgentDecision(
                        reply=(
                            "Não localizei esse modelo a pronta entrega no estoque no momento. "
                            "Se quiser, posso consultar opções por encomenda ou outros modelos."
                        ),
                        confidence="medium",
                    )
                return AgentDecision(
                    reply="No momento não localizei esse produto no catálogo. Pode me informar o modelo ou capacidade?",
                    confidence="medium",
                )

            multiple_model_request_without_capacity = (
                len(requested_models) > 1
                and not requested_capacities
            )
            return_all_matching_units = (
                (
                    installment_budget is not None
                    or (
                        (
                            len(requested_models) == 1
                            and (bool(requested_conditions) or len(requested_capacities) <= 1)
                        )
                        or multiple_model_request_without_capacity
                        or scoped_capacity is not None
                        or (
                            len(requested_models) == 1
                            and not requested_capacities
                            and "possibilidade de comprar" in _normalize(text)
                        )
                    )
                )
                and requested_quantity is None
                and not has_explicit_catalog_attributes
            )

            def condition_matches(item: Any) -> bool:
                if not requested_conditions:
                    return True
                condition = "lacrado" if _is_sealed_item(item) else "seminovo"
                return condition in requested_conditions

            def best_matches(scored_items: list[tuple[int, Any]]) -> list[Any]:
                positive = [(score, item) for score, item in scored_items if score > 0]
                if not positive:
                    return []
                best = max(score for score, _item in positive)
                return [item for score, item in positive if score == best]

            def include_ready_sealed_units(
                selected_items: list[Any],
                scored_items: list[tuple[int, Any]],
            ) -> list[Any]:
                """Keep physical sealed stock beside equally matching sheet prices."""
                if (
                    (
                        "seminovo" in requested_conditions
                        and "lacrado" not in requested_conditions
                    )
                    or requested_quantity is not None
                    or _requested_battery_health(catalog_filter_query) is not None
                    or _has_requested_catalog_color(catalog_filter_query, public_candidates)
                ):
                    return selected_items

                selected_ids = {str(getattr(item, "external_id", "")) for item in selected_items}
                ready_items = [
                    item
                    for score, item in scored_items
                    if score > 0
                    and getattr(item, "source", "") == "mercado_phone"
                    and _is_sealed_item(item)
                    and str(getattr(item, "external_id", "")) not in selected_ids
                ]
                return [*ready_items, *selected_items]

            def select_best_matches(scored_items: list[tuple[int, Any]]) -> list[Any]:
                if single_condition_requested:
                    return include_ready_sealed_units(best_matches(scored_items), scored_items)

                grouped: dict[str, list[tuple[int, Any]]] = {
                    "seminovo": [],
                    "lacrado": [],
                }
                for score, item in scored_items:
                    condition = "lacrado" if _is_sealed_item(item) else "seminovo"
                    grouped[condition].append((score, item))

                selected_by_condition: list[Any] = []
                for condition in ("seminovo", "lacrado"):
                    selected = best_matches(grouped[condition])
                    if condition == "lacrado":
                        selected = include_ready_sealed_units(selected, grouped[condition])
                    selected_by_condition.extend(selected)
                return selected_by_condition or best_matches(scored_items)

            def select_requested_model_matches(scored_items: list[tuple[int, Any]]) -> list[Any]:
                if len(requested_models) <= 1:
                    return select_best_matches(scored_items)

                selected_by_model: list[Any] = []
                selected_by_model_ids: set[str] = set()
                for requested_model in requested_models:
                    model_matches = [
                        (score, item)
                        for score, item in scored_items
                        if _model_key(getattr(item, "name", "")) == requested_model
                    ]
                    model_scopes = requested_model_scopes.get(requested_model, ())
                    if model_scopes:
                        for segment, capacities, colors in model_scopes:
                            scoped_matches = [
                                (_catalog_score(segment, item), item)
                                for _score, item in model_matches
                                if matches_model_attributes(item, capacities, colors)
                            ]
                            for item in select_best_matches(scoped_matches):
                                external_id = str(getattr(item, "external_id", "") or "")
                                if external_id not in selected_by_model_ids:
                                    selected_by_model.append(item)
                                    selected_by_model_ids.add(external_id)
                    else:
                        model_segments = requested_model_segments.get(requested_model, ())
                        if model_segments:
                            model_matches = [
                                (
                                    max(_catalog_score(segment, item) for segment in model_segments),
                                    item,
                                )
                                for _score, item in model_matches
                            ]
                        for item in select_best_matches(model_matches):
                            external_id = str(getattr(item, "external_id", "") or "")
                            if external_id not in selected_by_model_ids:
                                selected_by_model.append(item)
                                selected_by_model_ids.add(external_id)
                return selected_by_model

            if return_all_matching_units:
                selected = [
                    item
                    for score, item in scored
                    if score > 0 and condition_matches(item)
                ]
            elif len(requested_capacities) > 1 and not requested_model_scopes:
                selected = []
                for capacity in requested_capacities:
                    capacity_matches = [
                        (score, item)
                        for score, item in scored
                        if _capacity_key(getattr(item, "capacity", None) or getattr(item, "name", ""))
                        == capacity
                    ]
                    if not capacity_matches:
                        continue
                    selected.extend(select_requested_model_matches(capacity_matches))
            else:
                selected = select_requested_model_matches(scored)

            if (
                single_catalog_model_request
                and len(requested_catalog_colors) > 1
                and not color_either_or_request
                and not unavailable_requested_colors
            ):
                selected_ids = {
                    str(getattr(item, "external_id", ""))
                    for item in selected
                }
                for color in requested_catalog_colors:
                    color_matches = [
                        (score, item)
                        for score, item in scored
                        if _item_matches_catalog_colors(item, (color,))
                        and condition_matches(item)
                    ]
                    color_selected = select_best_matches(color_matches)
                    if not color_selected and color_matches:
                        color_selected = [
                            min(
                                color_matches,
                                key=lambda match: (-match[0], price_sort_key(match[1])),
                            )[1]
                        ]
                    for item in color_selected:
                        item_id = str(getattr(item, "external_id", ""))
                        if item_id not in selected_ids:
                            selected.append(item)
                            selected_ids.add(item_id)

        selected = _sort_product_availability_items(selected)
        availability_header = None
        if requested_color_unavailable and selected:
            availability_header = _unavailable_catalog_color_header(
                selected=selected,
                requested_models=requested_models,
                requested_ipad_models=requested_ipad_models,
                requested_families=requested_families,
                requested_catalog_colors=(
                    unavailable_requested_colors or requested_catalog_colors
                ),
                requested_capacities=requested_capacities,
                has_available_requested_color=has_available_requested_color,
                requested_iphone_variant=(
                    "pro max" if requested_pro_max_without_generation else None
                ),
                requested_apple_watch_models=requested_apple_watch_models,
            )
        reply = _format_product_availability(
            selected,
            header=availability_header,
            installment_budget=installment_budget,
            installment_quotes=installment_quotes,
            entry_amount=entry_amount,
        )
        if selected and _is_other_color_request(text):
            available_colors = _catalog_color_map(selected)
            if len(available_colors) == 1:
                available_color = next(iter(available_colors.values()))
                model_name = str(getattr(selected[0], "name", None) or "esse modelo").strip()
                reply = (
                    f"No momento, só encontrei o {model_name} na cor {available_color} 😊. "
                    "Não aparece outra cor disponível no catálogo."
                )
        bare_model_price_request = (
            not re.search(r"\biphones?\b", _normalize(catalog_filter_query))
            and bool(
                re.search(
                    r"\b(?:orcamento|valor(?:es)?|preco(?:s)?)\b",
                    _normalize(catalog_filter_query),
                )
            )
        )
        if (
            len(requested_models) > 1
            and selected
            and not requested_capacities
            and not condition_was_requested
            and bare_model_price_request
        ):
            selected_models = {
                _model_key(getattr(item, "name", "")) for item in selected
            }
            unavailable_models = [
                model for model in requested_models if model not in selected_models
            ]
            if unavailable_models:
                unavailable_labels = []
                for number, variant in unavailable_models:
                    if not variant:
                        unavailable_labels.append(f"iPhone {number} (modelo base)")
                    elif variant == "e":
                        unavailable_labels.append(f"iPhone {number}e")
                    else:
                        unavailable_labels.append(f"iPhone {number} {variant.title()}")
                reply += (
                    "\n\nNão localizei opção disponível para "
                    + " e ".join(unavailable_labels)
                    + "."
                )
        if len(requested_ipad_models) > 1 and selected:
            selected_ipad_models = {
                model
                for item in selected
                for model in _requested_ipad_model_keys(getattr(item, "name", ""))
                if model in requested_ipad_models
            }
            unavailable_ipad_models = [
                model for model in requested_ipad_models if model not in selected_ipad_models
            ]
            if unavailable_ipad_models:
                unavailable_labels = []
                for model in unavailable_ipad_models:
                    model_label = model.title() if model in {"air", "pro", "mini"} else model
                    unavailable_labels.append(f"iPad {model_label}")
                condition_label = (
                    " seminova"
                    if requested_conditions == {"seminovo"}
                    else " nova/lacrada"
                    if requested_conditions == {"lacrado"}
                    else ""
                )
                reply += (
                    "\n\nNão localizei opção"
                    + condition_label
                    + " disponível para "
                    + " e ".join(unavailable_labels)
                    + "."
                )
        if requested_quantity is not None:
            reply += (
                f"\n\nComo você precisa de {requested_quantity} aparelhos, "
                f"escolha os {requested_quantity} que prefere e depois me informe o horário desejado para retirada."
            )
        references = [str(getattr(item, "external_id", "")) for item in selected]
        return AgentDecision(
            reply=reply,
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

    def _try_store_hours(self, text: str) -> AgentDecision | None:
        if not _is_store_hours_request(text):
            return None
        reply = _store_hours_reply(self.faq)
        if _is_physical_store_request(text):
            reply = f"{reply} Endereço: {_store_address(self.faq)}."
        return AgentDecision(
            reply=reply,
            confidence="high",
        )

    def _try_visit_scheduling(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        is_visit = _is_visit_request(text)
        is_followup = _is_appointment_followup(text, history)
        pending_reservation = is_followup and _has_unanswered_reservation_request(history)
        is_reservation = _is_reservation_request(text) or pending_reservation
        if not (is_visit or is_reservation or is_followup):
            return None

        if is_reservation and not is_visit and not pending_reservation:
            visit_day = _visit_date_for_request(text)
            reply = _reservation_reply(self.faq, visit_day=visit_day)
            return AgentDecision(reply=reply, confidence="high")

        context = _appointment_context(text, history)
        if (
            is_followup
            and _has_visit_time_reference(context)
            and not _has_visit_date_reference(context)
            and _has_today_visit_offer(history)
        ):
            context = f"hoje\n{context}"
        visit_day = _visit_date_for_request(text) or _visit_date_for_request(context)
        if visit_day is not None and not _is_business_weekday(visit_day):
            reply = (
                _reservation_reply(self.faq, visit_day=visit_day)
                if is_reservation
                else _visit_day_reply(visit_day)
            )
            return AgentDecision(reply=reply, confidence="high")
        if _has_visit_date_reference(context) and _has_visit_time_reference(context):
            address = self.faq.get("address") or (
                "Avenida Nossa Senhora da Luz, 1341 - Jardim Social, Curitiba - PR, 82520-060"
            )
            visit_time = _visit_time_for_request(text)
            if visit_day is not None and visit_time is not None:
                reply = (
                    "Perfeito 😊 Vou registrar a solicitação da sua visita para "
                    f"{_today_label(visit_day)}, às {visit_time}, e encaminhar para um "
                    "atendente confirmar. O atendimento é feito com horário marcado. "
                    f"Endereço: {address}"
                )
            else:
                reply = (
                    "Perfeito 😊 Vou registrar a solicitação da sua visita para o dia e horário "
                    "informados e encaminhar para um atendente confirmar. O atendimento é feito "
                    "com horário marcado. "
                    f"Endereço: {address}"
                )
            if is_reservation:
                reply = (
                    f"{_reservation_policy(self.faq)} Não consigo garantir que o aparelho "
                    f"continue disponível até a visita. {reply}"
                )
            return AgentDecision(
                reply=reply,
                handoff=True,
                handoff_reason="Agendamento de visita solicitado; atendente deve confirmar o horário",
                confidence="high",
            )

        if is_reservation:
            visit_day = _visit_date_for_request(text)
            return AgentDecision(
                reply=_reservation_reply(self.faq, visit_day=visit_day),
                confidence="high",
            )

        if "amanha" in _normalize(text):
            reply = _tomorrow_store_reply(self.faq)
            return AgentDecision(reply=reply, confidence="high")

        visit_day = _visit_date_for_request(text)
        if visit_day is not None:
            return AgentDecision(reply=_visit_day_reply(visit_day), confidence="high")

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
        if (
            not _is_photo_request(text)
            and not _is_standalone_photo_followup(text, history)
            and not _is_photo_context_followup(text, history)
        ):
            return None
        if _is_photo_retry_request(text) and not _has_photo_request_in_history(
            history, current_text=text
        ):
            return None
        if _is_sealed_photo_request(text):
            return AgentDecision(reply=SEALED_PHOTO_REPLY, confidence="high")
        if _is_broad_promotion_photo_request(text):
            try:
                promotional_items = await self.cache.search("iPhone seminovo", limit=300)
            except Exception:
                promotional_items = []

            photo_items: list[Any] = []
            approved_urls: list[str] = []
            for item in promotional_items:
                item_text = (
                    f"{getattr(item, 'name', '')} {getattr(item, 'description', '')} "
                    f"{getattr(item, 'search_text', '')}"
                )
                if (
                    _catalog_family(item_text) != "iphone"
                    or _is_sealed_item(item)
                    or (
                        getattr(item, "source", None) == "mercado_phone"
                        and not _is_available_item(item)
                    )
                ):
                    continue
                urls = [
                    url
                    for url in (getattr(item, "photo_urls", []) or [])
                    if isinstance(url, str) and url.lower().startswith("https://")
                ]
                if not urls:
                    continue
                photo_items.append(item)
                approved_urls.extend(urls)

            approved_urls = list(dict.fromkeys(approved_urls))[:MAX_PRODUCT_PHOTOS]
            if approved_urls:
                references = [
                    str(getattr(item, "external_id", ""))
                    for item in photo_items
                    if getattr(item, "external_id", "")
                ]
                return AgentDecision(
                    reply="Claro! Seguem as fotos dos iPhones seminovos disponíveis na promoção.",
                    image_urls=approved_urls,
                    product_references=references,
                    confidence="high",
                )
            return AgentDecision(
                reply=(
                    "No momento não encontrei fotos cadastradas dos iPhones seminovos "
                    "disponíveis na promoção."
                ),
                confidence="medium",
            )
        current_query = _current_catalog_context(text, image_description)
        if not _has_product_reference(_normalize(current_query)):
            # A current photo request such as "fotos do 13" names a model even
            # when the customer omits the word "iPhone". Promote that shorthand
            # before inheriting a previous assistant list, which may mention
            # neighboring Pro and Pro Max variants.
            bare_model = _extract_bare_catalog_model_reference(current_query)
            if bare_model and _is_photo_request(current_query):
                current_query = f"{bare_model} {current_query}".strip()
        if _is_photo_context_followup(text, history) and not _has_product_reference(
            _normalize(current_query)
        ):
            followup_model = _extract_bare_catalog_model_reference(current_query)
            if followup_model:
                current_query = f"{followup_model} {current_query}".strip()
        query = _product_context_query(current_query, history)
        query = _strip_historical_accessory_terms_from_photo_query(
            query,
            current_query,
            request_text=text,
            image_description=image_description,
        )
        # A previous assistant answer can list several capacities. When the
        # current turn contains one explicit choice, make that choice the
        # first capacity seen by the photo resolver instead of letting the
        # historical list select its first entry.
        explicit_capacity_keys = _requested_capacity_keys(text)
        if len(explicit_capacity_keys) != 1:
            explicit_capacity_keys = _requested_capacity_keys(image_description or "")
        if len(explicit_capacity_keys) == 1:
            query = f"capacidade solicitada: {explicit_capacity_keys[0]}\n{query}".strip()
        current_color = _current_catalog_color(
            text,
            list(getattr(self.cache, "items", []) or []),
        )
        if current_color is None and image_description:
            current_color = _current_catalog_color(
                image_description,
                list(getattr(self.cache, "items", []) or []),
            )
        if current_color:
            query = f"foto_cor_atual: {current_color}\n{query}".strip()
        requested_models = _requested_iphone_model_keys(query)
        requested_capacities = _requested_capacity_keys(query)
        requested_condition = _requested_photo_condition(query)
        photo_model_batch_request = (
            len(requested_models) > 1
            or _requested_iphone_model_floor(query) is not None
        )
        return_all_matching_photo_units = (
            photo_model_batch_request
            and (
                _requested_iphone_model_floor(query) is not None
                or not requested_capacities
            )
            and _requested_battery_health(query) is None
            and not _has_requested_catalog_color(
                query,
                list(getattr(self.cache, "items", []) or []),
            )
        )
        finder = getattr(self.cache, "find_product_photos", None)

        def condition_matches(item: Any) -> bool:
            if requested_condition is None:
                return True
            is_sealed = _is_sealed_item(item)
            return is_sealed if requested_condition == "sealed" else not is_sealed

        def photo_lookup_failed(item: Any) -> bool:
            lookup_failed = getattr(self.cache, "product_photo_lookup_failed", None)
            product_id = str(getattr(item, "external_id", "") or "")
            return bool(
                product_id
                and callable(lookup_failed)
                and lookup_failed(product_id)
            )

        def photo_item_label(item: Any) -> str:
            product_parts = [
                str(getattr(item, "name", "") or "esse aparelho").strip()
            ]
            color = getattr(item, "color", None) or getattr(item, "colors", None)
            if isinstance(color, (list, tuple, set)):
                color = ", ".join(str(value).strip() for value in color if value)
            for detail in (getattr(item, "capacity", None), color):
                detail = str(detail or "").strip()
                if detail and _normalize(detail) not in _normalize(" ".join(product_parts)):
                    product_parts.append(detail)
            return " ".join(part for part in product_parts if part)

        def photo_item_label_list(items: list[Any]) -> str:
            labels = [photo_item_label(item) for item in items]
            if len(labels) > 1:
                return f"{', '.join(labels[:-1])} e {labels[-1]}"
            return labels[0] if labels else "os aparelhos indicados"

        def exact_candidates(items: list[Any]) -> list[Any]:
            candidates: list[Any] = []
            seen: set[str] = set()
            for item in items:
                external_id = str(getattr(item, "external_id", "") or "")
                if external_id in seen:
                    continue
                if not _is_device_item(item):
                    continue
                if (
                    getattr(item, "source", None) == "mercado_phone"
                    and not _is_available_item(item)
                ):
                    continue
                if not condition_matches(item) or not _matches_requested_model(query, item):
                    continue
                if requested_capacities and _capacity_key(
                    getattr(item, "capacity", None) or getattr(item, "name", "")
                ) not in requested_capacities:
                    continue
                seen.add(external_id)
                candidates.append(item)

            scored = [
                (_catalog_score(query, item, match_price=True), item)
                for item in candidates
            ]
            positive = [(score, item) for score, item in scored if score > 0]
            if not positive:
                return []
            best_score = max(score for score, _item in positive)
            return [item for score, item in positive if score == best_score]

        def multiple_photo_matches(
            items: list[Any],
            *,
            include_all_matching_units: bool = False,
        ) -> list[Any]:
            """Select requested photo units without losing valid alternatives."""
            scored: list[tuple[int, Any]] = []
            seen: set[str] = set()
            for item in items:
                external_id = str(getattr(item, "external_id", "") or "")
                if external_id in seen:
                    continue
                if not _is_device_item(item):
                    continue
                if (
                    getattr(item, "source", None) == "mercado_phone"
                    and not _is_available_item(item)
                ):
                    continue
                if not condition_matches(item) or not _matches_requested_model(query, item):
                    continue
                if requested_capacities and _capacity_key(
                    getattr(item, "capacity", None) or getattr(item, "name", "")
                ) not in requested_capacities:
                    continue
                score = _catalog_score(query, item, match_price=True)
                if score <= 0:
                    continue
                seen.add(external_id)
                scored.append((score, item))

            if include_all_matching_units:
                selected = [item for _score, item in scored]
            else:
                selected = []
                for requested_model in requested_models:
                    model_matches = [
                        (score, item)
                        for score, item in scored
                        if _model_key(getattr(item, "name", "")) == requested_model
                    ]
                    capacities = requested_capacities or (None,)
                    for capacity in capacities:
                        capacity_matches = [
                            (score, item)
                            for score, item in model_matches
                            if capacity is None
                            or _capacity_key(
                                getattr(item, "capacity", None) or getattr(item, "name", "")
                            )
                            == capacity
                        ]
                        if not capacity_matches:
                            continue
                        best_score = max(score for score, _item in capacity_matches)
                        selected.extend(
                            item for score, item in capacity_matches if score == best_score
                        )

            selected_by_id: dict[str, Any] = {}
            for item in selected:
                external_id = str(getattr(item, "external_id", "") or "")
                if external_id:
                    selected_by_id[external_id] = item
            return list(selected_by_id.values())

        def ambiguous_reply(items: list[Any]) -> AgentDecision:
            first = items[0]
            model = str(getattr(first, "name", "esse aparelho") or "esse aparelho").strip()
            condition = "novo lacrado" if _is_sealed_item(first) else "seminovo"
            lines: list[str] = []
            for item in items:
                capacity = str(getattr(item, "capacity", None) or "capacidade não informada").strip()
                color = str(
                    getattr(item, "color", None)
                    or getattr(item, "colors", None)
                    or "cor não informada"
                ).strip()
                photo_status = (
                    "fotos cadastradas"
                    if getattr(item, "photo_urls", [])
                    else "fotos a confirmar"
                )
                lines.append(f"• {capacity} — {color} — {photo_status}")
            return AgentDecision(
                reply=(
                    f"Encontrei o {model} {condition} em mais de uma opção:\n"
                    + "\n".join(lines)
                    + "\nQual capacidade você quer que eu envie nas fotos?"
                ),
                confidence="high",
            )

        if photo_model_batch_request:
            try:
                items = await self.cache.search(query, limit=300)
            except Exception:
                return None
            items = multiple_photo_matches(
                items,
                include_all_matching_units=return_all_matching_photo_units,
            )
        elif callable(finder):
            try:
                selected = await finder(query)
            except Exception:
                return None
            if (
                selected is None
                and current_color
                and _requested_photo_condition(current_query) is None
            ):
                # The preceding assistant card can carry a stale condition
                # label after the catalog row changes. Preserve that label on
                # the first lookup, but let the current explicit color select
                # the live item when the constrained lookup finds nothing.
                relaxed_query = _product_context_query(
                    current_query,
                    history,
                    strip_assistant_constraints=True,
                )
                if len(explicit_capacity_keys) == 1:
                    relaxed_query = (
                        f"capacidade solicitada: {explicit_capacity_keys[0]}\n{relaxed_query}"
                    ).strip()
                relaxed_query = f"foto_cor_atual: {current_color}\n{relaxed_query}".strip()
                if relaxed_query != query:
                    try:
                        selected = await finder(relaxed_query)
                    except Exception:
                        return None
                    if selected is not None:
                        query = relaxed_query
                        requested_condition = _requested_photo_condition(query)
            if selected is None:
                fallback: list[Any] = []
                sealed_cache = getattr(self.cache, "sealed_cache", None)
                if sealed_cache is not None:
                    try:
                        ensure_fresh = getattr(sealed_cache, "ensure_fresh", None)
                        if callable(ensure_fresh):
                            await ensure_fresh()
                        search_sealed = getattr(sealed_cache, "search", None)
                        if callable(search_sealed):
                            fallback.extend(await search_sealed(query, limit=5))
                        else:
                            fallback.extend(list(getattr(sealed_cache, "items", []))[:5])
                    except Exception:
                        pass
                try:
                    fallback.extend(await self.cache.search(query, limit=5))
                except Exception:
                    pass
                items = exact_candidates(fallback)
                if requested_condition is None:
                    used_items = [item for item in items if not _is_sealed_item(item)]
                    if used_items:
                        items = used_items
                if not items:
                    if requested_condition == "sealed":
                        return AgentDecision(reply=SEALED_PHOTO_REPLY, confidence="high")
                    alternative = await self._try_unavailable_seminew_alternative(
                        query,
                        requested_label=_extract_bare_catalog_model_reference(text),
                    )
                    if alternative is not None:
                        return alternative
                    requested_label = _extract_bare_catalog_model_reference(text) or "esse produto"
                    return AgentDecision(
                        reply=(
                            f"No momento, não localizei o {requested_label} disponível no estoque. "
                            "Posso verificar outro modelo ou capacidade?"
                        ),
                        confidence="medium",
                    )
            else:
                items = [selected]
        else:
            try:
                items = await self.cache.search(query, limit=5)
            except Exception:
                return None
        items = [item for item in items if condition_matches(item)]
        if items:
            if photo_model_batch_request:
                photo_rows = [
                    (
                        item,
                        [
                            url
                            for url in (getattr(item, "photo_urls", []) or [])
                            if isinstance(url, str) and url.lower().startswith("https://")
                        ],
                    )
                    for item in items
                ]
                available_items = [item for item, urls in photo_rows if urls]
                confirmed_missing = [
                    item
                    for item, urls in photo_rows
                    if not urls and not photo_lookup_failed(item)
                ]
                failed_lookup = [
                    item
                    for item, urls in photo_rows
                    if not urls and photo_lookup_failed(item)
                ]
                approved_urls = list(
                    dict.fromkeys(url for _item, urls in photo_rows for url in urls)
                )[:MAX_PRODUCT_PHOTOS]
                product_references = [
                    str(getattr(item, "external_id", ""))
                    for item in items
                    if getattr(item, "external_id", "")
                ]
                if not confirmed_missing and not failed_lookup:
                    return AgentDecision(
                        reply=(
                            "Claro! Seguem as fotos dos aparelhos que você indicou: "
                            f"{photo_item_label_list(available_items)}."
                        ),
                        image_urls=approved_urls,
                        product_references=product_references,
                        confidence="high",
                    )
                reply_parts: list[str] = []
                if available_items:
                    reply_parts.append(
                        "Claro! Seguem as fotos cadastradas de "
                        f"{photo_item_label_list(available_items)}."
                    )
                if confirmed_missing:
                    reply_parts.append(
                        "Não há fotos cadastradas para "
                        f"{photo_item_label_list(confirmed_missing)}. "
                        "Vou encaminhar o pedido para um atendente confirmar as fotos."
                    )
                if failed_lookup:
                    reply_parts.append(
                        "Não consegui confirmar as fotos de "
                        f"{photo_item_label_list(failed_lookup)} agora. "
                        "Tente novamente em instantes."
                    )
                return AgentDecision(
                    reply=" ".join(reply_parts),
                    handoff=bool(confirmed_missing),
                    handoff_reason=(
                        PRODUCT_PHOTO_HANDOFF_REASON if confirmed_missing else None
                    ),
                    image_urls=approved_urls,
                    product_references=product_references,
                    confidence="medium" if failed_lookup else "high",
                )
            if len(items) > 1:
                capacities = {
                    _capacity_key(getattr(item, "capacity", None) or getattr(item, "name", ""))
                    for item in items
                }
                approved_urls = [
                    url
                    for item in items
                    for url in (getattr(item, "photo_urls", []) or [])
                    if isinstance(url, str) and url
                ]
                if (
                    len(capacities) == 1
                    and None not in capacities
                    and not any(_is_sealed_item(item) for item in items)
                    and not _has_requested_catalog_color(current_query, items)
                    and len(approved_urls) == sum(
                        len(getattr(item, "photo_urls", []) or []) for item in items
                    )
                    and approved_urls
                ):
                    return AgentDecision(
                        reply=(
                            f"Claro! Seguem as fotos do {items[0].name} "
                            f"{items[0].capacity}."
                        ),
                        image_urls=list(dict.fromkeys(approved_urls))[:MAX_PRODUCT_PHOTOS],
                        product_references=[item.external_id for item in items],
                        confidence="high",
                    )
                return ambiguous_reply(items)
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
            product_label = photo_item_label(selected)
            product_reference = str(getattr(selected, "external_id", "") or "")
            if photo_lookup_failed(selected):
                return AgentDecision(
                    reply=(
                        f"Não consegui confirmar as fotos do {product_label} agora. "
                        "Tente novamente em instantes."
                    ),
                    confidence="low",
                    product_references=[product_reference] if product_reference else [],
                )
            return AgentDecision(
                reply=(
                    f"Encontrei o {product_label}, mas ainda não há fotos cadastradas para essa opção. "
                    "Vou encaminhar o pedido para um atendente confirmar as fotos."
                ),
                handoff=True,
                handoff_reason=PRODUCT_PHOTO_HANDOFF_REASON,
                confidence="high",
                product_references=[product_reference] if product_reference else [],
            )
        return None

    async def _try_combined_catalog_installment(
        self,
        text: str,
        history: list[dict[str, str]] | None,
        *,
        image_description: str | None = None,
    ) -> AgentDecision | None:
        if _extract_entry_amount(text) is not None:
            return None
        if _is_payment_link_request(text) or _is_boleto_payment_request(text):
            return None
        if not _is_product_availability_request(text, history=history):
            return None
        if not (_is_full_installment_request(text) or _is_specific_installment_request(text)):
            return None

        availability = await self._try_product_availability(
            text,
            history=history,
            image_description=image_description,
        )
        if availability is None:
            return None

        installment = await self._try_specific_installment(text, history)
        if installment is None:
            installment = await self._try_full_installment_table(
                text,
                history,
                image_description=image_description,
            )
        if installment is None:
            return availability

        sections = [availability.reply]
        if re.search(r"\bpix\b", _normalize(text)):
            sections.append("Sim 😊 No PIX, o valor é o mesmo preço à vista informado acima.")
        sections.append(installment.reply)
        return AgentDecision(
            reply="\n\n".join(sections),
            product_references=availability.product_references,
            confidence="high",
        )

    async def _try_multi_model_installment_tables(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        queries = _multi_model_installment_queries(text, history)
        if not queries:
            return None
        method = getattr(self.cache, "simulate_all_installments", None)
        if not callable(method):
            return None

        sections: list[str] = []
        references: list[str] = []
        all_found = True
        for query in queries:
            try:
                result = await method(query)
            except Exception:
                return None
            if result.get("encontrado"):
                sections.append(format_installment_table(result))
                continue
            if result.get("ambiguo"):
                ambiguous = _format_ambiguous_installment_decision(result)
                if ambiguous is not None:
                    sections.append(ambiguous.reply)
                    references.extend(ambiguous.product_references)
                    all_found = False
                    continue

            requested_model = _requested_iphone_model_keys(query)
            if requested_model:
                number, variant = requested_model[0]
                label = f"iPhone {number}" + (f" {variant.title()}" if variant else "")
            else:
                label = "esse modelo"
            sections.append(
                f"Não encontrei preço confirmado para simular o {label} no momento."
            )
            all_found = False

        return AgentDecision(
            reply="\n\n".join(sections),
            product_references=list(dict.fromkeys(references)),
            confidence="high" if all_found else "medium",
        )

    async def _try_both_unit_installment_tables(
        self,
        text: str,
        history: list[dict[str, str]] | None,
        result: dict[str, Any],
    ) -> AgentDecision | None:
        if not _is_both_unit_installment_request(text, history):
            return None
        candidates = result.get("candidatos") or []
        if len(candidates) != 2 or any(not isinstance(item, dict) for item in candidates):
            return None

        references = [str(item.get("referencia") or "") for item in candidates]
        if not all(references) or len(set(references)) != 2:
            return None
        method = getattr(self.cache, "simulate_all_installments", None)
        if not callable(method):
            return None

        sections: list[str] = []
        for index, candidate in enumerate(candidates, start=1):
            name = str(candidate.get("nome") or "").strip()
            capacity = str(candidate.get("capacidade") or "").strip()
            condition = str(candidate.get("condicao") or "").strip()
            color = str(candidate.get("cor") or "").strip()
            battery = candidate.get("saude_bateria")
            price = candidate.get("preco_brl")
            if not name or not capacity or price is None:
                return None

            query_parts = [name, capacity, condition, color]
            if battery is not None:
                try:
                    battery_value = float(battery)
                except (TypeError, ValueError):
                    return None
                query_parts.append(f"bateria {battery_value:g}%")
            try:
                table = await method(" ".join(part for part in query_parts if part))
            except Exception:
                return None
            if not table.get("encontrado"):
                return None
            try:
                candidate_price = float(price)
                simulated_price = float(table.get("preco_avista_brl"))
            except (TypeError, ValueError):
                return None
            if not math.isclose(candidate_price, simulated_price, rel_tol=0.0, abs_tol=0.01):
                return None

            details = [name, capacity]
            if condition:
                details.append(condition.lower())
            if battery is not None:
                details.append(f"bateria {battery_value:g}%")
            details.append(format_brl(candidate_price))
            sections.append(
                f"Opção {index} — {' — '.join(details)}\n{format_installment_table(table)}"
            )

        return AgentDecision(
            reply="\n\n".join(sections),
            product_references=references,
            confidence="high",
        )

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
        multiple_models = await self._try_multi_model_installment_tables(text, history)
        if multiple_models is not None:
            return multiple_models
        method = getattr(self.cache, "simulate_all_installments", None)
        if not callable(method):
            return None
        try:
            result = await method(query)
        except Exception:
            return None
        if result.get("encontrado"):
            return AgentDecision(
                reply=format_installment_table(result),
                confidence="high",
            )
        if result.get("ambiguo"):
            both_units = await self._try_both_unit_installment_tables(
                text,
                history,
                result,
            )
            if both_units is not None:
                return both_units
            condition_tables = await self._try_ambiguous_condition_installment_tables(
                query,
                result,
            )
            if condition_tables is not None:
                return condition_tables
            return _format_ambiguous_installment_decision(result)
        if not result.get("ambiguo"):
            alternative = await self._try_unavailable_seminew_alternative(query)
            if alternative is not None:
                return alternative
            return None

    async def _try_ambiguous_condition_installment_tables(
        self,
        query: str,
        result: dict[str, Any],
    ) -> AgentDecision | None:
        """Simulate both confirmed conditions when only condition is ambiguous."""
        candidates = result.get("candidatos") or []
        if len(candidates) != 2:
            return None

        normalized_query = _normalize(query)
        if _has_explicit_sealed_condition(normalized_query) or _has_seminovo_reference(
            normalized_query
        ):
            return None

        by_condition: dict[str, dict[str, Any]] = {}
        product_keys: set[tuple[str, str]] = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                return None
            condition = _normalize(str(candidate.get("condicao") or ""))
            if _has_explicit_sealed_condition(condition):
                key = "lacrado"
            elif _has_seminovo_reference(condition):
                key = "seminovo"
            else:
                return None
            if key in by_condition:
                return None
            by_condition[key] = candidate
            name = _normalize(str(candidate.get("nome") or ""))
            capacity = _normalize(str(candidate.get("capacidade") or ""))
            if not name or not capacity:
                return None
            product_keys.add((name, capacity))

        if set(by_condition) != {"seminovo", "lacrado"} or len(product_keys) != 1:
            return None

        method = getattr(self.cache, "simulate_all_installments", None)
        if not callable(method):
            return None

        sections: list[str] = []
        for condition, label in (
            ("seminovo", "Opção seminova"),
            ("lacrado", "Opção novo lacrado por encomenda"),
        ):
            try:
                table = await method(f"{query}\n{condition}")
            except Exception:
                return None
            if not table.get("encontrado"):
                return None
            if condition == "seminovo":
                battery = by_condition[condition].get("saude_bateria")
                if battery is not None:
                    label += f" (bateria {float(battery):g}%)"
            sections.append(f"{label}:\n{format_installment_table(table)}")

        return AgentDecision(
            reply="\n\n".join(sections),
            confidence="high",
        )

    async def _try_full_installment_table(
        self,
        text: str,
        history: list[dict[str, str]] | None,
        *,
        image_description: str | None = None,
    ) -> AgentDecision | None:
        if not (
            _is_full_installment_request(text)
            or _is_rate_model_followup(text, history)
            or _is_installment_model_followup(text, history)
        ):
            return None
        multiple_models = await self._try_multi_model_installment_tables(text, history)
        if multiple_models is not None:
            return multiple_models
        method = getattr(self.cache, "simulate_all_installments", None)
        if not callable(method):
            return None
        product_context = "\n".join(
            part for part in (text, image_description) if part and part.strip()
        )
        query = _installment_context_query(product_context, history)
        try:
            result = await method(query)
        except Exception:
            return None
        if not result.get("encontrado"):
            if result.get("ambiguo"):
                both_units = await self._try_both_unit_installment_tables(
                    text,
                    history,
                    result,
                )
                if both_units is not None:
                    return both_units
                condition_tables = await self._try_ambiguous_condition_installment_tables(
                    query,
                    result,
                )
                if condition_tables is not None:
                    return condition_tables
                return _format_ambiguous_installment_decision(result)
            return await self._try_unavailable_seminew_alternative(
                query
            )
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
        try:
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
        query = _installment_context_query(
            combined,
            history,
            strip_assistant_constraints=True,
        )
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


def _is_device_condition_question(text: str | None) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if re.search(
        r"\b(?:marcas?\s+de\s+uso|amassad\w*|riscos?\w*|"
        r"arranh\w*|defeit\w*|quebrad\w*)\b",
        normalized,
    ):
        return True
    if re.search(
        r"\b(?:"
        r"(?:ja\s+)?foi\s+(?:trocad\w*|substituid\w*)\b.{0,20}"
        r"(?:tela|display|vidro|bateria|peca\w*)"
        r"|(?:tela|display|vidro|bateria|peca\w*)\b.{0,20}"
        r"(?:ja\s+)?foi\s+(?:trocad\w*|substituid\w*)"
        r"|bateria\b.{0,30}\b(?:trocad\w*|substituid\w*)"
        r"|(?:tela|display|vidro)\s+(?:e|esta|sao)?\s*original\w*"
        r"|(?:tudo|todas?\s+as\s+pecas?)\s+(?:e|esta|sao)?\s*original\w*"
        r")\b",
        normalized,
    ):
        return True
    return bool(
        re.search(r"\b(?:o\s+que|que)\s+(?:seria|e|significa)\b", normalized)
        and re.search(r"\b(?:branc\w*|pret\w*|manch\w*|pont\w*|marc\w*|suj\w*)\b", normalized)
        and re.search(
            r"\b(?:perto|proxim\w*|entrada\s+(?:do|da)\s+carregador|"
            r"conector|porta\s+(?:do|da|de)\s+carregador)\b",
            normalized,
        )
    )


def _looks_like_trade_in_handoff(decision: AgentDecision) -> bool:
    normalized = _normalize(f"{decision.reply} {decision.handoff_reason}")
    has_device = re.search(
        r"\b(?:iphone|ipad|macbook|airpods?|apple\s+watch|celular|aparelho|"
        r"usado|usada|seminovo|seminova)\b",
        normalized,
    )
    has_trade_in_language = re.search(
        r"\b(?:parte do pagamento|como entrada|para troca|trade[- ]?in)\b",
        normalized,
    ) or (
        re.search(r"\b(?:avaliacao|avaliar)\b", normalized)
        and re.search(r"\b(?:iphone|ipad|macbook|airpods?|apple\s+watch|"
                      r"celular|aparelho|usado|usada|seminovo|seminova)\b", normalized)
    )
    return bool(has_device and has_trade_in_language)


def _ensure_trade_in_form_before_handoff(
    decision: AgentDecision,
    text: str,
    history: list[dict[str, str]] | None,
    image_description: str | None = None,
) -> AgentDecision:
    """Never transfer a trade-in conversation without first sending its form."""
    request_context = " ".join(
        part.strip()
        for part in (text, image_description)
        if part and part.strip()
    )

    if is_non_apple_trade_in_request(request_context):
        return decision.model_copy(
            update={
                "reply": NON_APPLE_TRADE_IN_REPLY,
                "handoff": False,
                "handoff_reason": None,
                "confidence": "high",
            }
        )

    if (
        not trade_in_em_andamento(history)
        and _is_catalog_buyer_interest(text)
        and _has_product_reference(_normalize(request_context))
        and _looks_like_trade_in_handoff(decision)
    ):
        return decision.model_copy(
            update={
                "reply": CATALOG_BUYER_DETAILS_REPLY,
                "handoff": True,
                "handoff_reason": CATALOG_BUYER_DETAILS_REASON,
                "confidence": "high",
            }
        )

    if _is_catalog_purchase_advice_question(request_context, history):
        return decision.model_copy(
            update={
                "reply": CATALOG_BUYER_DETAILS_REPLY,
                "handoff": True,
                "handoff_reason": CATALOG_BUYER_DETAILS_REASON,
                "confidence": "high",
            }
        )

    if not trade_in_em_andamento(history) and _is_catalog_price_negotiation(
        request_context, history
    ):
        return decision.model_copy(
            update={
                "reply": CATALOG_PRICE_NEGOTIATION_REPLY,
                "handoff": True,
                "handoff_reason": CATALOG_PRICE_NEGOTIATION_REASON,
                "confidence": "high",
            }
        )

    if (
        not trade_in_em_andamento(history)
        and is_purchase_without_trade_in_request(request_context, history)
        and _looks_like_trade_in_handoff(decision)
    ):
        return decision.model_copy(
            update={
                "reply": PURCHASE_WITHOUT_TRADE_IN_REPLY,
                "handoff": False,
                "handoff_reason": None,
                "confidence": "high",
                "product_references": [],
                "image_urls": [],
            }
        )
    if not decision.handoff or trade_in_em_andamento(history):
        return decision

    if _is_device_condition_question(request_context) and not is_trade_in_context_request(
        request_context, history
    ):
        return decision.model_copy(
            update={
                "reply": CONDITION_HANDOFF_REPLY,
                "handoff": True,
                "handoff_reason": CONDITION_HANDOFF_REASON,
                "confidence": "high",
            }
        )

    request_context = " ".join(
        part.strip()
        for part in (text, image_description)
        if part and part.strip()
    )
    if not (
        is_trade_in_context_request(request_context, history)
        or _looks_like_trade_in_handoff(decision)
    ):
        return decision

    return decision.model_copy(
        update={
            "reply": TRADE_IN_FORM,
            "handoff": True,
            "handoff_reason": TRADE_IN_REASON,
            "confidence": "high",
        }
    )
