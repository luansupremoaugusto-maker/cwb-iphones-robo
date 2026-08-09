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
