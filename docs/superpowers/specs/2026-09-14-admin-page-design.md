# Página administrativa do robô: catálogo e comandos

## Objetivo

Adicionar uma interface web administrativa privada para consultar a lista completa de produtos disponíveis sem iniciar uma conversa no WhatsApp e para executar, com confirmação, os comandos operacionais que hoje são enviados pelo telefone administrador.

## Contexto atual

- A API FastAPI existente expõe apenas `/health`, `/ready` e o webhook da Z-API.
- `StoreCatalogCache.list_available_products()` já combina as fontes usadas pelo robô:
  - seminovos disponíveis no Mercado Phone;
  - lacrados disponíveis para pronta entrega no Mercado Phone;
  - lacrados por encomenda e seus preços vindos do Google Sheets.
- `MessageProcessor` reconhece `#assumir`, `#retomar`, `#fechar`, `#retomar_todos` e `#liberar_todos` somente para telefones em `ADMIN_PHONES`.
- `Repository` já possui as operações de mudança de estado e o registro de auditoria necessários.

## Arquitetura aprovada

### Autenticação e proteção

- As rotas `/admin` e `/admin/api/*` exigirão HTTP Basic Auth.
- A configuração será feita por `ADMIN_USERNAME`, `ADMIN_PASSWORD` e `ADMIN_CSRF_SECRET`.
- Se qualquer credencial administrativa estiver ausente, as rotas administrativas responderão `404`, sem expor a existência do painel.
- As requisições `POST` de comando também exigirão um token CSRF calculado pelo servidor e enviado no cabeçalho `X-Admin-CSRF`.
- A página deve ser usada atrás do HTTPS fornecido pelo Caddy; nenhuma credencial será colocada em HTML, JavaScript ou URL.

### Rotas

- `GET /admin`: página HTML responsiva.
- `GET /admin/api/catalog`: catálogo atual em JSON, após a mesma atualização de cache usada pelo robô.
- `GET /admin/api/catalog.csv`: a mesma lista achatada em CSV UTF-8 com BOM, adequada para abrir no Excel.
- `POST /admin/api/commands`: executa somente ações allowlisted, com corpo `{ "action": ..., "phone": ... }`.

As respostas de catálogo não exporão IDs internos do Mercado Phone, IMEI, serial, URLs de fotos ou dados de conversas. Cada item exibirá nome, capacidade, condição, cor(es), preço(s), quantidade quando disponível, saúde da bateria e quantidade de fotos disponíveis.

### Serviço compartilhado de comandos

Será criado um serviço pequeno e determinístico para que WhatsApp e painel usem a mesma regra:

- `release_all`: chama `release_all_human_conversations`, libera `human_pending` e `human_active`, preserva `closed` e retorna a quantidade alterada.
- `assume`: exige telefone e define `human_active`.
- `resume`: exige telefone e define `bot_active`.
- `close`: exige telefone e define `closed`.

O serviço validará o telefone, rejeitará ações desconhecidas e registrará `admin_command` com operador, canal (`whatsapp` ou `web`), ação, alvo e quantidade liberada. O WhatsApp continuará enviando sua confirmação pela Z-API; o painel exibirá a confirmação diretamente na página.

### Interface

- Cabeçalho com atualização mais recente e botões `Atualizar` e `Baixar CSV`.
- Cartões de resumo por seção e tabela responsiva com busca textual.
- Seção de comandos com seletor de ação, telefone para ações individuais, confirmação explícita para liberar todos e área de resultado/erro.
- Valores dinâmicos serão inseridos no DOM com `textContent`, evitando HTML interpretável vindo do catálogo.

## Tratamento de erros

- Falha ao atualizar uma fonte de catálogo retorna `503` no endpoint de catálogo com mensagem genérica; detalhes de credenciais e exceções ficam somente nos logs já existentes.
- Credenciais inválidas retornam `401` e o cabeçalho Basic Auth.
- CSRF ausente ou inválido retorna `403`.
- Ação, telefone ou combinação de parâmetros inválidos retorna `400` sem alterar o banco.
- Exceções inesperadas durante um comando retornam `500`, registram erro no log e não são transformadas em sucesso visual.

## Testes de aceite

- Painel desabilitado sem as três variáveis administrativas.
- Basic Auth aceita credenciais corretas e rejeita incorretas.
- Catálogo JSON mantém as três seções e não vaza campos internos.
- CSV contém cabeçalho, todas as opções individuais e valores corretamente serializados.
- Comando sem CSRF é bloqueado.
- `release_all` libera conversas humanas, preserva encerradas e registra auditoria.
- `assume`, `resume` e `close` alteram somente o telefone solicitado e registram o canal web.
- Comandos atuais pelo WhatsApp continuam passando nos testes existentes.

## Fora do escopo

- Não haverá edição de estoque, preços ou dados no Mercado Phone/Google Sheets.
- Não haverá leitura ou exibição do histórico completo das conversas nesta primeira versão.
- Não haverá envio de mensagens para clientes pelo painel; os comandos apenas alteram o estado operacional já suportado pelo robô.
