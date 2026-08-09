# Robô de atendimento WhatsApp

Serviço FastAPI para a CWB.IPHONES, com Z-API, Mercado Phone somente leitura,
preços de lacrados vindos do Google Sheets, taxas de parcelamento fixas no robô,
OpenAI Agents SDK, PostgreSQL e processamento assíncrono por worker.

## Fontes de dados

- Mercado Phone: estoque, disponibilidade, informações do catálogo e anexos/fotos
  dos produtos por `POST` de listagem, sem nenhuma operação de alteração.
- Google Sheets: aba `BOT` para preços de aparelhos novos lacrados.
- `app/installments.py`: taxas fixas de parcelamento de 1x a 18x.
- `data/faq.yaml`: endereço, horários, entrega, pagamento, garantia e política
  de atendimento.

A planilha e o endpoint de arquivos são consultados somente com leitura. O robô
não cria, edita ou altera registros no Mercado Phone nem na planilha.

## Mensagens agrupadas

O webhook grava cada mensagem imediatamente e o worker espera dez segundos após
a última mensagem do cliente. Mensagens recebidas nesse intervalo são juntadas
em um único contexto, geram uma única chamada ao agente e uma única resposta.
O prazo pode ser ajustado por `MESSAGE_BATCH_WAIT_SECONDS`.

## Desenvolvimento local

Com Python 3.11+ instalado:

~~~powershell
uv sync --locked --extra dev
uv run pytest
~~~

O console offline não chama OpenAI, Google, Mercado Phone ou Z-API:

~~~powershell
uv run python scripts/test_console.py
~~~

Para consultar APIs em homologação, use explicitamente:

~~~powershell
uv run python scripts/test_console.py --live
~~~

A API web inicia com:

~~~powershell
$env:PORT = "8000"
uv run python main.py
~~~

## Google Sheets na VPS

A opção mais simples é publicar somente a aba `BOT` como CSV e configurar
`GOOGLE_SHEETS_PUBLIC_CSV_URL` no `.env.local`. Isso não exige chave, conta de
serviço ou projeto no Google Cloud. Como a URL publicada é acessível, use-a
apenas se for aceitável tornar esses preços públicos.

Se a planilha precisar permanecer privada, use uma conta de serviço com acesso de
leitor somente à planilha, salve o JSON em
`secrets/google-service-account.json` e siga
[docs/google-sheets.md](docs/google-sheets.md). A organização precisa permitir a
criação de chaves; caso contrário, peça uma exceção ao administrador ou use a
opção CSV.

O worker atualiza o cache de preços a cada hora. O preço do lacrado vem da aba
`BOT`; o estoque não é deduzido da planilha e continua sendo conferido no
Mercado Phone. As taxas de parcelamento são fixas no robô e não dependem da
planilha.

## Fotos dos produtos

Quando o cliente pede a foto de um aparelho, o robô identifica o ID do item no
estoque do Mercado Phone e consulta:

~~~text
POST https://app.mercadophone.tech/api.php?class=ArquivoApiController&method=index
~~~

com `origem=1` (Estoque) e `objetoId` igual ao ID do produto. As URLs HTTPS são
extraídas da resposta, sem salvar ou expor IDs, IMEI, serial ou outros metadados.
Não existe cadastro manual de fotos no projeto. Para não fazer uma chamada por
cada item, os anexos são buscados sob demanda, somente quando o cliente solicita
um modelo.

## Docker na VPS

1. Instale Docker e Docker Compose.
2. Copie o projeto para a VPS.
3. Crie `.env.local` e defina `DOMAIN` e uma senha forte em `POSTGRES_PASSWORD`.
4. Se usar a opção privada, copie também
   `secrets/google-service-account.json`.
5. Aponte o DNS do domínio para o IP da VPS.
6. Valide a configuraÃ§Ã£o e execute usando explicitamente `.env.local` para que
   as variÃ¡veis do Compose tambÃ©m sejam interpoladas:

~~~bash
docker compose --env-file .env.local config
docker compose --env-file .env.local build
docker compose --env-file .env.local up -d
docker compose --env-file .env.local logs -f app worker
~~~

O Caddy termina HTTPS para o domínio configurado. O webhook a cadastrar na Z-API
é:

~~~text
https://SEU_DOMINIO/webhooks/zapi/SEU_WEBHOOK_SECRET
~~~

## Segurança e escopo

O cliente Mercado Phone usa `GET` para lojas/estoque/catálogo e o `POST` de
listagem de arquivos somente para leitura. O histórico, eventos e filas ficam no
banco, com limpeza após `RETENTION_DAYS` (padrão de 30 dias). Áudios e imagens
recebidos do cliente são removidos após o processamento.

Mantenha `OUTBOUND_MODE=disabled` durante a validaÃ§Ã£o estrutural. Depois use
`OUTBOUND_MODE=test_only` e preencha `TEST_PHONES` para testar um telefone
autorizado. Somente apÃ³s os testes de aceite mude para `OUTBOUND_MODE=live`.
NÃ£o execute simultaneamente outra versÃ£o do robÃ´ apontando para o mesmo webhook.
