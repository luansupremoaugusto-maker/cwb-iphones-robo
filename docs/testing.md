# Testes controlados antes do WhatsApp

O robô pode ser aprimorado sem conectar a Z-API, sem domínio e sem alterar o
número real.

## Sandbox totalmente offline

Use um catálogo fictício e nenhum serviço externo:

```powershell
.\.venv\Scripts\python.exe scripts\test_console.py
```

Esse modo testa conversa, desambiguação, produto sem estoque, FAQ, handoff e
proteção de dados sem custo de API.

## Agrupamento de mensagens

Cada mensagem recebida entra no banco e o worker aguarda `MESSAGE_BATCH_WAIT_SECONDS`
(padrão: 10). Se o cliente enviar outra mensagem nesse intervalo, o prazo é
reiniciado. Depois de dez segundos sem nova mensagem, o agente recebe todo o
conteúdo junto e o robô envia uma única resposta.

O agrupamento é persistente no PostgreSQL/SQLite; reiniciar a VPS não elimina as
mensagens pendentes.

## Homologação com OpenAI e Mercado Phone

Use as credenciais já configuradas, mas mantenha o envio desativado:

```powershell
.\.venv\Scripts\python.exe scripts\test_console.py --live
```

Esse modo usa o modelo configurado, consulta o Mercado Phone por GET e consulta
anexos por POST somente quando uma foto é solicitada. Ele não chama a Z-API e não
envia nenhuma mensagem ao WhatsApp.

## Testes automatizados

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe evals\run_local.py
```

Os testes cobrem o debounce de dez segundos, a junção das mensagens, uma única
chamada ao agente, uma única resposta de saída e a consulta read-only de fotos.

## Fluxo final de homologação

1. Aperfeiçoar o FAQ e o prompt no sandbox offline.
2. Validar estoque real no modo `--live`.
3. Testar uma solicitação de foto com um produto que tenha anexo no Mercado Phone.
4. Executar os testes automatizados.
5. Subir uma cópia da aplicação na VPS com `OUTBOUND_MODE=disabled`.
6. Depois configurar o `Client-Token`, domínio e webhook.
7. Usar `OUTBOUND_MODE=test_only` com um telefone de teste.
8. Confirmar que várias mensagens rápidas geram uma resposta única.
9. Somente após a validação, mudar para `OUTBOUND_MODE=live`.
