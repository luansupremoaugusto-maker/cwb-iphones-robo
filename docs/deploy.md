# Implantação na VPS

## Preparação

- DNS A/AAAA do domínio apontando para a VPS.
- Docker Engine e Docker Compose Plugin instalados.
- `.env.local` criado com permissão restrita e sem commit.
- `DOMAIN` definido para o domínio público.
- `POSTGRES_PASSWORD` forte definido no ambiente usado pelo Compose.
- Chave Mercado Phone limitada à loja `X-Unit-Id=2620` e credenciais Z-API configuradas.
- Conta de serviço Google criada, com a Sheets API ativa e acesso **Leitor** somente
  à planilha de preços.
- Arquivo da conta de serviço salvo em
  `secrets/google-service-account.json`.
- `ADMIN_USERNAME`, `ADMIN_PASSWORD` e `ADMIN_CSRF_SECRET` definidos no
  `.env.local` para habilitar o painel administrativo privado.

## Subida

~~~bash
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail=100 app worker caddy
~~~

Valide `https://SEU_DOMINIO/health` e `https://SEU_DOMINIO/ready`. O endpoint
`ready` só fica pronto quando banco, OpenAI, Mercado Phone, Google Sheets e Z-API
estiverem configurados.

## Webhook

Cadastre na Z-API:

~~~text
https://SEU_DOMINIO/webhooks/zapi/SEU_WEBHOOK_SECRET
~~~

Use `ZAPI_EXPECTED_INSTANCE_ID` quando a conta tiver mais de uma instância.
Eventos de grupos, mensagens próprias, newsletters, status e duplicatas são ignorados.

## Painel administrativo

Acesse `https://SEU_DOMINIO/admin` e informe as credenciais na tela de login
configuradas no `.env.local`. HTTP Basic Auth continua aceito para integrações.
A página consulta o catálogo pelo mesmo caminho
do robô, mostra seminovos, lacrados em pronta entrega e lacrados por encomenda,
e permite baixar `catalogo-disponiveis.csv`.

O painel mostra ainda o resumo dos estados das conversas, a saúde do banco e das
integrações, a fila de atendimento humano com a última mensagem e uma auditoria
recente. Os botões da fila apenas preparam o comando correspondente; a execução
continua exigindo confirmação no navegador.

O painel também oferece `Assumir conversa`, `Retomar conversa para o robô`,
`Fechar conversa` e `Liberar todos os clientes`. Os três primeiros exigem o
telefone da conversa; o último libera apenas `human_pending` e `human_active` e
preserva `closed` (`release_all`). Antes da execução, o painel mostra a prévia
do impacto, pede justificativa e exige confirmação; tudo fica auditado.

O perfil definido em `ADMIN_ROLE` pode ser `owner` (controle completo) ou
`operator` (comandos individuais). O proprietário também pode pausar o robô,
ativar manutenção, revogar sessões de navegador, acompanhar falhas recentes e
atualizar Mercado Phone/Google Sheets separadamente. Enquanto o estado global
estiver pausado ou em manutenção, as mensagens recebidas são armazenadas e
aguardam processamento; nenhuma resposta automática é enviada.

Mantenha o domínio atrás de HTTPS e nunca coloque as credenciais administrativas
na URL ou no repositório. Se as três variáveis não estiverem preenchidas, as
rotas `/admin` ficam desabilitadas.

## Operação segura

- Mantenha `OUTBOUND_MODE=disabled` durante os testes.
- Use `test_only` e preencha `TEST_PHONES` antes do primeiro teste de envio.
- Só use `live` depois de revisar o FAQ e o fluxo de handoff.
- Os comandos de atendente são enviados por um telefone em `ADMIN_PHONES`:
  `#assumir 5511999999999`, `#retomar 5511999999999`, `#fechar 5511999999999`.
- Para reativar de uma vez todas as conversas em `human_pending` ou `human_active`,
  use `#retomar_todos` (alias: `#liberar_todos`). Conversas `closed` permanecem
  encerradas.
- O comando em massa só funciona para telefones cadastrados em `ADMIN_PHONES`.
- Faça backup do PostgreSQL e monitore os logs de erro, os refreshes da planilha
  e os callbacks da Z-API.
