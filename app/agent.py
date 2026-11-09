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
    simulate_installment_table_with_entry,
    simulate_installment_with_entry,
)
from app.safety import protect_customer_decision
from app.schemas import AgentDecision, InventoryItem, TurnInterpretation
from app.trade_in import (
    CONDITION_HANDOFF_REASON,
    CONDITION_HANDOFF_REPLY,
    DOWNGRADE_EVALUATION_REPLY,
    DOWNGRADE_MODEL_QUESTION,
    NON_APPLE_TRADE_IN_REPLY,
    PARTS_BUYBACK_REPLY,
    PURCHASE_WITHOUT_TRADE_IN_REPLY,
    TRADE_IN_FORM,
    TRADE_IN_NEGOTIATION_REPLY,
    TRADE_IN_REASON,
    is_trade_in_negotiation,
    is_completed_trade_in_form,
    is_unlabeled_trade_in_form_response,
    is_downgrade_request,
    is_box_inclusion_question,
    is_pending_trade_in_evaluation_followup,
    is_trade_in_context_request,
    is_trade_in_request,
    has_explicit_trade_in_cue,
    is_photo_offer_confirmation,
    is_parts_buyback_request,
    is_non_apple_trade_in_request,
    trade_in_catalog_target,
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
- Se o cliente perguntar se um valor corresponde à opção seminova, confira no
  catálogo o mesmo produto disponível pelo valor exato antes de confirmar. Se não
  houver correspondência única, ou o valor corresponder a outra condição, peça
  esclarecimento. Depois de confirmar, responda diretamente sem acrescentar
  informação sobre uma opção lacrada que não tenha sido conferida.
- Não encaminhe ao atendente só para repetir uma condição já confirmada no
  catálogo. Encaminhe perguntas sobre estado físico, reparos ou marcas de uso que
  não constem no cadastro.
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
  aguarde o cliente preencher o formulário, sem transferir antes.
- Quando o cliente perguntar se fazemos downgrade de iPhone, confirme que sim,
  pergunte qual modelo de iPhone deseja pegar e envie o formulário de avaliação
  do aparelho atual. Aguarde o preenchimento antes de encaminhar ao atendente.
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
  aguarde o cliente devolvê-lo preenchido. Quando receber a avaliação preenchida,
  encaminhe a conversa ao atendente sem reenviar o formulário.
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
- Quando receber um bloco "Interpretação contextual do turno", use-o para separar
  pedidos atuais, interesses futuros/hipotéticos e sugestões recusadas. Ele resume
  a intenção e não fornece fatos de estoque, preço ou política da loja. Confira
  qualquer informação comercial nas ferramentas aprovadas. Se a interpretação
  divergir claramente das palavras do cliente, priorize o texto do cliente ou
  pergunte para esclarecer.
- Um modelo citado como possibilidade futura pode ser útil para uma comparação ou
  consulta informativa, mas não deve ser apresentado como a escolha atual do cliente.

O campo reply deve ser exatamente a mensagem que será enviada ao cliente.
""".strip()


TURN_INTERPRETER_INSTRUCTIONS = """
Leia o histórico recente e o turno atual do cliente como uma conversa, inclusive
quando o turno reunir várias mensagens curtas do WhatsApp. Devolva somente a
interpretação estruturada solicitada.

Separe intenções simultâneas e diferencie: pedido para agir agora, possibilidade
futura/hipotética e sugestão anterior recusada. Preserve modelos, capacidades,
cores, condições, valores de entrada e prazo que o cliente realmente mencionou.
Não transforme uma possibilidade futura em pedido atual. Não conclua estoque,
preço, parcelamento, garantia ou política da loja; esses fatos vêm de outras
fontes. Não classifique compra de aparelho como avaliação/troca sem sinais de que
o cliente oferece um aparelho próprio. Se o pedido atual ainda puder ter mais de
uma leitura plausível, marque needs_clarification e escreva uma pergunta curta em
português brasileiro.
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


def _should_use_turn_interpreter(
    text: str,
    history: list[dict[str, str]] | None,
) -> bool:
    """Select turns where the regex routes can benefit from contextual reading."""
    normalized = _normalize(text)
    if not normalized:
        return False

    # Preserve the dedicated evaluation workflow and its exact state transitions.
    if (
        trade_in_em_andamento(history)
        or is_completed_trade_in_form(text)
        or is_unlabeled_trade_in_form_response(text, history)
        or is_downgrade_request(text)
        or is_non_apple_trade_in_request(text)
        or trade_in_catalog_target(text)
        or is_trade_in_request(text)
        or is_trade_in_context_request(text, history)
    ):
        return False

    model_keys = set(_requested_iphone_model_keys(text))
    has_ambiguity_marker = bool(
        re.search(
            r"\b(?:nao|nem|mas|porem|talvez|quem\s+sabe|se|caso|"
            r"depois|mais\s+tarde|futuro|futura|ano\s+que\s+vem|"
            r"fim\s+do\s+ano|mais\s+perto|na\s+verdade|em\s+vez\s+de|"
            r"ou|tambem|outro|outra)\b",
            normalized,
        )
    )
    if len(model_keys) > 1 and (has_ambiguity_marker or len(text.splitlines()) > 1):
        return True

    # Conflicting service cues often arrive together in one WhatsApp batch.
    intent_cues = (
        bool(re.search(r"\b(?:foto|fotos|imagem|imagens)\b", normalized)),
        bool(re.search(r"\b(?:parcel\w*|entrada|sinal|cartao|parcelas?)\b", normalized)),
        bool(_has_product_reference(normalized)),
        bool(re.search(r"\b(?:entrega|retirada|visita|endereco|horario)\b", normalized)),
    )
    if sum(intent_cues) >= 2:
        return True

    words = normalized.split()
    last_assistant = next(
        (
            _normalize(entry.get("content", ""))
            for entry in reversed(history or [])
            if entry.get("role") == "assistant" and entry.get("content", "").strip()
        ),
        "",
    )
    is_short_contextual_reply = (
        len(words) <= 7
        and bool(
            re.search(r"\b(?:sim|nao|esse|essa|isso|aquele|aquela|outro|outra)\b", normalized)
        )
        and "?" in last_assistant
        and _has_recent_catalog_product_context(history)
    )
    return is_short_contextual_reply


_IMPLICIT_DEVICE_BUYBACK_PRICE_RE = re.compile(
    r"\bquanto\s+(?:dari\w*|pagari\w*|pegari\w*)\s+"
    r"(?:em|no|na|num|numa|por|pelo|pela)\s+"
    r"(?:(?:um|uma|o|a)\s+)?(?:iphone\s*)?1[0-8]\b",
    re.IGNORECASE,
)


def _is_iphone_17_catalog_list_request(text: str) -> bool:
    normalized = _normalize(text or "")
    return bool(
        re.search(r"\b17\b", normalized)
        and re.search(r"\bpro\s+max\b", normalized)
        and re.search(r"\b(?:disponivel|disponibilidade|estoque)\b", normalized)
        and re.search(r"\b(?:compr\w*|quer\w*)\b", normalized)
    )


def _unanswered_iphone_17_catalog_request(
    history: list[dict[str, str]] | None,
) -> str | None:
    entries = (history or [])[-8:]
    candidate_index = next(
        (
            index
            for index in range(len(entries) - 1, -1, -1)
            if entries[index].get("role") == "user"
            and _is_iphone_17_catalog_list_request(entries[index].get("content", ""))
        ),
        None,
    )
    if candidate_index is None:
        return None

    for entry in entries[candidate_index + 1 :]:
        if entry.get("role") != "assistant":
            continue
        answer = _normalize(entry.get("content", ""))
        if (
            re.search(r"\biphone\s+17\b", answer)
            and re.search(
                r"\b(?:disponivel|disponibilidade|opcoes?|estoque|lacrado)\b|\br\$",
                answer,
            )
        ):
            return None
    return entries[candidate_index].get("content") or None


def _is_broad_iphone_17_catalog_request(text: str) -> bool:
    normalized = _normalize(text or "")
    has_line_request = bool(
        re.search(
            r"\b(?:para\s+cada|linha\s+(?:completa|17)|todos?\s+(?:os\s+)?modelos)\b",
            normalized,
        )
        or re.search(r"\bquais\s+modelos\s+(?:de\s+)?(?:iphone\s*)?17\b", normalized)
    )
    has_specific_filter = bool(
        _requested_capacity_keys(text)
        or _requested_catalog_colors(text, [])
        or re.search(r"\b(?:lacrad\w*|seminov\w*|usad\w*|novo|nova)\b", normalized)
    )
    return has_line_request and not has_specific_filter


def _has_iphone_17_pro_comparison_request(text: str) -> bool:
    normalized = _normalize(text or "")
    return bool(
        re.search(
            r"\bpro\b.{0,35}\bpro\s+max\b|\bpro\s+max\b.{0,35}\bpro\b",
            normalized,
        )
    )


def _combined_iphone_17_catalog_request(
    text: str,
    history: list[dict[str, str]] | None = None,
) -> str | None:
    """Recover the catalog part when an iPhone 17 request and buyback are combined."""
    buyback_match = _IMPLICIT_DEVICE_BUYBACK_PRICE_RE.search(text or "")
    if not buyback_match:
        return None

    catalog_request = (text or "")[: buyback_match.start()]
    buyback_request = (text or "")[buyback_match.start() :]
    if not is_trade_in_request(buyback_request):
        return None
    if _is_iphone_17_catalog_list_request(catalog_request):
        return catalog_request
    return _unanswered_iphone_17_catalog_request(history)


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






_SEMINOVO_TRADE_IN_CATALOG_TARGET_RE = re.compile(
    r"\b(?:procur(?:ando|ava|ar|o|a)|busc(?:ando|ava|ar|o|a)|"
    r"quer(?:endo|ia|o|er)|gostaria\s+de)\b.{0,48}?"
    r"\b(?:semi(?:[\s-]*nov[oa]s?)?|usad[oa]s?)\b\s+"
    r"(?P<models>\d{1,2}(?:\s*(?:pro\s*max|pro|max|plus|mini|air|e|se))?"
    r"(?:\s*/\s*\d{1,2}(?:\s*(?:pro\s*max|pro|max|plus|mini|air|e|se))?)?)"
    r"(?=\s*(?:para|pra|na)\s+troca\b|\s*(?:[,.;!?]|$))",
    re.IGNORECASE,
)




















































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
    catalog_followup = (
        _is_bare_model_catalog_followup(text, history)
        or _pending_iphone_variant_catalog_context(text, history) is not None
        or (
            _is_catalog_price_followup(text)
            and _has_recent_catalog_product_context(history)
        )
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














_COMMON_CATALOG_COLOR_NAMES = (
    "azul claro",
    "azul escuro",
    "azul intenso",
    "azul ultramarino",
    "roxo profundo",
    "preto espacial",
    "titânio natural",
    # Customers often shorten the titanium color to just "natural".
    "natural",
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


_SEMINOVO_PRICE_CONFIRMATION_RE = re.compile(
    r"^(?:no\s+valor(?:\s+de)?|por)\s+(?:r\$\s*)?\d[\d.,]*\s+"
    r"(?:__copula__|e)\s+(?:um\s+)?(?:semi[\s-]+nov[oa]|usad[oa])"
    r"(?:\s+entao)?(?:[,;]?\s+(?:sem\s+problema|ok|tudo\s+bem|"
    r"certo|correto|perfeito))*$",
    re.IGNORECASE,
)


def _known_seminovo_price_confirmation_query(
    text: str | None,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Return the latest specific seminovo offer for a price confirmation."""
    normalized = _normalize(text or "").strip(" .!?\t\r\n")
    if (
        not normalized
        or not history
        or not _SEMINOVO_PRICE_CONFIRMATION_RE.fullmatch(normalized)
    ):
        return None
    if trade_in_em_andamento(history) or is_trade_in_context_request(text, history):
        return None

    latest_assistant = next(
        (
            entry.get("content", "")
            for entry in reversed(history)
            if entry.get("role") == "assistant" and entry.get("content", "").strip()
        ),
        "",
    )
    if not latest_assistant or history[-1].get("role") != "assistant":
        return None

    for sentence in re.split(r"(?<=[.!?])\s+|\r?\n", latest_assistant):
        normalized_sentence = _normalize(sentence)
        if (
            _has_product_reference(normalized_sentence)
            and _has_seminovo_reference(normalized_sentence)
            and re.search(
                r"\b(?:disponivel|estoque|consta|encontrei|temos|r\$)\b",
                normalized_sentence,
            )
        ):
            return re.sub(
                r"\b(?:seminov[oa]s?|semi[\s-]+nov[oa]s?|usad[oa]s?)\b.*$",
                "",
                normalized_sentence,
            ).strip()
    return None


def _catalog_offer_model_markers(query: str) -> tuple[str, ...]:
    """Extract non-iPhone model markers needed to distinguish catalog variants."""
    normalized = _normalize(query)
    family_match = re.search(
        r"\b(?:ipad|macbook|airpods?|apple\s+watch)\b",
        normalized,
    )
    if family_match is None:
        return ()

    product_name = re.split(
        r"\b(?:seminov[oa]s?|semi[\s-]+nov[oa]s?|usad[oa]s?|"
        r"lacrad\w*|nov\w*|disponivel|estoque|r\$|bateria)\b",
        normalized[family_match.end() :],
        maxsplit=1,
    )[0]
    return tuple(
        dict.fromkeys(
            re.findall(
                r"\b(?:pro|max|mini|plus|air|se|ultra|m\d{1,2}|"
                r"a\d{1,2}|\d{1,2})\b",
                product_name,
            )
        )
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






_SHORT_IPHONE_MODEL_SELECTION_RE = re.compile(
    r"^iphone\s+\d{1,2}(?:\s+(?:pro\s+max|pro|max|plus|mini|air|e))?"
    r"(?:\s+\d{2,4}\s*gb)?\s*[?!.]*$",
    re.IGNORECASE,
)














































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




def _active_photo_price_request(
    text: str,
    history: list[dict[str, str]] | None,
) -> str:
    if _is_photo_request(text):
        return text
    if not _is_photo_context_followup(text, history):
        return ""
    return next(
        (
            entry.get("content", "").strip()
            for entry in reversed(history or [])
            if entry.get("role") == "user"
            and _is_photo_request(entry.get("content", ""))
        ),
        "",
    )


def _photo_price_selector_context(
    text: str,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Recover only the prior offer rows selected by a customer's photo prices."""
    price_request = _active_photo_price_request(text, history)
    requested_prices = _photo_price_selectors(price_request)
    if not requested_prices:
        return None

    entries = [
        (index, entry.get("content", ""))
        for index, entry in enumerate(history or [])
        if entry.get("role") == "assistant"
    ]
    selected_lines: dict[int, set[int]] = {}
    line_cache: dict[int, list[str]] = {}
    for requested_price in requested_prices:
        selected_entry: int | None = None
        selected_indexes: set[int] = set()
        for index, content in reversed(entries):
            lines = content.splitlines()
            matching_indexes = {
                line_index
                for line_index, line in enumerate(lines)
                if requested_price in _photo_price_selectors(line)
            }
            if matching_indexes:
                selected_entry = index
                selected_indexes = matching_indexes
                line_cache[index] = lines
                break
        if selected_entry is None:
            return None
        selected_lines.setdefault(selected_entry, set()).update(selected_indexes)

    context_lines: list[str] = []
    requested_price_set = set(requested_prices)
    offer_price_pattern = re.compile(
        r"\br\s*\$\s*(?P<amount>\d{1,3}(?:[.,]\d{3})*(?:,\d{2})?|"
        r"\d{4,}(?:[.,]\d{2})?)",
        flags=re.IGNORECASE,
    )

    def keep_requested_price(match: re.Match[str]) -> str:
        amount = _parse_brl_amount(match.group("amount"))
        if amount is not None and round(amount * 100) in requested_price_set:
            return match.group(0)
        return ""

    for index in sorted(selected_lines):
        lines = line_cache[index]
        chosen_indexes = selected_lines[index]
        for line_index in sorted(chosen_indexes):
            line = lines[line_index].strip()
            line = offer_price_pattern.sub(keep_requested_price, line)
            line = re.sub(r"\s+", " ", line).strip()
            if not _has_product_reference(_normalize(line)):
                heading = next(
                    (
                        candidate.strip()
                        for candidate in reversed(lines[:line_index])
                        if _has_product_reference(_normalize(candidate))
                    ),
                    "",
                )
                if heading:
                    line = f"{heading}\n{line}"
            if line and line not in context_lines:
                context_lines.append(line)

    context_lines.extend([price_request, text])
    return "\n".join(dict.fromkeys(line for line in context_lines if line.strip()))




def _product_context_query(
    text: str,
    history: list[dict[str, str]] | None,
    *,
    strip_assistant_constraints: bool = False,
) -> str:
    """Preserva a referência do produto atual ao consultar o histórico."""
    current = text.strip()
    normalized = _normalize(current)
    selected_row_context = _selected_catalog_row_context(current, history)
    if selected_row_context:
        return selected_row_context
    price_selector_context = _photo_price_selector_context(current, history)
    if price_selector_context:
        return price_selector_context
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

    entries: list[tuple[int, str | None, str]] = []
    for index, item in enumerate(history or []):
        content = item.get("content", "").strip()
        if not content:
            continue
        role = item.get("role")
        if role == "user" and is_trade_in_context_request(
            content,
            (history or [])[:index],
        ):
            # The customer's trade-in phone is not the catalog product they
            # are considering. Keep an explicit upgrade target when present.
            content = trade_in_catalog_target(content) or ""
            if not content:
                continue
        entries.append((index, role, content))
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


def _pending_iphone_variant_catalog_context(
    text: str,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Resolve a Pro Max mention against one recently offered base iPhone model."""
    normalized = _normalize(text)
    if (
        not normalized
        or not history
        or _has_product_reference(normalized)
        or _requested_iphone_model_keys(text)
        or _is_photo_request(text)
        or _is_accessory_catalog_request(text)
        or is_trade_in_request(text)
    ):
        return None

    has_variant_followup = bool(
        re.fullmatch(
            r"(?:(?:e|ou|nem|tem|teria)\s+)*(?:o\s+)?pro\s+max[?!.,]*",
            normalized,
        )
        or re.search(
            r"(?:[?!.,;]\s*|\b(?:e|ou|nem)\s+)"
            r"(?:o\s+)?pro\s+max[?!.,]*(?:$|\s)",
            normalized,
        )
    )
    if not has_variant_followup or not _has_recent_catalog_product_context(history):
        return None

    latest_assistant_answer = next(
        (
            entry.get("content", "").strip()
            for entry in reversed(history)
            if entry.get("role") == "assistant" and entry.get("content", "").strip()
        ),
        "",
    )
    offered_models = _requested_iphone_model_keys(latest_assistant_answer)
    if (
        len(offered_models) != 1
        or not isinstance(offered_models[0][0], int)
        or offered_models[0][1]
    ):
        return None

    generation = offered_models[0][0]
    condition = _product_interest_default_condition(latest_assistant_answer)
    model = f"iPhone {generation} Pro Max"
    if condition:
        model += f" {condition}"
    return f"{model}\n{text.strip()}"










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








_BARE_MODEL_SELECTION_REPLY_RE = re.compile(
    r"^(?:iphone\s*)?(?P<number>\d{1,2})"
    r"(?:\s*(?P<e>e)|\s+(?P<variant>pro\s+max|pro|max|plus|mini|air))?[?!.,]*$",
    re.IGNORECASE,
)






_CATALOG_BUYER_INTEREST_RE = re.compile(
    r"\b(?:fiquei|estou|to|tô)\s+interessad[oa]\b"
    r"|\bme\s+interessei\s+(?:no|na|em|nesse|nessa|neste|nesta)\b"
    r"|\bteria\s+interesse\s+(?:nesse|nessa|neste|nesta)\b"
    r"|\btenho\s+(?:o\s+)?interesse\s+(?:no|na|em|nesse|nessa|neste|nesta)\b",
    re.IGNORECASE,
)
_CATALOG_BUYER_INTEREST_NEGATION_RE = re.compile(
    r"\b(?:nao|nunca)\s+(?:fiquei|estou|to|tô|teria|me\s+interessei|"
    r"tenho\s+(?:o\s+)?interesse)\b",
    re.IGNORECASE,
)
_CATALOG_BUYER_TRADE_IN_SIGNAL_RE = re.compile(
    r"\b(?:troca|trocar|troco|vender|avaliar|avaliacao|"
    r"parte\s+do\s+pagamento)\b",
    re.IGNORECASE,
)








_CATALOG_BATTERY_UNIT_SELECTION_RE = re.compile(
    r"^\s*(?:(?:quero|queria|vou\s+querer|prefiro|escolho|escolheria|"
    r"fico\s+com|vou\s+ficar\s+com)\s+)?"
    r"(?:(?:a|o|uma|um|essa|esse|esta|este)\s+)?(?:de\s+)?"
    r"(?P<health>\d{1,3}(?:[.,]\d+)?)\s*(?:%|por\s+cento)?"
    r"(?:\s+(?:(?:a|de)\s+)?saude(?:\s+da)?\s+bateria)?[.!?]*\s*$",
    re.IGNORECASE,
)




def _catalog_price_unit_selection_query(
    text: str,
    history: list[dict[str, str]] | None,
) -> str | None:
    """Resolve a selected cash price against the offer that asked for a choice."""
    if (
        _is_photo_request(text)
        or not history
        or history[-1].get("role") != "assistant"
    ):
        return None

    offer = _normalize(history[-1].get("content", ""))
    asks_which_unit = bool(
        re.search(
            r"\bqual\s+(?:delas|deles|das\s+duas|dos\s+dois|unidade|opcao)\b",
            offer,
        )
    )
    asks_to_simulate = bool(re.search(r"\b(?:simul\w*|parcel\w*)\b", offer))
    if not asks_which_unit or not asks_to_simulate:
        return None

    selection_history = [*history, {"role": "user", "content": text}]
    return _selected_installment_offer_context(selection_history)


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

    retrying_photos = _is_photo_retry_request(current_text or "")
    current_models = _requested_iphone_model_keys(current_text or "")
    for entry in reversed(history or []):
        role = entry.get("role")
        content = entry.get("content", "").strip()
        if not content:
            continue

        if role == "assistant":
            if (
                not retrying_photos
                and not current_models
                and re.search(
                    r"\b(?:segue|seguem|enviei|mandei)\s+(?:as?\s+)?fotos?\b",
                    _normalize(content),
                )
            ):
                return False
            continue

        if role != "user":
            continue

        if _is_image_history_entry(content):
            # The visual description of an image sent by the customer can
            # mention a product and the word "imagem". Only its actual caption
            # can express a photo request; the model detected in the image
            # cannot turn the image into a request to send product photos.
            caption = re.search(
                r"(?im)^Legenda da imagem:\s*(.*?)(?=^Descricao visual da imagem recebida:|\Z)",
                content,
                flags=re.DOTALL,
            )
            content = caption.group(1).strip() if caption else ""
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
    if re.fullmatch(
        r"(?:e\s+)?(?:(?:o|os|a|as)\s+)?"
        r"(?:semi\s+nov[oa]s?|seminov[oa]s?|usad[oa]s?|lacrad[oa]s?|nov[oa]s?)"
        r"(?:\s+(?:ali|dali|da\s+lista|de\s+antes))?",
        normalized,
    ):
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


def build_turn_interpreter(settings: Settings) -> Agent:
    """Build a small structured-output pass for ambiguous conversational turns."""
    return Agent(
        name="Interpretação contextual do atendimento",
        instructions=TURN_INTERPRETER_INSTRUCTIONS,
        model=settings.openai_model,
        model_settings=ModelSettings(
            reasoning={"effort": "low"},
            verbosity="low",
        ),
        output_type=TurnInterpretation,
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
        self.turn_interpreter = (
            build_turn_interpreter(settings)
            if not offline and settings.openai_api_key
            else None
        )

    @staticmethod
    def _sanitize_image_urls(
        decision: AgentDecision,
        approved_urls: set[str] | None = None,
    ) -> AgentDecision:
        urls = [
            url
            for url in decision.image_urls
            if isinstance(url, str) and re.match(r"^https://", url, flags=re.IGNORECASE)
            and (approved_urls is None or url in approved_urls)
        ][:MAX_PRODUCT_PHOTOS]
        return decision.model_copy(update={"image_urls": urls})

    def _approved_catalog_photo_urls(self) -> set[str]:
        getter = getattr(self.cache, "approved_photo_urls", None)
        if not callable(getter):
            return set()
        return set(getter())

    async def _interpret_turn(
        self,
        text: str,
        history: list[dict[str, str]] | None,
        image_description: str | None = None,
    ) -> TurnInterpretation | None:
        if self.turn_interpreter is None:
            return None

        context_lines = []
        for item in (history or [])[-12:]:
            role = "Cliente" if item.get("role") == "user" else "Atendente virtual"
            context_lines.append(f"{role}: {item.get('content', '')}")
        current = text.strip() or "O cliente enviou uma mensagem sem texto."
        if image_description:
            current += f"\nDescrição cautelosa da imagem: {image_description}"
        prompt = "\n".join(
            [
                *context_lines,
                "Turno atual do cliente:",
                current,
            ]
        )
        try:
            result = await Runner.run(self.turn_interpreter, prompt, max_turns=1)
            output = result.final_output
            return (
                output
                if isinstance(output, TurnInterpretation)
                else TurnInterpretation.model_validate(output)
            )
        except Exception:
            # A failed interpretation should leave the established deterministic
            # handlers available for this turn.
            return None

    async def _run_customer_agent(
        self,
        text: str,
        history: list[dict[str, str]] | None,
        image_description: str | None = None,
        interpretation: TurnInterpretation | None = None,
    ) -> AgentDecision | None:
        if not self.settings.openai_api_key or self.agent is None:
            return None

        context_lines = []
        for item in (history or [])[-12:]:
            role = "Cliente" if item.get("role") == "user" else "Atendente virtual"
            context_lines.append(f"{role}: {item.get('content', '')}")
        if interpretation is not None:
            context_lines.append(
                "Interpretação contextual do turno (não contém fatos comerciais): "
                + json.dumps(
                    interpretation.model_dump(mode="json", exclude_none=True),
                    ensure_ascii=False,
                )
            )
        current = text.strip() or "O cliente enviou uma mensagem sem texto."
        if image_description:
            current += f"\nDescrição cautelosa da imagem: {image_description}"
        prompt = "\n".join([*context_lines, f"Cliente: {current}"])
        try:
            result = await Runner.run(self.agent, prompt, max_turns=6)
            output = result.final_output
            decision = (
                output
                if isinstance(output, AgentDecision)
                else AgentDecision.model_validate(output)
            )
            decision = _ensure_trade_in_form_before_handoff(
                decision,
                text,
                history,
                image_description=image_description,
            )
            return protect_customer_decision(
                self._sanitize_image_urls(
                    decision,
                    approved_urls=self._approved_catalog_photo_urls(),
                )
            )
        except Exception:
            return None

    async def _try_combined_iphone_17_catalog_and_trade_in(
        self,
        catalog_request: str,
    ) -> AgentDecision:
        is_full_line_request = _is_broad_iphone_17_catalog_request(catalog_request)
        if is_full_line_request:
            query = "Quais modelos da linha completa do iPhone 17 estão disponíveis?"
            catalog_decision = await self._try_available_products(query)
            fallback = (
                "No momento, não localizei opções da linha iPhone 17 disponíveis no catálogo."
            )
        else:
            query = catalog_request.strip()
            if not re.search(r"\biphones?\s+17\b", _normalize(query)):
                model = re.search(r"\b17\b", query)
                if model:
                    query = f"{query[:model.start()]}iPhone {query[model.start():]}"
            catalog_decision = await self._try_product_availability(query)
            fallback = (
                "Não consegui confirmar agora a disponibilidade do modelo pedido."
            )
        catalog_reply = (
            catalog_decision.reply.strip()
            if catalog_decision is not None
            else fallback
        )
        comparison_reply = (
            "Entre o iPhone 17 Pro e o Pro Max, a diferença mais prática é o tamanho: "
            "a tela do Pro tem 6,3 polegadas e a do Pro Max, 6,9 polegadas."
            if _has_iphone_17_pro_comparison_request(catalog_request)
            else ""
        )
        reply_parts = [catalog_reply]
        if comparison_reply:
            reply_parts.append(comparison_reply)
        reply_parts.append(TRADE_IN_FORM)
        return AgentDecision(
            reply="\n\n".join(reply_parts),
            confidence="high",
            product_references=(
                catalog_decision.product_references
                if catalog_decision is not None
                else []
            ),
            image_urls=(
                catalog_decision.image_urls if catalog_decision is not None else []
            ),
        )

    async def _try_trade_in_with_catalog_target(
        self,
        catalog_request: str,
        *,
        form_completed: bool,
    ) -> AgentDecision:
        catalog_decision = await self._try_product_availability(catalog_request)
        catalog_reply = (
            catalog_decision.reply.strip()
            if catalog_decision is not None
            else "Não consegui confirmar agora a disponibilidade do modelo pedido."
        )
        follow_up = (
            TRADE_IN_NEGOTIATION_REPLY if form_completed else TRADE_IN_FORM
        )
        return AgentDecision(
            reply=f"{catalog_reply}\n\n{follow_up}",
            handoff=form_completed,
            handoff_reason=TRADE_IN_REASON if form_completed else None,
            confidence="high",
            product_references=(
                catalog_decision.product_references
                if catalog_decision is not None
                else []
            ),
            image_urls=(
                catalog_decision.image_urls if catalog_decision is not None else []
            ),
        )

    async def respond(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
        image_description: str | None = None,
    ) -> AgentDecision:
        turn_interpretation: TurnInterpretation | None = None
        ambiguous_agent_attempted = False
        form_completed = is_completed_trade_in_form(text)
        unlabeled_form_response = is_unlabeled_trade_in_form_response(text, history)
        trade_target = trade_in_catalog_target(text)
        if form_completed:
            if trade_target:
                return protect_customer_decision(
                    await self._try_trade_in_with_catalog_target(
                        trade_target,
                        form_completed=True,
                    )
                )
            return AgentDecision(
                reply=TRADE_IN_NEGOTIATION_REPLY,
                handoff=True,
                handoff_reason=TRADE_IN_REASON,
                confidence="high",
            )
        if unlabeled_form_response:
            return AgentDecision(
                reply=TRADE_IN_NEGOTIATION_REPLY,
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
        if is_non_apple_trade_in_request(text) and not is_parts_buyback_request(text):
            return AgentDecision(reply=NON_APPLE_TRADE_IN_REPLY, confidence="high")
        if is_downgrade_request(text):
            return AgentDecision(
                reply=(
                    DOWNGRADE_MODEL_QUESTION
                    if trade_in_em_andamento(history)
                    else DOWNGRADE_EVALUATION_REPLY
                ),
                confidence="high",
            )
        if is_pending_trade_in_evaluation_followup(text, image_description, history):
            return AgentDecision(
                reply=TRADE_IN_FORM,
                handoff=False,
                confidence="high",
            )
        combined_catalog_request = (
            _combined_iphone_17_catalog_request(text, history)
            if not trade_in_em_andamento(history) and not _is_photo_request(text)
            else None
        )
        if combined_catalog_request is not None:
            return protect_customer_decision(
                await self._try_combined_iphone_17_catalog_and_trade_in(
                    combined_catalog_request
                )
            )
        if trade_target and not trade_in_em_andamento(history):
            return protect_customer_decision(
                await self._try_trade_in_with_catalog_target(
                    trade_target,
                    form_completed=False,
                )
            )
        store_condition_policy_decision = self._try_store_condition_policy_question(text)
        if store_condition_policy_decision is not None:
            return protect_customer_decision(store_condition_policy_decision)
        image_availability_models = _explicit_image_availability_query(
            text,
            image_description,
            list(getattr(self.cache, "items", []) or []),
        )
        if image_availability_models is not None:
            if not image_availability_models:
                return protect_customer_decision(
                    AgentDecision(
                        reply=(
                            "Não consegui identificar com segurança o modelo e a capacidade "
                            "na foto. Você pode me confirmar esses dados para eu consultar o estoque?"
                        ),
                        handoff=False,
                        confidence="medium",
                    )
                )
            decision = await self._try_product_availability(
                f"{text} {image_availability_models}",
                history=history,
            )
            if decision is not None:
                return protect_customer_decision(decision)
            return protect_customer_decision(
                AgentDecision(
                    reply=(
                        "Não consegui consultar o estoque desse modelo agora. "
                        "Vou encaminhar a consulta para um atendente confirmar."
                    ),
                    handoff=True,
                    handoff_reason="Falha ao consultar disponibilidade do produto identificado na imagem",
                    confidence="low",
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
                    handoff=False,
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
        seminovo_confirmation_decision = (
            await self._try_known_seminovo_price_confirmation(text, history)
        )
        if seminovo_confirmation_decision is not None:
            return protect_customer_decision(seminovo_confirmation_decision)
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
        sealed_order_payment_decision = self._try_sealed_order_payment(text, history)
        if sealed_order_payment_decision is not None:
            return protect_customer_decision(sealed_order_payment_decision)
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
            and not _has_explicit_trade_in_context(combined_request, history)
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
                handoff=False,
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

        # Resolve explicit photo requests before catalog availability can
        # replace the requested photos with a stock listing. Preserve a second
        # payment answer when WhatsApp batches that question into the same turn.
        if _is_explicit_photo_request(text) and len(_photo_price_selectors(text)) < 2:
            photo_decision = await self._try_product_photos(
                text,
                history,
                image_description=image_description,
            )
            if photo_decision is not None:
                if _is_payment_methods_question(text) or _is_installment_rate_question(text):
                    payment_decision = self._try_payment_methods(text)
                    if payment_decision is None:
                        payment_decision = await self._try_installment_rate_question(
                            text,
                            history=history,
                        )
                    if payment_decision is not None:
                        photo_decision = photo_decision.model_copy(
                            update={
                                "reply": (
                                    f"{payment_decision.reply.rstrip()}\n\n"
                                    f"{photo_decision.reply.lstrip()}"
                                )
                            }
                        )
                return protect_customer_decision(
                    self._sanitize_image_urls(photo_decision)
                )
            if not _is_photo_retry_request(text) or _has_photo_request_in_history(
                history,
                current_text=text,
            ):
                return protect_customer_decision(
                    AgentDecision(
                        reply=(
                            "Não consegui confirmar as fotos desse aparelho agora. "
                            "Vou encaminhar sua solicitação para um atendente confirmar."
                        ),
                        handoff=True,
                        handoff_reason=PRODUCT_PHOTO_HANDOFF_REASON,
                        confidence="low",
                    )
                )

        if _is_installment_budget_options_request(text, history):
            installment_budget_decision = await self._try_product_availability(
                text,
                history=history,
                image_description=image_description,
            )
            if installment_budget_decision is not None:
                return protect_customer_decision(installment_budget_decision)

        entry_installment_decision = await self._try_entry_installment(text, history)
        if entry_installment_decision is not None:
            return protect_customer_decision(entry_installment_decision)

        installment_offer_confirmation = await self._try_installment_offer_confirmation(
            text,
            history,
        )
        if installment_offer_confirmation is not None:
            return protect_customer_decision(installment_offer_confirmation)

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

        cash_price_decision = self._try_cash_price_followup(text, history)
        if cash_price_decision is not None:
            return protect_customer_decision(cash_price_decision)

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

        installment_rate_decision = await self._try_installment_rate_question(
            text,
            history=history,
        )
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

        installment_unit_selection = await self._try_installment_unit_selection(
            text,
            history,
        )
        if installment_unit_selection is not None:
            return protect_customer_decision(installment_unit_selection)

        case_accessory_decision = self._try_case_accessory_information(text)
        if case_accessory_decision is not None:
            return protect_customer_decision(case_accessory_decision)

        if _is_catalog_budget_objection(text, history):
            return AgentDecision(
                reply=CATALOG_BUDGET_OBJECTION_REPLY,
                confidence="high",
            )

        interpretation_text = "\n".join(
            part.strip() for part in (text, image_description) if part and part.strip()
        )
        if (
            self.turn_interpreter is not None
            and self.agent is not None
            and _should_use_turn_interpreter(interpretation_text, history)
        ):
            turn_interpretation = await self._interpret_turn(
                text,
                history,
                image_description=image_description,
            )
            if turn_interpretation is not None:
                if (
                    turn_interpretation.needs_clarification
                    and turn_interpretation.clarification_question
                    and turn_interpretation.confidence in {"high", "medium"}
                ):
                    return AgentDecision(
                        reply=turn_interpretation.clarification_question.strip(),
                        confidence=turn_interpretation.confidence,
                    )
                if turn_interpretation.confidence in {"high", "medium"}:
                    ambiguous_agent_attempted = True
                    semantic_decision = await self._run_customer_agent(
                        text,
                        history,
                        image_description=image_description,
                        interpretation=turn_interpretation,
                    )
                    if semantic_decision is not None and semantic_decision.confidence != "low":
                        return semantic_decision

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
        if not ambiguous_agent_attempted:
            decision = await self._run_customer_agent(
                text,
                history,
                image_description=image_description,
                interpretation=turn_interpretation,
            )
            if decision is not None:
                return decision
        return AgentDecision(
            reply="Vou encaminhar sua mensagem para um atendente confirmar essa informação.",
            handoff=True,
            handoff_reason="Falha no agente: chamada ou resposta inválida",
            confidence="low",
        )

    async def _try_installment_rate_question(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_installment_rate_question as _agent_handler,
        )
        return await _agent_handler(self, text, history)

    async def _try_generic_installment_count_question(
        self,
        text: str,
        history: list[dict[str, str]] | None,
        *,
        image_description: str | None = None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_generic_installment_count_question as _agent_handler,
        )
        return await _agent_handler(self, text, history, image_description=image_description)

    async def _try_rate_model_followup(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_rate_model_followup as _agent_handler,
        )
        return await _agent_handler(self, text, history)

    async def _try_model_installment_followup(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_model_installment_followup as _agent_handler,
        )
        return await _agent_handler(self, text, history)

    async def _try_installment_offer_confirmation(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_installment_offer_confirmation as _agent_handler,
        )
        return await _agent_handler(self, text, history)

    async def _try_installment_unit_selection(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_installment_unit_selection as _agent_handler,
        )
        return await _agent_handler(self, text, history)

    async def _try_payment_link(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_payment_link as _agent_handler,
        )
        return await _agent_handler(self, text, history)

    def _try_boleto_installment(self, text: str) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_boleto_installment as _agent_handler,
        )
        return _agent_handler(self, text)

    def _try_payment_methods(self, text: str) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_payment_methods as _agent_handler,
        )
        return _agent_handler(self, text)

    def _try_sealed_order_payment(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_sealed_order_payment as _agent_handler,
        )
        return _agent_handler(self, text, history)

    async def _try_combined_catalog_payment_methods(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_combined_catalog_payment_methods as _agent_handler,
        )
        return await _agent_handler(self, text, history)

    async def _try_combined_warranty_payment(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_combined_warranty_payment as _agent_handler,
        )
        return await _agent_handler(self, text, history)

    def _try_price_policy(self, text: str) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_price_policy as _agent_handler,
        )
        return _agent_handler(self, text)

    def _try_cash_price_followup(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_cash_price_followup as _agent_handler,
        )
        return _agent_handler(self, text, history)

    def _append_delivery_or_pickup_info(self, reply: str, text: str) -> str:
        from app.agent_handlers.store import (
            _append_delivery_or_pickup_info as _agent_handler,
        )
        return _agent_handler(self, reply, text)

    def _try_delivery_payment(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
    ) -> AgentDecision | None:
        from app.agent_handlers.store import (
            _try_delivery_payment as _agent_handler,
        )
        return _agent_handler(self, text, history)

    def _try_delivery_or_pickup(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
    ) -> AgentDecision | None:
        from app.agent_handlers.store import (
            _try_delivery_or_pickup as _agent_handler,
        )
        return _agent_handler(self, text, history)

    async def _try_available_products(self, text: str) -> AgentDecision | None:
        from app.agent_handlers.catalog import (
            _try_available_products as _agent_handler,
        )
        return await _agent_handler(self, text)

    def _try_case_accessory_information(self, text: str) -> AgentDecision | None:
        from app.agent_handlers.catalog import (
            _try_case_accessory_information as _agent_handler,
        )
        return _agent_handler(self, text)

    def _try_accessory_inclusion_information(
        self,
        text: str,
        history: list[dict[str, str]] | None = None,
    ) -> AgentDecision | None:
        from app.agent_handlers.catalog import (
            _try_accessory_inclusion_information as _agent_handler,
        )
        return _agent_handler(self, text, history)

    async def _try_battery_detail(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.catalog import (
            _try_battery_detail as _agent_handler,
        )
        return await _agent_handler(self, text, history)

    async def _try_catalog_product_reference(
        self,
        text: str,
        *,
        image_description: str | None = None,
    ) -> AgentDecision | None:
        from app.agent_handlers.catalog import (
            _try_catalog_product_reference as _agent_handler,
        )
        return await _agent_handler(self, text, image_description=image_description)

    async def _try_unavailable_lacrado_alternative(
        self,
        query: str,
        *,
        requested_budget: float | None = None,
        condition_query: str | None = None,
    ) -> AgentDecision | None:
        from app.agent_handlers.catalog import (
            _try_unavailable_lacrado_alternative as _agent_handler,
        )
        return await _agent_handler(
            self,
            query,
            requested_budget=requested_budget,
            condition_query=condition_query,
        )


    async def _try_unavailable_seminew_alternative(
        self,
        query: str,
        *,
        requested_label: str | None = None,
    ) -> AgentDecision | None:
        from app.agent_handlers.catalog import (
            _try_unavailable_seminew_alternative as _agent_handler,
        )
        return await _agent_handler(self, query, requested_label=requested_label)

    async def _try_catalog_price_recall(self, text: str) -> AgentDecision | None:
        from app.agent_handlers.catalog import (
            _try_catalog_price_recall as _agent_handler,
        )
        return await _agent_handler(self, text)

    async def _try_known_seminovo_price_confirmation(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        query = _known_seminovo_price_confirmation_query(text, history)
        requested_prices = _photo_price_selectors(text or "")
        if query is None or len(requested_prices) != 1:
            return None

        family = _catalog_family(query)
        model_markers = _catalog_offer_model_markers(query)
        has_specific_model = (
            family == "iphone"
            and len(_requested_iphone_model_keys(query)) == 1
        ) or any(re.search(r"\d", marker) for marker in model_markers)
        if not has_specific_model:
            return None

        try:
            candidates = await self.cache.search(query, limit=300)
        except Exception:
            return None

        price_matches = []
        for item in candidates:
            if (
                not _is_device_item(item)
                or not _matches_requested_model(query, item)
            ):
                continue
            item_name = _normalize(str(getattr(item, "name", "") or ""))
            if any(
                not re.search(rf"\b{re.escape(marker)}\b", item_name)
                for marker in model_markers
            ):
                continue
            try:
                price_cents = round(float(getattr(item, "price_brl", None)) * 100)
            except (TypeError, ValueError):
                continue
            if price_cents == requested_prices[0]:
                price_matches.append(item)

        if len(price_matches) != 1:
            return None

        item = price_matches[0]
        item_condition = _normalize(
            f"{getattr(item, 'name', '')} {getattr(item, 'condition', '')}"
        )
        if (
            not _is_available_item(item)
            or _is_sealed_item(item)
            or not _has_seminovo_reference(item_condition)
        ):
            return None

        reference = str(getattr(item, "external_id", "") or "")
        return AgentDecision(
            reply="Isso 😊 Esse valor corresponde à opção seminova disponível que te informei.",
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
        from app.agent_handlers.catalog import (
            _try_product_availability as _agent_handler,
        )
        return await _agent_handler(
            self,
            text,
            history,
            image_description=image_description,
            allow_buyer_interest_with_image=allow_buyer_interest_with_image,
        )

    def _try_current_day_information(self, text: str) -> AgentDecision | None:
        from app.agent_handlers.store import (
            _try_current_day_information as _agent_handler,
        )
        return _agent_handler(self, text)

    def _try_store_hours(self, text: str) -> AgentDecision | None:
        from app.agent_handlers.store import (
            _try_store_hours as _agent_handler,
        )
        return _agent_handler(self, text)

    def _try_store_condition_policy_question(
        self,
        text: str,
    ) -> AgentDecision | None:
        from app.agent_handlers.store import (
            _try_store_condition_policy_question as _agent_handler,
        )
        return _agent_handler(self, text)

    def _try_visit_scheduling(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.store import (
            _try_visit_scheduling as _agent_handler,
        )
        return _agent_handler(self, text, history)

    def _try_physical_store(self, text: str) -> AgentDecision | None:
        from app.agent_handlers.store import (
            _try_physical_store as _agent_handler,
        )
        return _agent_handler(self, text)

    async def _try_warranty(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.store import (
            _try_warranty as _agent_handler,
        )
        return await _agent_handler(self, text, history)

    async def _try_product_photos(
        self,
        text: str,
        history: list[dict[str, str]] | None,
        *,
        image_description: str | None = None,
    ) -> AgentDecision | None:
        from app.agent_handlers.photos import (
            _try_product_photos as _agent_handler,
        )
        return await _agent_handler(self, text, history, image_description=image_description)

    async def _try_combined_catalog_installment(
        self,
        text: str,
        history: list[dict[str, str]] | None,
        *,
        image_description: str | None = None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_combined_catalog_installment as _agent_handler,
        )
        return await _agent_handler(self, text, history, image_description=image_description)

    async def _try_multi_model_installment_tables(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_multi_model_installment_tables as _agent_handler,
        )
        return await _agent_handler(self, text, history)

    async def _try_both_unit_installment_tables(
        self,
        text: str,
        history: list[dict[str, str]] | None,
        result: dict[str, Any],
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_both_unit_installment_tables as _agent_handler,
        )
        return await _agent_handler(self, text, history, result)

    async def _try_specific_installment(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_specific_installment as _agent_handler,
        )
        return await _agent_handler(self, text, history)

    async def _try_ambiguous_condition_installment_tables(
        self,
        query: str,
        result: dict[str, Any],
    ) -> AgentDecision | None:
        """Simulate both confirmed conditions when only condition is ambiguous."""
        from app.agent_handlers.payments import (
            _try_ambiguous_condition_installment_tables as _agent_handler,
        )
        return await _agent_handler(self, query, result)

    async def _try_full_installment_table(
        self,
        text: str,
        history: list[dict[str, str]] | None,
        *,
        image_description: str | None = None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_full_installment_table as _agent_handler,
        )
        return await _agent_handler(self, text, history, image_description=image_description)

    async def _try_entry_installment(
        self,
        text: str,
        history: list[dict[str, str]] | None,
    ) -> AgentDecision | None:
        from app.agent_handlers.payments import (
            _try_entry_installment as _agent_handler,
        )
        return await _agent_handler(self, text, history)

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
        r"problema\w*.{0,30}funcionamento|funcionamento.{0,30}problema\w*|"
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


def _has_explicit_trade_in_context(
    text: str | None,
    history: list[dict[str, str]] | None,
) -> bool:
    normalized = _normalize(text)
    current_trade_in_cue = has_explicit_trade_in_cue(text) or is_trade_in_request(text)
    has_exchange_phrase = bool(
        re.search(
            r"\b(?:na troca|para troca|parte do pagamento|como entrada|de entrada|retoma\w*)\b",
            normalized,
        )
    )
    if (
        _is_device_condition_question(text)
        and _is_payment_methods_question(text)
        and not has_exchange_phrase
    ):
        current_trade_in_cue = False
    else:
        current_trade_in_cue = current_trade_in_cue or is_trade_in_context_request(
            text, history
        )
    if current_trade_in_cue:
        return True
    return any(
        entry.get("role") == "user"
        and (
            has_explicit_trade_in_cue(entry.get("content"))
            or is_trade_in_request(entry.get("content"))
        )
        for entry in (history or [])[-8:]
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
    """Send the evaluation form first, then transfer only after it is completed."""
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

    if _is_device_condition_question(request_context) and not _has_explicit_trade_in_context(
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
            "handoff": False,
            "handoff_reason": None,
            "confidence": "high",
        }
    )

# Keep app.agent imports stable while rules live in domain modules.
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


from .agent_rules.catalog_intents import (
    _capacity_key,
    _capacity_list_no_match_reply,
    _catalog_color_correction_scope,
    _catalog_color_key,
    _catalog_color_map,
    _confirmed_catalog_price,
    _current_catalog_color,
    _current_catalog_context,
    _explicit_image_availability_query,
    _extract_catalog_product_id,
    _has_pronta_entrega_reference,
    _has_requested_catalog_color,
    _is_accessory_catalog_request,
    _is_accessory_inclusion_question,
    _is_affirmative_first_iphone_statement,
    _is_all_iphone_17_line_request,
    _is_available_list_request,
    _is_battery_detail_request,
    _is_broad_airpods_request,
    _is_catalog_availability_confirmation,
    _is_catalog_color_either_or_request,
    _is_catalog_color_list_request,
    _is_catalog_followup,
    _is_catalog_price_followup,
    _is_cheapest_catalog_request,
    _is_contextual_pronta_entrega_followup,
    _is_generic_iphone_list_request,
    _is_image_history_entry,
    _is_ipad_model_alternative_price_followup,
    _is_new_model_list_request,
    _is_other_color_request,
    _is_pronta_entrega_only_request,
    _is_sealed_catalog_list_request,
    _is_sealed_iphone_list_request,
    _is_variant_catalog_list_request,
    _item_matches_catalog_colors,
    _latest_catalog_color_request,
    _requested_capacity_key,
    _requested_model_attribute_scopes,
    _seminovo_trade_in_catalog_target_query,
    _strip_catalog_color_references,
    _trailing_model_capacity_scope,
    _visual_catalog_search_query,
    _visual_iphone_model_keys,
    _visual_iphone_model_segments,
)

from .agent_rules.photos import (
    _has_explicit_photo_device_reference,
    _is_broad_promotion_photo_request,
    _is_explicit_photo_request,
    _is_made_to_order_sealed_item,
    _is_photo_request,
    _is_photo_retry_request,
    _is_sealed_item,
    _is_sealed_photo_request,
    _strip_historical_accessory_terms_from_photo_query,
)

from .agent_rules.store import (
    _appointment_context,
    _delivery_or_pickup_reply,
    _delivery_payment_reply,
    _extract_time_reference,
    _has_appointment_prompt,
    _has_catalog_condition_signal,
    _has_delivery_context,
    _has_today_visit_offer,
    _has_unanswered_reservation_request,
    _has_visit_date_reference,
    _has_visit_time_reference,
    _is_appointment_followup,
    _is_business_weekday,
    _is_current_date_request,
    _is_delivery_fee_request,
    _is_delivery_followup_request,
    _is_delivery_or_pickup_request,
    _is_delivery_payment_question,
    _is_delivery_quote_followup,
    _is_explicit_human_request,
    _is_handoff_confirmation,
    _is_physical_store_request,
    _is_pickup_schedule_request,
    _is_reservation_request,
    _is_store_day_hours_request,
    _is_store_hours_request,
    _is_today_store_status_request,
    _is_visit_request,
    _is_visit_unavailability_message,
    _latest_appointment_date_offer,
    _pickup_schedule_reply,
    _reservation_policy,
    _reservation_reply,
    _store_address,
    _store_hours,
    _store_hours_reply,
    _store_now,
    _today_label,
    _today_store_reply,
    _tomorrow_store_reply,
    _visit_date_for_request,
    _visit_day_reply,
    _visit_time_for_request,
)

from .agent_rules.catalog_formatting import (
    _format_available_products,
    _format_battery,
    _format_product_availability,
    _public_item,
    _sort_product_availability_items,
    _unavailable_catalog_color_header,
)

from .agent_rules.payments import (
    _bare_iphone_model_in_installment_question,
    _cash_price_quotes_from_history,
    _customer_installment_price_reference,
    _extract_budget_limit,
    _extract_entry_amount,
    _extract_installment_budget,
    _extract_latest_budget_limit,
    _format_ambiguous_installment_decision,
    _has_installment_model_prompt,
    _has_installment_product_context,
    _has_installment_rate_prompt,
    _has_payment_link_installment_context,
    _has_recent_card_installment_simulation,
    _has_recent_specific_installment_selection,
    _history_installment_condition,
    _installment_context_query,
    _is_boleto_payment_request,
    _is_both_unit_installment_request,
    _is_cash_discount_question,
    _is_cash_price_question,
    _is_contextual_boleto_followup,
    _is_credit_only_installment_question,
    _is_full_installment_request,
    _is_generic_installment_count_question,
    _is_installment_budget_options_request,
    _is_installment_fee_inclusion_question,
    _is_installment_model_followup,
    _is_installment_offer_confirmation,
    _is_installment_rate_for_referenced_price,
    _is_installment_rate_question,
    _is_installment_selection_followup,
    _is_no_interest_installment_question,
    _is_payment_link_followup,
    _is_payment_link_request,
    _is_payment_methods_question,
    _is_price_validity_question,
    _is_rate_model_followup,
    _is_specific_installment_request,
    _last_requested_installments,
    _multi_model_installment_queries,
    _parse_brl_amount,
    _parse_budget_amount,
    _parse_entry_amount,
    _recent_installment_entry_amount,
    _requested_device_quantity,
    _requested_installment_product_keys,
    _requested_installments,
    _selected_installment_offer_context,
    _selected_price_from_installment_choice,
    _strip_catalog_history_constraints,
)

from .agent_rules.context import (
    _bare_model_selection_reply,
    _catalog_battery_unit_selection_query,
    _customer_product_condition_context_query,
    _extract_bare_catalog_model_reference,
    _is_bare_model_availability_request,
    _is_bare_model_catalog_followup,
    _is_capacity_availability_followup,
    _is_catalog_buyer_interest,
    _is_catalog_model_confirmation,
    _is_product_interest_prompt,
    _is_standalone_photo_followup,
    _pending_multi_model_product_request_context,
    _photo_price_selectors,
    _product_interest_default_condition,
    _product_interest_model_context,
    _selected_catalog_row_context,
)
