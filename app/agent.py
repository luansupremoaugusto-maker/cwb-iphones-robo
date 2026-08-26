from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents import Agent, ModelSettings, Runner, function_tool, set_default_openai_key
from agents.tracing import set_tracing_disabled

from app.adapters.catalog_cache import (
    _catalog_score,
    _catalog_family,
    _is_available_item,
    _is_accessory_catalog_query,
    _is_device_item,
    _is_sealed_accessory_item,
    _matches_requested_model,
    _model_key,
    _requested_battery_health,
    _requested_iphone_model_keys,
    _requested_photo_condition,
)
from app.adapters.mercado_phone import InventoryCache
from app.adapters.mercado_phone_files import MAX_PRODUCT_PHOTOS
from app.config import Settings
from app.faq import FAQStore
from app.installments import (
    format_brl,
    format_installment_rates,
    format_installment_result,
    format_installment_table,
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
    is_trade_in_context_request,
    is_photo_offer_confirmation,
    is_parts_buyback_request,
    is_non_apple_trade_in_request,
    is_catalog_purchase_advice_request,
    catalog_price_recall_amount,
    trade_in_em_andamento,
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
CATALOG_PRICE_NEGOTIATION_REPLY = (
    "Vou encaminhar sua pergunta sobre o valor desse aparelho para um atendente "
    "confirmar essa negociação com você."
)
CATALOG_PRICE_NEGOTIATION_REASON = "Negociação de preço de aparelho disponível"
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
- Quando o cliente pedir fotos, use get_product_photos. Só envie image_urls que
  vierem do catálogo ou da lista aprovada de fotos; nunca invente links ou use
  fotos de outro modelo. Se não houver foto cadastrada, informe isso sem prometer
  uma foto e ofereça encaminhar ao atendente.
- Quando o cliente perguntar se a loja compra algum produto, responda que sim,
  somente produtos da marca Apple, envie o formulário de avaliação aprovado e
  defina handoff=true.
- Sempre que o cliente perguntar como fica o parcelamento, quanto fica parcelado,
  quais são as parcelas ou pedir uma simulação, use simulate_all_installments e
  envie a tabela completa de 1x até 18x. Isso vale mesmo quando ele mencionar
  uma quantidade específica, como 5x, 6x, 12x ou 18x: a quantidade é uma
  referência para a dúvida, não um filtro para esconder as demais opções.
  Não pergunte em quantas vezes o cliente quer parcelar. Se a pergunta mencionar
  link de pagamento ou cartão online, informe que essa modalidade não é mais aceita
  e não faça simulação ou cálculo de parcelas pelo link.
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


def _has_product_reference(normalized: str) -> bool:
    # A bare number is not enough to identify a product: values such as
    # "1k" (entry) and "18x" (installments) are common in the same chat.
    # Keep numeric shorthand only when it carries a model variant or an
    # explicit catalog condition, such as "16 novo" or "16 lacrado".
    return bool(
        re.search(r"\b(?:iphones?|ipads?|macbooks?|airpods?|apple\s+watch)\b", normalized)
        or re.search(r"\b\d{1,2}\s+(?:e|pro|max|air|mini|plus)\b", normalized)
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


def _is_catalog_followup(text: str) -> bool:
    """Recognize a short price/condition follow-up for a prior product."""
    normalized = _normalize(text)
    if not normalized:
        return False
    return any(
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


def _is_available_list_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if _is_generic_iphone_list_request(normalized):
        return True
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


def _is_generic_iphone_list_request(text: str) -> bool:
    normalized = _normalize(text)
    if not re.search(r"\biphones?\b", normalized):
        return False
    if _requested_iphone_model_keys(normalized):
        return False
    if _is_accessory_catalog_request(normalized):
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
                "entrega",
                "aparelho",
                "unidade",
                "quantos",
                "preciso de",
                "necessito de",
            )
        )
    ):
        return False
    return bool(
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
    if not re.search(r"\blacrados\b", normalized):
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

def _is_product_availability_request(text: str) -> bool:
    """Route a product-specific availability question without an LLM guess."""
    normalized = _normalize(text)
    accessory_request = _is_accessory_catalog_request(normalized)
    bare_model_request = _is_bare_model_availability_request(text)
    if not normalized or (
        not _has_product_reference(normalized)
        and not accessory_request
        and not bare_model_request
    ):
        return False
    if _is_available_list_request(text) or _is_sealed_catalog_list_request(text):
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
    if accessory_request:
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
        "qual o preco",
        "valor",
        "valores",
        "preco",
        "precos",
        "novo",
        "nova",
        "lacrado",
        "lacrados",
        "encomenda",
        "seminovo",
        "seminovos",
        "usado",
        "usados",
    )
    if any(re.search(rf"\b{re.escape(phrase)}\b", normalized) for phrase in availability_phrases):
        return True
    if re.match(r"^(?:iphones?|ipads?|macbooks?|airpods?|apple\s+watch)\b", normalized):
        return True
    has_purchase_intent = any(
        phrase in normalized
        for phrase in (
            "gostaria de ver",
            "gostaria de informacoes sobre",
            "quero ver",
            "quero comprar",
            "procuro",
            "preciso de",
            "necessito de",
        )
    )
    has_broad_filter = any(
        marker in normalized
        for marker in ("faixa de", "ate ", "orcamento", "em torno de", "cerca de", "por volta de")
    )
    return bool(
        (has_purchase_intent or has_broad_filter)
        and re.search(r"\b(?:iphones?|ipads?|macbooks?|airpods?|apple\s+watch)\b", normalized)
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
    return normalized in {"fonte", "carregador"} or bool(
        re.search(
            r"\b(?:tem|vende\w*|possui|disponivel|estoque|a venda|valor|preco|custa|original|tipo|usb|20w)\b",
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
    if not re.search(r"\b(?:fonte|carregador|cabo|acessorio|acessorios)\b", normalized):
        return False
    return bool(
        re.search(
            r"(?:\bvem\s+(?:com|junto|inclus\w*|na\s+caixa)\b|\bacompanha\w*\b|\binclui\w*\b)",
            normalized,
        )
    )


def _requested_capacity_keys(text: str) -> tuple[str, ...]:
    normalized = _normalize(text)
    keys: list[str] = []
    for match in re.finditer(r"\b(\d+(?:[.,]\d+)?)\s*(gb|tb|g)\b", normalized):
        number = match.group(1).replace(",", ".")
        unit = "tb" if match.group(2) == "tb" else "gb"
        if number.endswith(".0"):
            number = number[:-2]
        key = f"{number}{unit}"
        if key not in keys:
            keys.append(key)
    for match in re.finditer(r"\b(1024|512|256|128|64|32)\b", normalized):
        key = f"{match.group(1)}gb"
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def _requested_capacity_key(text: str) -> str | None:
    keys = _requested_capacity_keys(text)
    return keys[0] if keys else None


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
        re.search(r"\bde\s+onde\s+(?:e|fica)\s+(?:a\s+)?loja\b", normalized)
    )


def _is_delivery_or_pickup_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or _has_sealed_reference(normalized):
        return False
    has_delivery = bool(re.search(r"\b(?:entrega|entregam|entregas)\b", normalized))
    has_pickup = bool(
        re.search(r"\bretirad\w*\b", normalized)
        or "retirar na loja" in normalized
        or "buscar na loja" in normalized
        or "pegar na loja" in normalized
    )
    return has_delivery or has_pickup


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


def _is_store_hours_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or "hoje" in normalized:
        return False
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
        re.search(
            r"\b(?:posso|consigo|da para|da pra|gostaria de)\s+"
            r"(?:ir|visitar|passar|comparecer)\b",
            normalized,
        )
        or re.search(
            r"\b(?:posso|consigo)\s+(?:te\s+)?entreg\w*\b.{0,60}\bhoje\b",
            normalized,
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
        or re.search(r"\b(?:[01]?\d|2[0-3])\s*(?::\s*[0-5]\d|h(?:\s*[0-5]\d)?|horas)\b", normalized)
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


def _has_today_visit_offer(history: list[dict[str, str]] | None) -> bool:
    return any(
        entry.get("role") == "assistant"
        and "visita para hoje" in _normalize(entry.get("content", ""))
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
        or _is_sealed_catalog_list_request(text)
        or _is_product_availability_request(text)
        or _is_physical_store_request(text)
    ):
        return False

    if _has_visit_date_reference(text) or _has_visit_time_reference(text):
        return True

    short_reply = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    short_reply = re.sub(r"\s+", " ", short_reply).strip()
    return short_reply in {
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


def _store_hours_reply(faq: FAQStore) -> str:
    hours = _store_hours(faq)
    if "marcad" in _normalize(hours):
        return hours
    return f"{hours} O atendimento \u00e9 feito com hor\u00e1rio marcado."


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


def _has_recent_catalog_product_context(history: list[dict[str, str]] | None) -> bool:
    for entry in reversed(history or []):
        if entry.get("role") != "assistant" or not entry.get("content"):
            continue
        answer = _normalize(entry.get("content", ""))
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
    if not buyer_context and not (has_catalog_context and state_question):
        return False

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
    return any(marker in normalized for marker in ("lacrado", "encomenda"))


def _has_seminovo_reference(normalized: str) -> bool:
    return any(marker in normalized for marker in ("seminovo", "seminovos", "semi novo", "semi novos", "usado", "usados"))


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
    "O cartão de crédito pode ser parcelado em até 18 vezes na máquina física."
)


PAYMENT_ONLY_CREDIT_REPLY = (
    "Sim. A única forma de parcelamento é no cartão de crédito, em até 18 vezes "
    "na máquina física. PIX, dinheiro e cartão de débito são pagamentos à vista, "
    "sem taxas; não parcelamos no boleto ou no PIX."
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


def _is_payment_methods_question(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized or _is_payment_link_request(text):
        return False
    has_cash_method = bool(re.search(r"\b(?:pix|dinheiro|debito)\b", normalized))
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
    ):
        return False
    has_method = bool(re.search(r"\b(?:pix|dinheiro|debito|credito|cartao|cartoes)\b", normalized))
    has_generic_payment_method_phrase = bool(
        re.search(r"\b(?:forma[s]?|metodo[s]?)\s+de\s+pagamento\b", normalized)
    )
    has_short_payment_question = bool(
        re.fullmatch(
            r"(?:pagamento|pagamento\s+como\s+funciona|como\s+funciona(?:\s+o)?\s+pagamento)",
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
    if not normalized or not re.search(r"\b(?:valor|preco|precos|cotacao)\b", normalized):
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


def _extract_budget_limit(text: str) -> float | None:
    """Extract a maximum price from a natural-language budget request."""
    normalized = _normalize(text)
    marker = re.search(
        r"\b(?:ate|no maximo(?: de)?|maximo(?: de)?|na faixa de|faixa de|"
        r"orcamento(?: de)?|em torno de|cerca de|por volta de)\b",
        normalized,
    )
    if not marker:
        return None

    amount_match = re.search(
        r"(?:r\$\s*)?(?P<value>\d+(?:[.,]\d+)?)(?:\s*(?P<scale>mil|k))?",
        normalized[marker.end() :],
    )
    if not amount_match:
        return None
    amount = _parse_brl_amount(amount_match.group("value"))
    if amount is None:
        return None
    # Do not treat delivery deadlines, installment counts, or quantities as
    # prices. This matters when a previous catalog answer is part of the
    # follow-up context, for example: "entrega em até 1 semana".
    suffix = normalized[marker.end() + amount_match.end() :]
    if re.match(
        r"\s*(?:x\b|semanas?\b|dias?\b|horas?\b|mes(?:es)?\b|"
        r"vez(?:es)?\b|parcelas?\b|unidades?\b|aparelhos?\b|"
        r"celulares?\b|telefones?\b|iphones?\b|gb\b|tb\b|%)",
        suffix,
    ):
        return None
    if amount_match.group("scale") and amount < 1000:
        amount *= 1000
    return amount


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
    if _is_installment_rate_question(text) or not _has_installment_rate_prompt(history):
        return False
    return _has_installment_product_context(text)


def _strip_catalog_history_constraints(value: str) -> str:
    cleaned = re.sub(
        r"\b(?:lacrados?|encomendas?|seminovos?|usados?|entregas?|pagamentos?|"
        r"parcel\w*|taxas?|juros|garantia|reserv\w*|endereco|horario|nota\s+fiscal)\b",
        " ",
        _normalize(value),
    )
    return re.sub(r"\s+", " ", cleaned).strip()


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
    if _has_product_reference(_normalize(current)) or _is_case_accessory_request(current):
        return current

    for item in reversed(history or []):
        if item.get("role") == "assistant" and any(
            marker in _normalize(item.get("content", "")) for marker in ("iphone", "ipad", "produto")
        ):
            assistant_context = item.get("content", "")
            if strip_assistant_constraints:
                assistant_context = _strip_catalog_history_constraints(assistant_context)
            return f"{assistant_context}\n{current}".strip()
    previous_user_text = [
        item.get("content", "").strip()
        for item in (history or [])
        if item.get("role") == "user" and item.get("content", "").strip()
    ]
    return "\n".join([*previous_user_text[-4:], current]).strip()


def _product_context_query(
    text: str,
    history: list[dict[str, str]] | None,
    *,
    strip_assistant_constraints: bool = False,
) -> str:
    """Preserva a referência do produto atual ao consultar o histórico."""
    current = text.strip()
    normalized = _normalize(current)
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
    if not image_anchor:
        for index, role, content in reversed(entries):
            if index >= anchor_index and role == "assistant" and is_specific_product_answer(content):
                context_content = re.sub(
                    r"\b(?:pelicula|capa|capinha|case|fonte|cabo|carregador|protetor|suporte)s?\b",
                    " ",
                    _normalize(content),
                ).strip()
                if strip_assistant_constraints:
                    context_content = _strip_catalog_history_constraints(context_content)
                if context_content and context_content not in parts:
                    parts.append(context_content)
                break
    for index, role, content in entries:
        if index >= anchor_index and role == "user" and content not in parts:
            parts.append(content)
    parts.append(current)
    return "\n".join(parts[-6:]).strip()


def _extract_bare_catalog_model_reference(text: str) -> str | None:
    """Turn follow-ups such as "a bateria do 15" into an explicit model."""
    pattern = re.compile(
        r"\b(?:do|da|dos|das|de|no|na|o|a|um|uma|esse|essa|este|esta)\s+"
        r"(?:iphone\s*)?(?P<number>\d{1,2})"
        r"(?P<variant>\s*(?:e|pro\s+max|pro|max|plus|mini|air))?\b",
        flags=re.IGNORECASE,
    )
    matches = list(pattern.finditer(text or ""))
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


def _battery_detail_context_query(text: str, history: list[dict[str, str]] | None) -> str | None:
    normalized = _normalize(text)
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


def _has_photo_request_in_history(history: list[dict[str, str]] | None) -> bool:
    photo_words = ("foto", "fotos", "imagem", "imagens")
    return any(
        entry.get("role") == "user"
        and any(word in _normalize(entry.get("content", "")) for word in photo_words)
        for entry in (history or [])
    )


def _is_photo_context_followup(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Keep a previous photo request active while the customer clarifies it."""
    if not _has_photo_request_in_history(history):
        return False
    normalized = _normalize(text)
    if not normalized:
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
    return bool(
        re.fullmatch(
            r"(?:eu\s+)?vi\s+que\s+(?:voce|voces|vc)\s+tem",
            normalized,
        )
        or re.fullmatch(
            r"(?:eu\s+)?vi\s+(?:ali\s+)?que\s+tem\s+(?:um\s+)?"
            r"(?:iphone\s*)?\d{1,2}"
            r"(?:\s+(?:pro\s+max|pro|max|plus|mini|air|e))?"
            r"(?:\s+[a-z]+)?",
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


def _format_product_availability(items: list[Any]) -> str:
    if not items:
        return "No momento não localizei esse produto no catálogo."

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
    formatted_lines = [lines[0]]
    separator = "\u2014"
    for item, line in zip(items, lines[1:]):
        item_name = str(getattr(item, "name", None) or "produto").strip()
        _, _, details = line.partition(" ")
        formatted_lines.append(f"\u2022 {item_name} {separator} {details}")
    return "\n".join(formatted_lines)


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
        combined_request = " ".join(part for part in (text, image_description) if part)
        if is_parts_buyback_request(combined_request):
            return AgentDecision(reply=PARTS_BUYBACK_REPLY, confidence="high")
        if is_non_apple_trade_in_request(combined_request):
            return AgentDecision(reply=NON_APPLE_TRADE_IN_REPLY, confidence="high")
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

        payment_link_decision = await self._try_payment_link(text, history)
        if payment_link_decision is not None:
            return protect_customer_decision(payment_link_decision)

        price_policy_decision = self._try_price_policy(text)
        if price_policy_decision is not None:
            return protect_customer_decision(price_policy_decision)

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

        current_day_decision = self._try_current_day_information(text)
        if current_day_decision is not None:
            return protect_customer_decision(current_day_decision)

        store_hours_decision = self._try_store_hours(text)
        if store_hours_decision is not None:
            return protect_customer_decision(store_hours_decision)

        installment_rate_decision = self._try_installment_rate_question(text)
        if installment_rate_decision is not None:
            return protect_customer_decision(installment_rate_decision)

        accessory_inclusion_decision = self._try_accessory_inclusion_information(text)
        if accessory_inclusion_decision is not None:
            return protect_customer_decision(accessory_inclusion_decision)

        rate_followup_decision = await self._try_rate_model_followup(text, history)
        if rate_followup_decision is not None:
            return protect_customer_decision(rate_followup_decision)

        case_accessory_decision = self._try_case_accessory_information(text)
        if case_accessory_decision is not None:
            return protect_customer_decision(case_accessory_decision)

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

    def _try_installment_rate_question(self, text: str) -> AgentDecision | None:
        if not _is_installment_rate_question(text):
            return None
        return AgentDecision(
            reply=format_installment_rates(),
            confidence="high",
        )

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

    def _try_payment_methods(self, text: str) -> AgentDecision | None:
        if not _is_payment_methods_question(text):
            return None

        if _is_credit_only_installment_question(text):
            reply = PAYMENT_ONLY_CREDIT_REPLY
        else:
            reply = self.faq.get("pagamento") or PAYMENT_METHODS_REPLY
        normalized = _normalize(text)
        if re.search(r"\b(?:dois|duas)\s+cartoes?\s+de\s+credito\b", normalized) or "mais de um cartao" in normalized:
            reply = (
                "Sim 😊 Você pode usar dois cartões de crédito na mesma compra e completar o valor "
                f"com PIX, dinheiro ou cartão de débito. {reply}"
            )
        return AgentDecision(
            reply=self._append_delivery_or_pickup_info(reply, text),
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

    def _try_delivery_or_pickup(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
    ) -> AgentDecision | None:
        delivery_followup = _is_delivery_followup_request(text, history)
        if (
            not (_is_delivery_or_pickup_request(text) or delivery_followup)
            or _is_explicit_human_request(text)
            or _is_physical_store_request(text)
        ):
            return None
        reply = _delivery_or_pickup_reply(self.faq, text, force_delivery=delivery_followup)
        if not reply:
            return None
        return AgentDecision(reply=reply, confidence="high")

    async def _try_available_products(self, text: str) -> AgentDecision | None:
        sealed_only = _is_sealed_catalog_list_request(text)
        if not (_is_available_list_request(text) or sealed_only):
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
                return None
            result = {
                "encontrado": True,
                "seminovos": [],
                "lacrados_pronta_entrega": lacrados_pronta_entrega,
                "lacrados": lacrados,
            }
        if _is_generic_iphone_list_request(text):
            normalized = _normalize(text)
            other_family_requested = bool(
                re.search(r"\b(?:ipads?|macbooks?|airpods?|apple\s+watch)\b", normalized)
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
        return AgentDecision(reply=_format_available_products(result), confidence="high")

    def _try_case_accessory_information(self, text: str) -> AgentDecision | None:
        if not _is_case_accessory_request(text):
            return None
        return AgentDecision(
            reply=self.faq.get("capinhas") or CASE_ACCESSORY_REPLY,
            confidence="high",
        )

    def _try_accessory_inclusion_information(self, text: str) -> AgentDecision | None:
        if not _is_accessory_inclusion_question(text):
            return None

        normalized = _normalize(text)
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
        if not _is_battery_detail_request(text):
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
        if not candidates:
            alternative = await self._try_unavailable_seminew_alternative(
                query,
                requested_label=_extract_bare_catalog_model_reference(text),
            )
            if alternative is not None:
                return alternative
            requested_label = _extract_bare_catalog_model_reference(text) or "esse aparelho"
            return AgentDecision(
                reply=(
                    f"Não localizei o {requested_label} disponível no estoque no momento. "
                    "Não consigo confirmar a saúde da bateria sem uma unidade cadastrada."
                ),
                confidence="medium",
            )

        selected = max(
            candidates,
            key=lambda item: (
                _catalog_score(query, item),
                _normalize(str(getattr(item, "name", "") or "")),
                _normalize(str(getattr(item, "capacity", "") or "")),
            ),
        )
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
        return AgentDecision(
            reply=(
                f"Sobre o {label}: {battery_reply} O cadastro não informa se a bateria é original "
                "ou se já foi trocada, então não consigo confirmar essa parte por aqui."
            ),
            product_references=[str(getattr(selected, "external_id", ""))],
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
    ) -> AgentDecision | None:
        normalized_query = _normalize(query)
        if not _has_sealed_reference(normalized_query):
            return None

        method = getattr(self.cache, "list_available_products", None)
        if not callable(method):
            return None
        try:
            result = await method()
        except Exception:
            return None

        seminovos = result.get("seminovos") or []
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
        other_lacrados = [
            entry
            for entry in lacrados
            if same_family(entry) and not same_requested_model(entry) and within_budget(entry)
        ]
        alternatives = {
            "seminovos": [*matching_seminovos, *other_seminovos],
            "lacrados": other_lacrados,
        }
        if not alternatives["seminovos"] and not alternatives["lacrados"]:
            return AgentDecision(
                reply=(
                    "N\u00e3o localizei esse modelo novo lacrado na tabela de lacrados. "
                    "Tamb\u00e9m n\u00e3o encontrei outra op\u00e7\u00e3o cadastrada para sugerir agora. "
                    "Se quiser, me diga outro modelo ou capacidade."
                ),
                confidence="medium",
            )

        return AgentDecision(
            reply=(
                "N\u00e3o localizei esse modelo novo lacrado na tabela de lacrados. "
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

        if _has_sealed_reference(_normalize(query)):
            return None

        method = getattr(self.cache, "list_available_products", None)
        if not callable(method):
            return None
        try:
            result = await method()
        except Exception:
            return None

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
    ) -> AgentDecision | None:
        # A short clarification after a photo request is still a photo
        # selection, even when it contains availability wording such as
        # "tem um 11 verde". Let the photo resolver preserve that context.
        if _is_photo_context_followup(text, history):
            return None
        current_query = _current_catalog_context(text, image_description)
        if _is_bare_model_availability_request(current_query):
            bare_model = _extract_bare_catalog_model_reference(current_query)
            if bare_model:
                current_query = f"{bare_model} {current_query}".strip()
        query = current_query
        if (
            history
            and not _has_product_reference(_normalize(current_query))
            and not _is_accessory_catalog_request(current_query)
            and not _is_case_accessory_request(current_query)
            and (
                _is_catalog_followup(current_query)
                or _is_catalog_availability_confirmation(current_query, history)
            )
        ):
            query = _product_context_query(
                current_query,
                history,
                strip_assistant_constraints=True,
            )
        if not _is_product_availability_request(query):
            return None

        requested_budget = _extract_budget_limit(query)
        requested_quantity = _requested_device_quantity(query)
        try:
            candidates = await self.cache.search(query, limit=300)
        except Exception:
            alternative = await self._try_unavailable_lacrado_alternative(
                query, requested_budget=requested_budget
            )
            if alternative is not None:
                return alternative
            return None

        public_candidates = [
            item
            for item in candidates
            if (
                _is_sealed_accessory_item(item)
                if _is_accessory_catalog_request(query)
                else _is_device_item(item)
            )
            and (getattr(item, "source", None) != "mercado_phone" or _is_available_item(item))
        ]
        public_candidates = [item for item in public_candidates if _matches_requested_model(query, item)]
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

        requested_capacities = _requested_capacity_keys(query)
        requested_models = _requested_iphone_model_keys(query)
        if requested_capacities:
            public_candidates = [
                item
                for item in public_candidates
                if _capacity_key(getattr(item, "capacity", None) or getattr(item, "name", ""))
                in requested_capacities
            ]

        normalized_query = _normalize(query)
        requested_conditions: set[str] = set()
        if _has_sealed_reference(normalized_query):
            requested_conditions.add("lacrado")
        if _has_seminovo_reference(normalized_query):
            requested_conditions.add("seminovo")

        if not public_candidates:
            alternative = await self._try_unavailable_lacrado_alternative(
                query, requested_budget=requested_budget
            )
            if alternative is not None:
                return alternative
            if requested_budget is None:
                alternative = await self._try_unavailable_seminew_alternative(query)
                if alternative is not None:
                    return alternative
            capacity_text = (
                f" {', '.join(value.upper() for value in requested_capacities)}"
                if requested_capacities
                else ""
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

        broad_request = (
            requested_budget is not None
            or requested_quantity is not None
            or _is_broad_airpods_request(query)
        )
        if broad_request:
            def price_sort_key(item: Any) -> tuple[float, str, str, str]:
                price = getattr(item, "price_brl", None)
                try:
                    numeric_price = float(price) if price is not None else float("inf")
                except (TypeError, ValueError):
                    numeric_price = float("inf")
                return (
                    numeric_price,
                    _normalize(str(getattr(item, "name", "") or "")),
                    _normalize(str(getattr(item, "capacity", "") or "")),
                    _normalize(str(getattr(item, "color", None) or getattr(item, "colors", "") or "")),
                )

            selected = sorted(public_candidates, key=price_sort_key)
        else:
            scored = [(_catalog_score(query, item), item) for item in public_candidates]
            best_score = max(score for score, _item in scored)
            if best_score <= 0:
                alternative = await self._try_unavailable_lacrado_alternative(
                    query, requested_budget=requested_budget
                )
                if alternative is not None:
                    return alternative
                if requested_budget is None:
                    alternative = await self._try_unavailable_seminew_alternative(query)
                    if alternative is not None:
                        return alternative
                return AgentDecision(
                    reply="No momento não localizei esse produto no catálogo. Pode me informar o modelo ou capacidade?",
                    confidence="medium",
                )

            return_all_matching_units = (
                len(requested_models) == 1
                and bool(requested_conditions)
                and requested_quantity is None
                and _requested_battery_health(query) is None
                and not _has_requested_catalog_color(query, public_candidates)
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

            condition_was_requested = _has_sealed_reference(_normalize(query)) or _has_seminovo_reference(
                _normalize(query)
            )

            def select_best_matches(scored_items: list[tuple[int, Any]]) -> list[Any]:
                if condition_was_requested:
                    return best_matches(scored_items)

                grouped: dict[str, list[tuple[int, Any]]] = {
                    "seminovo": [],
                    "lacrado": [],
                }
                for score, item in scored_items:
                    condition = "lacrado" if _is_sealed_item(item) else "seminovo"
                    grouped[condition].append((score, item))

                selected_by_condition: list[Any] = []
                for condition in ("seminovo", "lacrado"):
                    selected_by_condition.extend(best_matches(grouped[condition]))
                return selected_by_condition or best_matches(scored_items)

            def select_requested_model_matches(scored_items: list[tuple[int, Any]]) -> list[Any]:
                if len(requested_models) <= 1:
                    return select_best_matches(scored_items)

                selected_by_model: list[Any] = []
                for requested_model in requested_models:
                    model_matches = [
                        (score, item)
                        for score, item in scored_items
                        if _model_key(getattr(item, "name", "")) == requested_model
                    ]
                    selected_by_model.extend(select_best_matches(model_matches))
                return selected_by_model

            if return_all_matching_units:
                selected = [
                    item
                    for score, item in scored
                    if score > 0 and condition_matches(item)
                ]
            elif len(requested_capacities) > 1:
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

        reply = _format_product_availability(selected)
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
        return AgentDecision(
            reply=_store_hours_reply(self.faq),
            confidence="high",
        )

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
        if (
            is_followup
            and _has_visit_time_reference(context)
            and not _has_visit_date_reference(context)
            and _has_today_visit_offer(history)
        ):
            context = f"hoje\n{context}"
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

        if "amanha" in _normalize(text):
            reply = _tomorrow_store_reply(self.faq)
            return AgentDecision(reply=reply, confidence="high")

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
        if _is_photo_retry_request(text) and not _has_photo_request_in_history(history):
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
        if _is_photo_context_followup(text, history) and not _has_product_reference(
            _normalize(current_query)
        ):
            followup_model = _extract_bare_catalog_model_reference(current_query)
            if followup_model:
                current_query = f"{followup_model} {current_query}".strip()
        query = _product_context_query(current_query, history)
        # A previous assistant answer can list several capacities. When the
        # current turn contains one explicit choice, make that choice the
        # first capacity seen by the photo resolver instead of letting the
        # historical list select its first entry.
        explicit_capacity_keys = _requested_capacity_keys(text)
        if len(explicit_capacity_keys) != 1:
            explicit_capacity_keys = _requested_capacity_keys(image_description or "")
        if len(explicit_capacity_keys) == 1:
            query = f"capacidade solicitada {explicit_capacity_keys[0]}\n{query}".strip()
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
        requested_condition = _requested_photo_condition(query)
        finder = getattr(self.cache, "find_product_photos", None)

        def condition_matches(item: Any) -> bool:
            if requested_condition is None:
                return True
            is_sealed = _is_sealed_item(item)
            return is_sealed if requested_condition == "sealed" else not is_sealed

        def exact_candidates(items: list[Any]) -> list[Any]:
            requested_capacities = _requested_capacity_keys(query)
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

            scored = [(_catalog_score(query, item), item) for item in candidates]
            positive = [(score, item) for score, item in scored if score > 0]
            if not positive:
                return []
            best_score = max(score for score, _item in positive)
            return [item for score, item in positive if score == best_score]

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

        if callable(finder):
            try:
                selected = await finder(query)
            except Exception:
                return None
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
        if not result.get("ambiguo"):
            alternative = await self._try_unavailable_seminew_alternative(query)
            if alternative is not None:
                return alternative
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
        if not (_is_full_installment_request(text) or _is_rate_model_followup(text, history)):
            return None
        method = getattr(self.cache, "simulate_all_installments", None)
        if not callable(method):
            return None
        try:
            result = await method(_installment_context_query(text, history))
        except Exception:
            return None
        if not result.get("encontrado"):
            return await self._try_unavailable_seminew_alternative(
                _installment_context_query(text, history)
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
        and is_purchase_without_trade_in_request(request_context)
        and _looks_like_trade_in_handoff(decision)
    ):
        return decision.model_copy(
            update={
                "reply": PURCHASE_WITHOUT_TRADE_IN_REPLY,
                "handoff": False,
                "handoff_reason": None,
                "confidence": "high",
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
