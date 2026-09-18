from __future__ import annotations

import html
import json


_LOGIN_PAGE_TEMPLATE = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Acesso administrativo · CWB.IPHONES</title>
  <style>
    :root { color-scheme: light; --ink: #172033; --muted: #667085; --line: #e4e7ec; --surface: #fff; --soft: #f5f7fb; --brand: #2457d6; --danger: #b42318; }
    * { box-sizing: border-box; }
    body { align-items: center; background: var(--soft); color: var(--ink); display: flex; font: 15px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; justify-content: center; margin: 0; min-height: 100vh; padding: 20px; }
    .card { background: var(--surface); border: 1px solid var(--line); border-radius: 16px; box-shadow: 0 10px 28px rgba(16, 24, 40, .08); max-width: 420px; padding: 28px; width: 100%; }
    .eyebrow { color: var(--brand); font-size: 12px; font-weight: 750; letter-spacing: .09em; margin-bottom: 8px; text-transform: uppercase; }
    h1 { font-size: 28px; letter-spacing: -.03em; margin: 0 0 8px; }
    p { margin: 0 0 20px; }
    .muted { color: var(--muted); }
    .error { background: #fef3f2; border: 1px solid #fecdca; border-radius: 9px; color: var(--danger); padding: 10px 12px; }
    form { display: grid; gap: 14px; }
    .field { display: grid; gap: 6px; }
    label { font-size: 13px; font-weight: 700; }
    input, button { border: 1px solid #cfd5df; border-radius: 9px; font: inherit; min-height: 44px; padding: 10px 12px; }
    button { background: var(--brand); border-color: var(--brand); color: #fff; cursor: pointer; font-weight: 700; }
    button:hover { background: #1742ad; border-color: #1742ad; }
  </style>
</head>
<body>
  <main class="card">
    <div class="eyebrow">CWB.IPHONES · painel privado</div>
    <h1>Acesso administrativo</h1>
    <p class="muted">Entre para consultar os disponíveis e controlar o atendimento do robô.</p>
    __ERROR__
    <form method="post" action="/admin/login" autocomplete="on">
      <div class="field">
        <label for="username">Usuário</label>
        <input id="username" name="username" type="text" autocomplete="username" required autofocus>
      </div>
      <div class="field">
        <label for="password">Senha</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required>
      </div>
      <button type="submit">Entrar</button>
    </form>
  </main>
</body>
</html>
"""


_PAGE_TEMPLATE = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Administração do robô</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #172033;
      --muted: #667085;
      --line: #e4e7ec;
      --surface: #ffffff;
      --soft: #f5f7fb;
      --brand: #2457d6;
      --brand-dark: #1742ad;
      --danger: #b42318;
      --success: #067647;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--soft);
      color: var(--ink);
      font: 15px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .shell { max-width: 1440px; margin: 0 auto; padding: 28px 20px 48px; }
    header { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; margin-bottom: 24px; }
    h1, h2, p { margin-top: 0; }
    h1 { font-size: clamp(25px, 4vw, 36px); margin-bottom: 6px; letter-spacing: -0.03em; }
    h2 { font-size: 20px; margin-bottom: 5px; }
    .eyebrow { color: var(--brand); font-size: 12px; font-weight: 750; letter-spacing: .09em; text-transform: uppercase; margin-bottom: 7px; }
    .muted, .source-note { color: var(--muted); }
    .actions, .toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    button, .button-link {
      border: 1px solid var(--brand);
      border-radius: 9px;
      background: var(--brand);
      color: #fff;
      cursor: pointer;
      font: inherit;
      font-weight: 700;
      padding: 10px 14px;
      text-decoration: none;
    }
    button:hover, .button-link:hover { background: var(--brand-dark); border-color: var(--brand-dark); }
    button.secondary, .button-link.secondary { background: var(--surface); color: var(--brand); }
    button.secondary:hover, .button-link.secondary:hover { background: #eef3ff; }
    button.danger { background: var(--danger); border-color: var(--danger); }
    button:disabled { cursor: wait; opacity: .6; }
    .panel { background: var(--surface); border: 1px solid var(--line); border-radius: 16px; box-shadow: 0 5px 18px rgba(16, 24, 40, .04); margin-top: 18px; padding: 20px; }
    .panel > [hidden] { display: none !important; }
    .summary-grid { display: grid; gap: 12px; grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .operational-summary { grid-template-columns: repeat(5, minmax(0, 1fr)); }
    .summary-card { background: var(--soft); border: 1px solid var(--line); border-radius: 12px; padding: 15px; }
    .summary-card strong { display: block; font-size: 25px; line-height: 1.15; margin-top: 3px; }
    .summary-card span { color: var(--muted); font-size: 13px; }
    .panel-heading { align-items: flex-start; display: flex; gap: 14px; justify-content: space-between; }
    .panel-heading > :last-child { flex-shrink: 0; }
    .toolbar { justify-content: space-between; margin: 20px 0 12px; }
    input, select { border: 1px solid #cfd5df; border-radius: 9px; background: #fff; color: var(--ink); font: inherit; min-height: 42px; padding: 9px 11px; }
    input[type="search"] { min-width: min(100%, 360px); }
    .table-wrap { border: 1px solid var(--line); border-radius: 11px; overflow: auto; }
    table { border-collapse: collapse; min-width: 980px; width: 100%; }
    th, td { border-bottom: 1px solid var(--line); padding: 12px 11px; text-align: left; vertical-align: top; }
    th { background: #f9fafb; color: var(--muted); font-size: 12px; letter-spacing: .04em; position: sticky; top: 0; text-transform: uppercase; }
    tbody tr:last-child td { border-bottom: 0; }
    tbody tr:hover { background: #fbfcff; }
    .status { min-height: 22px; margin: 10px 0 0; }
    .status.error { color: var(--danger); }
    .status.success { color: var(--success); }
    .sources { display: flex; flex-wrap: wrap; gap: 8px 20px; margin-top: 12px; }
    .source-note { font-size: 12px; }
    .health-grid { display: grid; gap: 10px; grid-template-columns: repeat(5, minmax(0, 1fr)); margin-top: 16px; }
    .health-card { align-items: center; background: var(--soft); border: 1px solid var(--line); border-radius: 10px; display: flex; gap: 8px; justify-content: space-between; min-height: 48px; padding: 10px 12px; }
    .health-card > div { align-items: center; display: flex; gap: 8px; min-width: 0; }
    .health-card strong { display: block; font-size: 13px; }
    .health-card small { color: var(--muted); display: block; font-size: 11px; }
    .health-dot { background: var(--muted); border-radius: 50%; flex: 0 0 9px; height: 9px; width: 9px; }
    .health-card.ok .health-dot { background: var(--success); }
    .health-card.bad .health-dot { background: var(--danger); }
    .health-card.stale .health-dot { background: #b54708; }
    .health-refresh { font-size: 11px; padding: 6px 8px; white-space: nowrap; }
    .monitoring-grid { display: grid; gap: 10px; grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top: 14px; }
    .monitoring-card { background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 12px; }
    .monitoring-card strong { display: block; font-size: 13px; margin-bottom: 4px; }
    .monitoring-card p { color: var(--muted); font-size: 13px; margin: 0; }
    .badge { background: #eef3ff; border-radius: 999px; color: var(--brand-dark); display: inline-block; font-size: 12px; font-weight: 700; padding: 3px 8px; white-space: nowrap; }
    .badge.pending { background: #fffaeb; color: #7a2e0b; }
    .badge.active { background: #ecfdf3; color: var(--success); }
    .badge.closed { background: #f2f4f7; color: var(--muted); }
    .panel-toggle {
      align-items: center;
      background: var(--surface);
      border-color: var(--line);
      color: var(--muted);
      display: inline-flex;
      font-size: 20px;
      height: 36px;
      justify-content: center;
      line-height: 1;
      min-width: 36px;
      padding: 0;
    }
    .panel-toggle:hover { background: #eef3ff; border-color: var(--brand); color: var(--brand); }
    .panel-toggle:focus-visible { outline: 3px solid #c7d7fe; outline-offset: 2px; }
    .compact-table table { min-width: 980px; }
    .compact-table th, .compact-table td { padding: 10px; }
    .queue-actions { display: flex; flex-wrap: wrap; gap: 6px; }
    .queue-actions button { font-size: 12px; padding: 7px 9px; }
    .audit-detail { color: var(--muted); font-size: 12px; max-width: 330px; overflow-wrap: anywhere; }
    .nowrap { white-space: nowrap; }
    .recovery-editor { background: #fbfcff; border: 1px solid #c7d7fe; border-radius: 12px; display: grid; gap: 14px; margin: 16px 0; padding: 14px; }
    .recovery-editor[hidden] { display: none; }
    .recovery-editor h3 { font-size: 16px; margin: 0 0 4px; }
    .recovery-history { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; display: grid; gap: 8px; max-height: 300px; overflow: auto; padding: 10px; }
    .recovery-message { border-radius: 9px; padding: 9px 11px; white-space: pre-wrap; }
    .recovery-message.inbound { background: #eef3ff; margin-right: 12%; }
    .recovery-message.outbound { background: #f2f4f7; margin-left: 12%; }
    .recovery-message strong { display: block; font-size: 12px; margin-bottom: 3px; }
    .recovery-message small { color: var(--muted); display: block; font-size: 11px; margin-top: 4px; }
    .trace-result { display: grid; gap: 14px; margin-top: 16px; }
    .trace-result[hidden] { display: none; }
    .trace-meta { color: var(--muted); font-size: 13px; }
    .trace-summary { grid-template-columns: repeat(6, minmax(0, 1fr)); }
    .trace-summary .summary-card strong { font-size: 21px; }
    .trace-audit table { min-width: 760px; }
    .recovery-compose { display: grid; gap: 7px; }
    textarea { border: 1px solid #cfd5df; border-radius: 9px; color: var(--ink); font: inherit; min-height: 150px; padding: 10px 11px; resize: vertical; width: 100%; }
    .recovery-review { margin: 0; }
    .recovery-review.warning { margin: 0; }
    .recovery-actions { align-items: center; display: flex; flex-wrap: wrap; gap: 8px; }
    .recovery-actions .status { margin: 0; }
    .recovery-row-actions { display: grid; gap: 6px; min-width: 138px; }
    .recovery-row-actions button { font-size: 12px; padding: 7px 9px; }
    .category-badge { background: #f2f4f7; border-radius: 999px; color: var(--ink); display: inline-block; font-size: 12px; font-weight: 700; padding: 3px 8px; }
    .command-grid { align-items: end; display: grid; gap: 12px; grid-template-columns: minmax(180px, 1fr) minmax(190px, 1fr) minmax(260px, 1.4fr) auto; }
    .command-actions { display: flex; flex-wrap: wrap; gap: 8px; }
    .command-actions button { white-space: nowrap; }
    .export-panel { background: #fbfcff; border: 1px solid var(--line); border-radius: 12px; display: grid; gap: 12px; margin: 18px 0 12px; padding: 14px; }
    .export-panel h3 { font-size: 15px; margin: 0 0 4px; }
    .export-panel p { font-size: 13px; margin: 0; }
    .export-options { border: 0; display: flex; flex-wrap: wrap; gap: 8px 18px; margin: 0; padding: 0; }
    .export-options legend { color: var(--muted); font-size: 12px; font-weight: 750; margin-bottom: 4px; padding: 0; width: 100%; }
    .export-option { align-items: center; display: flex; gap: 7px; font-weight: 600; }
    .export-option input { min-height: auto; }
    .export-actions { align-items: center; display: flex; flex-wrap: wrap; gap: 10px; }
    .export-actions .status { margin: 0; }
    #export-csv:disabled, #export-pdf:disabled { cursor: not-allowed; }
    .preview-box { background: #eef3ff; border: 1px solid #c7d7fe; border-radius: 9px; color: var(--brand-dark); font-size: 13px; margin-top: 14px; padding: 10px 12px; }
    .control-grid { align-items: end; display: grid; gap: 12px; grid-template-columns: minmax(190px, 1fr) minmax(220px, 1.2fr) minmax(260px, 1.4fr) auto; }
    .sessions-table table { min-width: 720px; }
    .field { display: grid; gap: 5px; }
    #phone-field[hidden] { display: none; }
    label { font-size: 13px; font-weight: 700; }
    .warning { background: #fffaeb; border: 1px solid #fedf89; border-radius: 9px; color: #7a2e0b; font-size: 13px; margin: 14px 0 0; padding: 10px 12px; }
    .empty { color: var(--muted); padding: 22px 12px; text-align: center; }
    @media (max-width: 820px) {
      header { flex-direction: column; }
      .actions { width: 100%; }
      .actions > * { flex: 1; text-align: center; }
      .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .operational-summary, .health-grid, .monitoring-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .panel-heading { flex-direction: column; }
      .command-grid, .control-grid { grid-template-columns: 1fr; }
      .command-grid button, .control-grid button { width: 100%; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div>
        <div class="eyebrow">CWB.IPHONES · painel privado</div>
        <h1>Administração do robô</h1>
        <p class="muted">Consulte o catálogo e controle o atendimento sem enviar comandos pelo WhatsApp.</p>
      </div>
      <div class="actions">
        <button id="refresh-catalog" type="button">Atualizar catálogo</button>
      </div>
    </header>

    <section class="panel" data-panel-key="commands" aria-labelledby="commands-title">
      <div class="panel-heading">
        <div>
          <h2 id="commands-title">Comandos operacionais</h2>
          <p class="muted">As ações abaixo alteram o estado das conversas e ficam registradas na auditoria.</p>
        </div>
        <button class="panel-toggle" type="button" data-panel-toggle aria-expanded="true" aria-label="Minimizar painel" title="Minimizar painel">−</button>
      </div>
      <form id="command-form">
        <div class="command-grid">
          <div class="field">
            <label for="command-action">Ação</label>
            <select id="command-action" name="action">
              <option value="release_all">Liberar todos os clientes</option>
              <option value="assume">Assumir conversa</option>
              <option value="resume">Retomar conversa para o robô</option>
              <option value="close">Fechar conversa</option>
            </select>
          </div>
          <div class="field" id="phone-field" hidden>
            <label for="command-phone">Telefone da conversa</label>
            <input id="command-phone" name="phone" inputmode="tel" placeholder="5541999999999" autocomplete="off">
          </div>
          <div class="field">
            <label for="command-justification">Justificativa</label>
            <input id="command-justification" name="justification" maxlength="250" placeholder="Por que esta ação é necessária?" autocomplete="off" required>
          </div>
          <div class="command-actions">
            <button id="command-preview" class="secondary" type="submit">Pré-visualizar impacto</button>
            <button id="command-submit" class="danger" type="button" disabled>Executar após a prévia</button>
          </div>
        </div>
        <p class="warning">“Liberar todos” reativa somente conversas em atendimento humano; conversas encerradas permanecem encerradas.</p>
        <div id="command-preview-box" class="preview-box" hidden></div>
        <p id="command-status" class="status" role="status" aria-live="polite"></p>
      </form>
    </section>

    <section class="panel" data-panel-key="operations" aria-labelledby="operations-title">
      <div class="panel-heading">
        <div>
          <h2 id="operations-title">Visão geral do atendimento</h2>
          <p class="muted">Acompanhe as conversas, a fila humana e a saúde das integrações.</p>
        </div>
        <div class="actions">
          <button id="refresh-dashboard" class="secondary" type="button">Atualizar painel</button>
          <span id="operations-status" class="status muted" role="status" aria-live="polite"></span>
          <button class="panel-toggle" type="button" data-panel-toggle aria-expanded="true" aria-label="Minimizar painel" title="Minimizar painel">−</button>
        </div>
      </div>
      <div class="summary-grid operational-summary" aria-label="Resumo do atendimento">
        <div class="summary-card"><span>Total de conversas</span><strong id="conversation-total">—</strong></div>
        <div class="summary-card"><span>Robô ativo</span><strong id="conversation-bot-active">—</strong></div>
        <div class="summary-card"><span>Aguardando humano</span><strong id="conversation-human-pending">—</strong></div>
        <div class="summary-card"><span>Humano em atendimento</span><strong id="conversation-human-active">—</strong></div>
        <div class="summary-card"><span>Encerradas</span><strong id="conversation-closed">—</strong></div>
      </div>
      <div id="health-grid" class="health-grid" aria-label="Saúde das integrações"></div>
      <div class="monitoring-grid" aria-label="Monitoramento operacional">
        <div class="monitoring-card"><strong>Estado global do robô</strong><p id="bot-control-status">Carregando…</p></div>
        <div class="monitoring-card"><strong>Fontes desatualizadas</strong><p id="monitoring-stale-status">Verificando…</p></div>
        <div class="monitoring-card"><strong>Falhas nas últimas 24 horas</strong><p id="monitoring-error-status">Verificando…</p></div>
      </div>
    </section>

    <section class="panel" data-panel-key="conversation-lookup" aria-labelledby="conversation-lookup-title">
      <div class="panel-heading">
        <div>
          <h2 id="conversation-lookup-title">Consultar conversa</h2>
          <p class="muted">Abra o registro temporário pelo protocolo CWB ou pelo telefone para investigar uma falha.</p>
        </div>
        <button class="panel-toggle" type="button" data-panel-toggle aria-expanded="true" aria-label="Minimizar painel" title="Minimizar painel">−</button>
      </div>
      <div class="toolbar">
        <input id="conversation-lookup" type="search" placeholder="CWB-00000001 ou 5541999999999" autocomplete="off" aria-label="Protocolo ou telefone da conversa">
        <button id="conversation-lookup-submit" type="button">Consultar conversa</button>
        <span id="conversation-lookup-status" class="status muted" role="status" aria-live="polite"></span>
      </div>
      <div id="conversation-lookup-result" class="trace-result" hidden>
        <div id="conversation-lookup-meta" class="trace-meta"></div>
        <div id="conversation-lookup-summary" class="summary-grid trace-summary" aria-label="Resumo técnico da conversa"></div>
        <div id="conversation-lookup-history" class="recovery-history" aria-label="Histórico completo da conversa"></div>
        <div class="table-wrap compact-table trace-audit">
          <table>
            <thead><tr><th>Data</th><th>Evento</th><th>Detalhes</th></tr></thead>
            <tbody id="conversation-lookup-audit-body"></tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="panel" data-panel-key="recovery" aria-labelledby="recovery-title">
      <div class="panel-heading">
        <div>
          <h2 id="recovery-title">Recuperação pós-viagem</h2>
          <p class="muted">Prepare respostas para conversas antigas, inclusive quando o robô respondeu por último. Mensagens novas ficam fora desta fila até envelhecerem.</p>
        </div>
        <div class="actions">
          <label for="recovery-older-hours">Sem atualização há</label>
          <input id="recovery-older-hours" type="number" min="0" max="8760" step="1" value="24" aria-label="Horas sem atualização">
          <span class="muted">horas</span>
          <button id="refresh-recovery" class="secondary" type="button">Atualizar fila</button>
          <button class="panel-toggle" type="button" data-panel-toggle aria-expanded="true" aria-label="Minimizar painel" title="Minimizar painel">−</button>
        </div>
      </div>
      <div id="recovery-editor" class="recovery-editor" hidden>
        <div class="panel-heading">
          <div>
            <h3 id="recovery-editor-title">Preparar resposta</h3>
            <p id="recovery-editor-meta" class="muted"></p>
          </div>
          <button id="close-recovery-editor" class="secondary" type="button">Fechar editor</button>
        </div>
        <div id="recovery-history" class="recovery-history" aria-label="Histórico da conversa"></div>
        <div class="recovery-compose">
          <label for="recovery-message">Rascunho da resposta</label>
          <textarea id="recovery-message" maxlength="4000" aria-describedby="recovery-review"></textarea>
          <p id="recovery-review" class="warning recovery-review" hidden></p>
        </div>
        <div class="recovery-actions">
          <button id="send-recovery-message" type="button">Enviar resposta</button>
          <button id="skip-recovery-message" class="secondary" type="button">Pular conversa</button>
          <span id="recovery-editor-status" class="status muted" role="status" aria-live="polite"></span>
        </div>
      </div>
      <div class="toolbar">
        <input id="recovery-search" type="search" placeholder="Buscar cliente, telefone ou assunto" autocomplete="off" aria-label="Buscar na recuperação">
        <span id="recovery-summary" class="status muted" role="status" aria-live="polite">Carregando fila…</span>
        <span id="recovery-status" class="status muted" role="status" aria-live="polite"></span>
      </div>
      <div class="table-wrap compact-table">
        <table>
          <thead>
            <tr><th>Categoria</th><th>Cliente</th><th>Telefone</th><th>Última mensagem</th><th>Idade</th><th>Ação</th></tr>
          </thead>
          <tbody id="recovery-queue-body"></tbody>
        </table>
      </div>
      <div class="recovery-actions">
        <button id="recovery-more" class="secondary" type="button" hidden>Carregar mais 50</button>
      </div>
    </section>

    <section class="panel" data-panel-key="catalog" aria-labelledby="catalog-title">
      <div class="panel-heading">
        <div>
          <h2 id="catalog-title">Catálogo de disponíveis</h2>
          <p class="muted">A lista usa o mesmo catálogo do robô: estoque físico do Mercado Phone e preços de lacrados por encomenda da planilha.</p>
        </div>
        <button class="panel-toggle" type="button" data-panel-toggle aria-expanded="true" aria-label="Minimizar painel" title="Minimizar painel">−</button>
      </div>
      <div class="summary-grid" aria-label="Resumo do catálogo">
        <div class="summary-card"><span>Total de opções</span><strong id="catalog-total">—</strong></div>
        <div class="summary-card"><span>Seminovos</span><strong id="count-seminovos">—</strong></div>
        <div class="summary-card"><span>Lacrados em estoque</span><strong id="count-lacrados_pronta_entrega">—</strong></div>
        <div class="summary-card"><span>Lacrados por encomenda</span><strong id="count-lacrados">—</strong></div>
      </div>
      <div class="export-panel" aria-labelledby="catalog-export-title">
        <div>
          <h3 id="catalog-export-title">Exportar catálogo para o robô</h3>
          <p class="muted">Escolha quais categorias devem entrar no CSV ou no PDF. Os seminovos são os aparelhos disponíveis no estoque; os lacrados ficam separados entre pronta entrega e encomenda.</p>
        </div>
        <fieldset class="export-options">
          <legend>Categorias para exportar</legend>
          <label class="export-option" for="export-seminovos"><input id="export-seminovos" type="checkbox" checked> Seminovos em estoque</label>
          <label class="export-option" for="export-lacrados_pronta_entrega"><input id="export-lacrados_pronta_entrega" type="checkbox" checked> Lacrados à pronta entrega</label>
          <label class="export-option" for="export-lacrados"><input id="export-lacrados" type="checkbox" checked> Lacrados por encomenda</label>
        </fieldset>
        <div class="export-actions">
          <button id="export-csv" class="secondary" type="button">Baixar CSV</button>
          <button id="export-pdf" class="secondary" type="button">Baixar PDF</button>
          <span id="export-status" class="status muted" role="status" aria-live="polite"></span>
        </div>
      </div>
      <div class="toolbar">
        <input id="catalog-search" type="search" placeholder="Buscar modelo, capacidade ou cor" autocomplete="off" aria-label="Buscar no catálogo">
        <div class="toolbar" style="margin:0; justify-content:flex-end">
          <select id="catalog-category-filter" aria-label="Filtrar por categoria">
            <option value="">Todas as categorias</option>
          </select>
          <select id="catalog-capacity-filter" aria-label="Filtrar por capacidade">
            <option value="">Todas as capacidades</option>
          </select>
          <select id="catalog-color-filter" aria-label="Filtrar por cor">
            <option value="">Todas as cores</option>
          </select>
          <select id="catalog-condition-filter" aria-label="Filtrar por condição">
            <option value="">Todas as condições</option>
          </select>
          <select id="catalog-stock-filter" aria-label="Filtrar por disponibilidade">
            <option value="">Toda disponibilidade</option>
            <option value="Em estoque">Somente em estoque</option>
            <option value="Por encomenda">Somente por encomenda</option>
            <option value="Sem estoque">Sem estoque</option>
          </select>
          <input id="catalog-price-min" type="number" min="0" step="0.01" placeholder="Preço mínimo" aria-label="Preço mínimo">
          <input id="catalog-price-max" type="number" min="0" step="0.01" placeholder="Preço máximo" aria-label="Preço máximo">
          <label class="field" style="display:flex; align-items:center; gap:6px; white-space:nowrap"><input id="catalog-photos-filter" type="checkbox"> Com fotos</label>
        </div>
        <span id="catalog-status" class="status muted" role="status" aria-live="polite">Carregando catálogo…</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Categoria</th><th>Produto</th><th>Capacidade</th><th>Condição</th><th>Cor(es)</th><th>Preço(s)</th><th>Quantidade</th><th>Disponibilidade</th><th>Bateria</th><th>Fotos</th></tr>
          </thead>
          <tbody id="catalog-body"></tbody>
        </table>
      </div>
      <div class="sources">
        <span id="mercado-source" class="source-note">Mercado Phone: —</span>
        <span id="sheets-source" class="source-note">Google Sheets: —</span>
        <span id="generated-source" class="source-note">Página gerada: —</span>
      </div>
    </section>

    <section class="panel" data-panel-key="queue" aria-labelledby="queue-title">
      <div class="panel-heading">
        <div>
          <h2 id="queue-title">Fila de atendimento humano</h2>
          <p class="muted">Veja quem aguarda atendimento ou já está sendo atendido. As ações preparam o comando sem executá-lo automaticamente.</p>
        </div>
        <div class="actions">
          <button id="refresh-queue" class="secondary" type="button">Atualizar fila</button>
          <button class="panel-toggle" type="button" data-panel-toggle aria-expanded="true" aria-label="Minimizar painel" title="Minimizar painel">−</button>
        </div>
      </div>
      <div class="toolbar">
        <input id="human-queue-search" type="search" placeholder="Buscar nome, telefone ou mensagem" autocomplete="off" aria-label="Buscar na fila humana">
        <span id="human-queue-status" class="status muted" role="status" aria-live="polite">Carregando fila…</span>
      </div>
      <div class="table-wrap compact-table">
        <table>
          <thead>
            <tr><th>Status</th><th>Cliente</th><th>Telefone</th><th>Última mensagem</th><th>Atualizado</th><th>Ações</th></tr>
          </thead>
          <tbody id="human-queue-body"></tbody>
        </table>
      </div>
    </section>

    <section class="panel" data-panel-key="control" aria-labelledby="control-title">
      <div class="panel-heading">
        <div>
          <h2 id="control-title">Controles gerais</h2>
          <p class="muted">Pause as respostas do robô, ative manutenção e acompanhe os acessos ao painel.</p>
        </div>
        <div class="actions">
          <span id="control-role" class="badge">Perfil: —</span>
          <button class="panel-toggle" type="button" data-panel-toggle aria-expanded="true" aria-label="Minimizar painel" title="Minimizar painel">−</button>
        </div>
      </div>
      <form id="admin-control-form">
        <div class="control-grid">
          <div class="field">
            <label for="control-action">Ação global</label>
            <select id="control-action" name="action">
              <option value="pause_bot">Pausar respostas do robô</option>
              <option value="resume_bot">Retomar respostas do robô</option>
              <option value="maintenance_on">Ativar manutenção</option>
              <option value="maintenance_off">Desativar manutenção</option>
              <option value="logout_sessions">Desconectar sessões administrativas</option>
            </select>
          </div>
          <div class="field">
            <label for="control-reason">Motivo exibido no estado</label>
            <input id="control-reason" maxlength="255" placeholder="Ex.: manutenção programada" autocomplete="off">
          </div>
          <div class="field">
            <label for="control-justification">Justificativa</label>
            <input id="control-justification" maxlength="250" placeholder="Por que esta ação é necessária?" autocomplete="off" required>
          </div>
          <button id="control-submit" class="danger" type="submit">Aplicar controle</button>
        </div>
        <p id="control-status" class="status" role="status" aria-live="polite"></p>
      </form>
      <div class="toolbar">
        <strong>Sessões ativas no navegador</strong>
        <button id="refresh-sessions" class="secondary" type="button">Atualizar sessões</button>
      </div>
      <p class="muted">Sessões de Basic Auth não aparecem nesta lista. “Desconectar sessões” revoga os logins por navegador.</p>
      <div class="table-wrap compact-table sessions-table">
        <table>
          <thead><tr><th>Operador</th><th>Início</th><th>Última atividade</th><th>Expira</th></tr></thead>
          <tbody id="sessions-body"></tbody>
        </table>
      </div>
      <p id="sessions-status" class="status muted" role="status" aria-live="polite">Carregando sessões…</p>
    </section>

    <section class="panel" data-panel-key="audit" aria-labelledby="audit-title">
      <div class="panel-heading">
        <div>
          <h2 id="audit-title">Auditoria recente</h2>
          <p class="muted">Comandos e eventos importantes registrados pelo robô, com operador, canal e resultado.</p>
        </div>
        <div class="actions">
          <button id="refresh-audit" class="secondary" type="button">Atualizar auditoria</button>
          <button class="panel-toggle" type="button" data-panel-toggle aria-expanded="true" aria-label="Minimizar painel" title="Minimizar painel">−</button>
        </div>
      </div>
      <div class="toolbar">
        <input id="audit-search" type="search" placeholder="Filtrar por evento, telefone ou operador" autocomplete="off" aria-label="Buscar na auditoria">
        <span id="audit-status" class="status muted" role="status" aria-live="polite">Carregando auditoria…</span>
      </div>
      <div class="table-wrap compact-table">
        <table>
          <thead>
            <tr><th>Data</th><th>Evento</th><th>Alvo</th><th>Operador / canal</th><th>Detalhes</th></tr>
          </thead>
          <tbody id="audit-body"></tbody>
        </table>
      </div>
    </section>
  </main>

  <script>
    (() => {
      const csrfToken = __CSRF_TOKEN__;
      const sectionDefinitions = [
        ["seminovos", "Seminovos"],
        ["lacrados_pronta_entrega", "Lacrados para pronta entrega"],
        ["lacrados", "Lacrados por encomenda"]
      ];
      let catalog = null;
      let dashboard = null;
      let humanQueue = [];
      let recoveryQueue = [];
      let recoveryTotal = 0;
      let recoveryDraft = null;
      let recoverySearchTimer = null;
      let auditEvents = [];
      const healthDefinitions = [
        ["database", "Banco de dados", null],
        ["mercado_phone", "Mercado Phone", "mercado_phone"],
        ["google_sheets", "Google Sheets", "google_sheets"],
        ["zapi", "Z-API", null],
        ["openai", "OpenAI", null]
      ];

      const byId = (id) => document.getElementById(id);
      const PANEL_STATE_STORAGE_KEY = "cwb-admin-panel-state-v1";
      const panelState = (() => {
        try {
          const raw = sessionStorage.getItem(PANEL_STATE_STORAGE_KEY);
          const parsed = raw ? JSON.parse(raw) : {};
          return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
        } catch (error) {
          return {};
        }
      })();
      const savePanelState = () => {
        try {
          sessionStorage.setItem(PANEL_STATE_STORAGE_KEY, JSON.stringify(panelState));
        } catch (error) {
          // A sessão pode bloquear storage; nesse caso o painel continua utilizável.
        }
      };
      const setPanelExpanded = (panel, expanded, persist = true) => {
        const heading = Array.from(panel.children).find((child) => child.classList.contains("panel-heading"));
        const toggle = heading?.querySelector("[data-panel-toggle]");
        if (!heading || !toggle) return;
        Array.from(panel.children).forEach((child) => {
          if (child !== heading) child.hidden = !expanded;
        });
        const label = expanded ? "Minimizar painel" : "Maximizar painel";
        toggle.setAttribute("aria-expanded", String(expanded));
        toggle.setAttribute("aria-label", label);
        toggle.title = label;
        toggle.textContent = expanded ? "−" : "+";
        if (persist) {
          panelState[panel.dataset.panelKey] = expanded;
          savePanelState();
        }
      };
      const initializeCollapsiblePanels = () => {
        document.querySelectorAll("section.panel[data-panel-key]").forEach((panel) => {
          setPanelExpanded(panel, panelState[panel.dataset.panelKey] !== false, false);
        });
      };
      const formatPrice = (value) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(Number(value));
      const formatDate = (value) => {
        if (!value) return "não carregado";
        const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
        return Number.isNaN(date.getTime()) ? "não disponível" : date.toLocaleString("pt-BR");
      };
      const itemText = (item) => [item.nome, item.capacidade, item.condicao, item.cor, item.disponibilidade, ...(item.cores || [])].join(" ").toLocaleLowerCase();
      const asText = (value, fallback = "—") => value === null || value === undefined || value === "" ? fallback : String(value);
      const batteryText = (value) => value === null || value === undefined || value === "" ? "—" : `${value}%`;
      const queueText = (item) => [item.chat_name, item.phone, item.status, item.status_label, item.last_message, item.paused_reason].join(" ").toLocaleLowerCase();
      const recoveryText = (item) => [item.chat_name, item.phone, ...(item.phone_aliases || []), item.category_label, item.last_message].join(" ").toLocaleLowerCase();
      const auditText = (item) => [item.event_type, item.subject, JSON.stringify(item.detail || {})].join(" ").toLocaleLowerCase();

      async function fetchJson(path, options = {}) {
        const response = await fetch(path, { credentials: "same-origin", ...options });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "Não foi possível carregar os dados administrativos.");
        return result;
      }

      function appendCell(row, value, className = "") {
        const cell = document.createElement("td");
        if (className) cell.className = className;
        cell.textContent = asText(value);
        row.append(cell);
        return cell;
      }

      function statusBadge(status, label) {
        const badge = document.createElement("span");
        badge.className = `badge ${status === "human_pending" ? "pending" : status === "human_active" ? "active" : status === "closed" ? "closed" : ""}`;
        badge.textContent = asText(label || status);
        return badge;
      }

      function renderDashboard() {
        const conversations = dashboard?.conversations || {};
        byId("conversation-total").textContent = asText(conversations.total, "0");
        byId("conversation-bot-active").textContent = asText(conversations.bot_active, "0");
        byId("conversation-human-pending").textContent = asText(conversations.human_pending, "0");
        byId("conversation-human-active").textContent = asText(conversations.human_active, "0");
        byId("conversation-closed").textContent = asText(conversations.closed, "0");

        const grid = byId("health-grid");
        grid.replaceChildren();
        healthDefinitions.forEach(([key, label, refreshKey]) => {
          const source = dashboard?.sources?.[key] || {};
          const card = document.createElement("div");
          card.className = `health-card ${source.stale ? "stale" : source.ok ? "ok" : "bad"}`;
          const dot = document.createElement("span");
          dot.className = "health-dot";
          const details = document.createElement("div");
          const title = document.createElement("strong");
          title.textContent = label;
          const note = document.createElement("small");
          const itemCount = source.items === undefined ? "" : ` · ${source.items} item(ns)`;
          note.textContent = source.stale
            ? `Desatualizado${source.last_refresh ? ` desde ${formatDate(source.last_refresh)}` : ""}${itemCount}`
            : source.last_refresh
              ? `Atualizado ${formatDate(source.last_refresh)}${itemCount}`
              : `${source.ok ? "Operacional" : "Indisponível"}${itemCount}`;
          details.append(title, note);
          const content = document.createElement("div");
          content.append(dot, details);
          card.append(content);
          if (refreshKey) {
            const refresh = document.createElement("button");
            refresh.type = "button";
            refresh.className = "secondary health-refresh";
            refresh.dataset.source = refreshKey;
            refresh.textContent = "Atualizar fonte";
            card.append(refresh);
          }
          grid.append(card);
        });

        const control = dashboard?.control || {};
        const controlMode = control.mode || "active";
        const controlLabels = { active: "Ativo", paused: "Pausado", maintenance: "Em manutenção" };
        byId("bot-control-status").textContent = `${controlLabels[controlMode] || controlMode}${control.reason ? ` · ${control.reason}` : ""}`;
        const monitoring = dashboard?.monitoring || {};
        const stale = monitoring.stale_sources || [];
        byId("monitoring-stale-status").textContent = stale.length ? stale.join(", ") : "Nenhuma fonte desatualizada.";
        const recentErrors = monitoring.recent_errors || {};
        byId("monitoring-error-status").textContent = recentErrors.count
          ? `${recentErrors.count} evento(s) com erro registrado(s).`
          : "Nenhuma falha registrada.";
        const permissions = dashboard?.permissions || dashboard?.control?.permissions || {};
        const controlRole = byId("control-role");
        controlRole.textContent = `Perfil: ${dashboard?.role || "owner"}`;
        if (permissions.owner_controls === false) {
          byId("admin-control-form").title = "Apenas o perfil proprietário pode alterar controles globais.";
          byId("control-submit").disabled = true;
        }
      }

      function renderConversationLookup(detail) {
        const result = byId("conversation-lookup-result");
        result.hidden = false;
        byId("conversation-lookup-meta").textContent = [
          detail.protocol,
          detail.phone,
          detail.chat_name,
          detail.status_label,
          detail.updated_at ? `Atualizada ${formatDate(detail.updated_at)}` : "",
        ].filter(Boolean).join(" · ");

        const summary = byId("conversation-lookup-summary");
        summary.replaceChildren();
        [
          ["message_count", "Mensagens"],
          ["inbound_count", "Cliente"],
          ["outbound_count", "Atendimento"],
          ["audit_count", "Eventos"],
          ["error_count", "Erros"],
          ["handoff_count", "Handoffs"],
        ].forEach(([key, label]) => {
          const card = document.createElement("div");
          card.className = "summary-card";
          const title = document.createElement("span");
          title.textContent = label;
          const value = document.createElement("strong");
          value.textContent = asText(detail.diagnostics?.[key], "0");
          card.append(title, value);
          summary.append(card);
        });

        const history = byId("conversation-lookup-history");
        history.replaceChildren();
        const messages = detail.messages || [];
        if (!messages.length) {
          const empty = document.createElement("div");
          empty.className = "empty";
          empty.textContent = "Nenhuma mensagem registrada.";
          history.append(empty);
        } else {
          messages.forEach((item) => {
            const bubble = document.createElement("div");
            bubble.className = `recovery-message ${item.direction === "inbound" ? "inbound" : "outbound"}`;
            const author = document.createElement("strong");
            author.textContent = item.direction === "inbound" ? "Cliente" : "Atendimento";
            const content = document.createElement("span");
            content.textContent = item.text || `[${item.kind || "mídia"} sem texto]`;
            const date = document.createElement("small");
            date.textContent = formatDate(item.created_at);
            bubble.append(author, content, date);
            history.append(bubble);
          });
        }

        const auditBody = byId("conversation-lookup-audit-body");
        auditBody.replaceChildren();
        const audit = detail.audit || [];
        if (!audit.length) {
          const row = document.createElement("tr");
          const cell = document.createElement("td");
          cell.colSpan = 3;
          cell.className = "empty";
          cell.textContent = "Nenhum evento técnico registrado.";
          row.append(cell);
          auditBody.append(row);
        } else {
          audit.forEach((item) => {
            const row = document.createElement("tr");
            appendCell(row, formatDate(item.created_at), "nowrap");
            appendCell(row, item.event_type || "—");
            const detailCell = appendCell(row, JSON.stringify(item.detail || {}), "audit-detail");
            detailCell.title = JSON.stringify(item.detail || {});
            auditBody.append(row);
          });
        }
      }

      async function lookupConversation() {
        const reference = byId("conversation-lookup").value.trim();
        const status = byId("conversation-lookup-status");
        if (!reference) {
          status.className = "status error";
          status.textContent = "Informe o protocolo ou o telefone da conversa.";
          return;
        }
        status.className = "status muted";
        status.textContent = "Consultando conversa…";
        byId("conversation-lookup-submit").disabled = true;
        try {
          const detail = await fetchJson(`/admin/api/conversations/${encodeURIComponent(reference)}`);
          renderConversationLookup(detail);
          status.className = "status success";
          status.textContent = `Conversa ${detail.protocol || reference} carregada.`;
        } catch (error) {
          byId("conversation-lookup-result").hidden = true;
          status.className = "status error";
          status.textContent = error.message;
        } finally {
          byId("conversation-lookup-submit").disabled = false;
        }
      }

      function renderHumanQueue() {
        const body = byId("human-queue-body");
        const query = byId("human-queue-search").value.trim().toLocaleLowerCase();
        const matches = humanQueue.filter((item) => !query || queueText(item).includes(query));
        body.replaceChildren();
        if (!matches.length) {
          const row = document.createElement("tr");
          const cell = document.createElement("td");
          cell.colSpan = 6;
          cell.className = "empty";
          cell.textContent = "Nenhuma conversa na fila humana.";
          row.append(cell);
          body.append(row);
          return;
        }
        matches.forEach((item) => {
          const row = document.createElement("tr");
          const statusCell = document.createElement("td");
          statusCell.append(statusBadge(item.status, item.status_label));
          row.append(statusCell);
          appendCell(row, item.chat_name || "Sem nome");
          appendCell(row, item.phone, "nowrap");
          appendCell(row, item.last_message || item.paused_reason || "Sem mensagem registrada");
          appendCell(row, formatDate(item.updated_at), "nowrap");
          const actionsCell = document.createElement("td");
          const actions = document.createElement("div");
          actions.className = "queue-actions";
          const commands = item.status === "human_pending"
            ? [["assume", "Assumir"], ["close", "Fechar"]]
            : [["resume", "Retomar robô"], ["close", "Fechar"]];
          commands.forEach(([action, label]) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "secondary";
            button.dataset.action = action;
            button.dataset.phone = item.phone || "";
            button.textContent = label;
            actions.append(button);
          });
          actionsCell.append(actions);
          row.append(actionsCell);
          body.append(row);
        });
      }

      function renderRecoveryQueue() {
        const body = byId("recovery-queue-body");
        const query = byId("recovery-search")?.value.trim().toLocaleLowerCase() || "";
        const matches = recoveryQueue.filter((item) => !query || recoveryText(item).includes(query));
        body.replaceChildren();
        if (!matches.length) {
          const row = document.createElement("tr");
          const cell = document.createElement("td");
          cell.colSpan = 6;
          cell.className = "empty";
          cell.textContent = "Nenhuma conversa antiga encontrada nesta janela.";
          row.append(cell);
          body.append(row);
          return;
        }
        matches.forEach((item) => {
          const row = document.createElement("tr");
          const categoryCell = document.createElement("td");
          const category = document.createElement("span");
          category.className = "category-badge";
          category.textContent = asText(item.category_label, "Dúvida geral");
          categoryCell.append(category);
          row.append(categoryCell);
          appendCell(row, item.chat_name || "Sem nome");
          appendCell(row, item.phone, "nowrap");
          appendCell(row, item.last_message || item.paused_reason || "Sem mensagem registrada");
          appendCell(row, item.age_hours === null || item.age_hours === undefined ? "—" : `${item.age_hours} h`, "nowrap");
          const actionsCell = document.createElement("td");
          const actions = document.createElement("div");
          actions.className = "recovery-row-actions";
          const prepareButton = document.createElement("button");
          prepareButton.type = "button";
          prepareButton.className = "secondary";
          prepareButton.dataset.recoveryAction = "prepare";
          prepareButton.dataset.recoveryPhone = item.phone || "";
          prepareButton.dataset.recoveryLastMessageId = item.last_message_id || "";
          prepareButton.textContent = "Preparar resposta";
          const skipButton = document.createElement("button");
          skipButton.type = "button";
          skipButton.className = "secondary";
          skipButton.dataset.recoveryAction = "skip";
          skipButton.dataset.recoveryPhone = item.phone || "";
          skipButton.dataset.recoveryLastMessageId = item.last_message_id || "";
          skipButton.textContent = "Pular";
          skipButton.title = "Pular até o cliente enviar uma nova mensagem";
          actions.append(prepareButton, skipButton);
          actionsCell.append(actions);
          row.append(actionsCell);
          body.append(row);
        });
      }

      function renderRecoveryEditor() {
        const editor = byId("recovery-editor");
        if (!recoveryDraft) {
          editor.hidden = true;
          return;
        }
        editor.hidden = false;
        byId("recovery-editor-title").textContent = `Resposta para ${recoveryDraft.chat_name || recoveryDraft.phone}`;
        byId("recovery-editor-meta").textContent = [
          recoveryDraft.phone,
          recoveryDraft.category_label,
          `confiança ${recoveryDraft.confidence}`,
        ].filter(Boolean).join(" · ");
        const history = byId("recovery-history");
        history.replaceChildren();
        (recoveryDraft.messages || []).forEach((item) => {
          const bubble = document.createElement("div");
          bubble.className = `recovery-message ${item.direction === "inbound" ? "inbound" : "outbound"}`;
          const author = document.createElement("strong");
          author.textContent = item.direction === "inbound" ? "Cliente" : "Atendimento";
          const content = document.createElement("span");
          content.textContent = item.text || `[${item.kind || "mídia"} sem texto]`;
          const date = document.createElement("small");
          date.textContent = formatDate(item.created_at);
          bubble.append(author, content, date);
          history.append(bubble);
        });
        byId("recovery-message").value = recoveryDraft.draft || "";
        const review = byId("recovery-review");
        review.hidden = !recoveryDraft.review_required;
        review.textContent = recoveryDraft.review_reason || "Revise esta resposta antes de enviar.";
        byId("send-recovery-message").disabled = !recoveryDraft.draft || recoveryDraft.send_allowed === false;
      }

      function renderAudit() {
        const body = byId("audit-body");
        const query = byId("audit-search").value.trim().toLocaleLowerCase();
        const matches = auditEvents.filter((item) => !query || auditText(item).includes(query));
        body.replaceChildren();
        if (!matches.length) {
          const row = document.createElement("tr");
          const cell = document.createElement("td");
          cell.colSpan = 5;
          cell.className = "empty";
          cell.textContent = "Nenhum evento encontrado.";
          row.append(cell);
          body.append(row);
          return;
        }
        matches.forEach((item) => {
          const row = document.createElement("tr");
          appendCell(row, formatDate(item.created_at), "nowrap");
          appendCell(row, item.event_type);
          appendCell(row, item.subject || "—", "nowrap");
          const detail = item.detail || {};
          appendCell(row, [detail.operator, detail.channel].filter(Boolean).join(" · ") || "—");
          const detailCell = appendCell(row, JSON.stringify(detail), "audit-detail");
          detailCell.title = JSON.stringify(detail);
          body.append(row);
        });
      }

      function flattenItems() {
        return sectionDefinitions.flatMap(([key, label]) => (catalog?.[key] || []).map((item) => ({ key, label, item })));
      }

      function selectedExportSections() {
        return sectionDefinitions
          .filter(([key]) => byId(`export-${key}`).checked)
          .map(([key]) => key);
      }

      function updateExportStatus() {
        const selected = selectedExportSections();
        const status = byId("export-status");
        ["export-csv", "export-pdf"].forEach((id) => { byId(id).disabled = !selected.length; });
        status.className = `status ${selected.length ? "muted" : "error"}`;
        status.textContent = selected.length
          ? `${selected.length} categoria(s) selecionada(s). O CSV e o PDF terão somente essa seleção.`
          : "Selecione ao menos uma categoria para exportar.";
      }

      function exportCatalog(format) {
        const selected = selectedExportSections();
        if (!selected.length) {
          updateExportStatus();
          return;
        }
        const url = new URL(`/admin/api/catalog.${format}`, window.location.origin);
        if (selected.length !== sectionDefinitions.length) url.searchParams.set("sections", selected.join(","));
        window.location.assign(url.toString());
      }

      function exportCatalogCsv() { exportCatalog("csv"); }
      function exportCatalogPdf() { exportCatalog("pdf"); }

      function renderSummary() {
        byId("catalog-total").textContent = asText(catalog?.total_modelos, "0");
        sectionDefinitions.forEach(([key]) => { byId(`count-${key}`).textContent = String((catalog?.[key] || []).length); });
        byId("mercado-source").textContent = `Mercado Phone: ${formatDate(catalog?.sources?.mercado_phone_last_refresh)}`;
        byId("sheets-source").textContent = `Google Sheets: ${formatDate(catalog?.sources?.google_sheets_last_refresh)}`;
        byId("generated-source").textContent = `Página gerada: ${formatDate(catalog?.generated_at)}`;
      }

      function uniqueCatalogValues(selector) {
        return [...new Set(flattenItems().flatMap(({ item }) => {
          const value = selector(item);
          return Array.isArray(value) ? value : [value];
        }).filter((value) => value !== null && value !== undefined && String(value).trim()))]
          .map((value) => String(value))
          .sort((left, right) => left.localeCompare(right, "pt-BR", { numeric: true }));
      }

      function populateSelect(id, values, placeholder) {
        const select = byId(id);
        const selected = select.value;
        select.replaceChildren();
        const first = document.createElement("option");
        first.value = "";
        first.textContent = placeholder;
        select.append(first);
        values.forEach((value) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = value;
          select.append(option);
        });
        select.value = values.includes(selected) ? selected : "";
      }

      function renderCatalogFilters() {
        populateSelect("catalog-category-filter", sectionDefinitions.map(([, label]) => label), "Todas as categorias");
        populateSelect("catalog-capacity-filter", uniqueCatalogValues((item) => item.capacidade), "Todas as capacidades");
        populateSelect("catalog-color-filter", uniqueCatalogValues((item) => item.cores?.length ? item.cores : item.cor), "Todas as cores");
        populateSelect("catalog-condition-filter", uniqueCatalogValues((item) => item.condicao), "Todas as condições");
      }

      function itemAvailability(item) {
        if (item.disponibilidade) return item.disponibilidade;
        if (item.quantidade === null || item.quantidade === undefined) return "Por encomenda";
        return Number(item.quantidade) > 0 ? "Em estoque" : "Sem estoque";
      }

      function itemMatchesFilters(label, item) {
        const category = byId("catalog-category-filter").value;
        const capacity = byId("catalog-capacity-filter").value;
        const color = byId("catalog-color-filter").value;
        const condition = byId("catalog-condition-filter").value;
        const stock = byId("catalog-stock-filter").value;
        const minPrice = Number.parseFloat(byId("catalog-price-min").value.replace(",", "."));
        const maxPrice = Number.parseFloat(byId("catalog-price-max").value.replace(",", "."));
        const prices = (item.precos_brl || []).map(Number).filter(Number.isFinite);
        const colors = item.cores?.length ? item.cores : [item.cor];
        return (!category || label === category)
          && (!capacity || item.capacidade === capacity)
          && (!color || colors.includes(color))
          && (!condition || item.condicao === condition)
          && (!stock || itemAvailability(item) === stock)
          && (!byId("catalog-photos-filter").checked || Number(item.fotos_disponiveis || 0) > 0)
          && (!Number.isFinite(minPrice) || prices.some((price) => price >= minPrice))
          && (!Number.isFinite(maxPrice) || prices.some((price) => price <= maxPrice));
      }

      function renderTable() {
        const body = byId("catalog-body");
        const query = byId("catalog-search").value.trim().toLocaleLowerCase();
        body.replaceChildren();
        const matches = flattenItems().filter(({ label, item }) =>
          (!query || itemText(item).includes(query)) && itemMatchesFilters(label, item)
        );
        if (!matches.length) {
          const row = document.createElement("tr");
          const cell = document.createElement("td");
          cell.colSpan = 10;
          cell.className = "empty";
          cell.textContent = "Nenhuma opção encontrada.";
          row.append(cell);
          body.append(row);
          return;
        }
        matches.forEach(({ label, item }) => {
          const row = document.createElement("tr");
          const colors = item.cores?.length ? item.cores.join(", ") : asText(item.cor);
          const prices = item.precos_brl?.length ? item.precos_brl.map(formatPrice).join(" | ") : "—";
          [label, item.nome, item.capacidade, item.condicao, colors, prices, asText(item.quantidade), itemAvailability(item), batteryText(item.saude_bateria), asText(item.fotos_disponiveis, "0")].forEach((value) => {
            const cell = document.createElement("td");
            cell.textContent = asText(value);
            row.append(cell);
          });
          body.append(row);
        });
      }

      async function loadDashboard() {
        const status = byId("operations-status");
        status.className = "status muted";
        status.textContent = "Atualizando…";
        try {
          dashboard = await fetchJson("/admin/api/dashboard");
          renderDashboard();
          status.textContent = `Atualizado ${formatDate(dashboard.generated_at)}.`;
        } catch (error) {
          status.className = "status error";
          status.textContent = error.message;
        }
      }

      async function loadHumanQueue() {
        const status = byId("human-queue-status");
        status.className = "status muted";
        status.textContent = "Atualizando fila…";
        byId("refresh-queue").disabled = true;
        try {
          const result = await fetchJson("/admin/api/conversations?status=human");
          humanQueue = result.items || [];
          renderHumanQueue();
          status.textContent = `${humanQueue.length} conversa(s) em atendimento humano.`;
        } catch (error) {
          status.className = "status error";
          status.textContent = error.message;
        } finally {
          byId("refresh-queue").disabled = false;
        }
      }

      async function loadRecoveryQueue(append = false) {
        const status = byId("recovery-status");
        const summary = byId("recovery-summary");
        const hours = Number.parseFloat(byId("recovery-older-hours").value);
        if (!Number.isFinite(hours) || hours < 0 || hours > 8760) {
          status.className = "status error";
          status.textContent = "Informe uma janela entre 0 e 8760 horas.";
          return;
        }
        status.className = "status muted";
        status.textContent = append ? "Carregando mais conversas…" : "Atualizando fila…";
        byId("refresh-recovery").disabled = true;
        byId("recovery-more").disabled = true;
        try {
          const offset = append ? recoveryQueue.length : 0;
          const params = new URLSearchParams({ older_than_hours: String(hours), limit: "50", offset: String(offset) });
          const query = byId("recovery-search")?.value.trim() || "";
          if (query) params.set("search", query);
          const result = await fetchJson(`/admin/api/recovery?${params.toString()}`);
          recoveryTotal = Number(result.total || 0);
          recoveryQueue = append ? [...recoveryQueue, ...(result.items || [])] : (result.items || []);
          renderRecoveryQueue();
          summary.textContent = `${recoveryQueue.length} de ${recoveryTotal} conversa(s) carregada(s).`;
          byId("recovery-more").hidden = !result.has_more;
          status.textContent = `Fila atualizada ${formatDate(result.generated_at)}.`;
        } catch (error) {
          status.className = "status error";
          status.textContent = error.message;
        } finally {
          byId("refresh-recovery").disabled = false;
          byId("recovery-more").disabled = false;
        }
      }

      async function prepareRecovery(phone) {
        const status = byId("recovery-editor-status");
        status.className = "status muted";
        status.textContent = "Preparando rascunho…";
        byId("send-recovery-message").disabled = true;
        try {
          const result = await fetchJson("/admin/api/recovery/draft", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-Admin-CSRF": csrfToken },
            body: JSON.stringify({ phone })
          });
          recoveryDraft = result;
          renderRecoveryEditor();
          byId("recovery-editor").scrollIntoView({ behavior: "smooth", block: "start" });
          status.textContent = result.review_required
            ? "Rascunho pronto. Revise o alerta antes de enviar."
            : "Rascunho pronto para revisão.";
        } catch (error) {
          recoveryDraft = null;
          renderRecoveryEditor();
          status.className = "status error";
          status.textContent = error.message;
        }
      }

      async function skipRecovery(phone, expectedLastMessageId, button = null) {
        if (!phone || !expectedLastMessageId) return;
        if (!window.confirm("Pular esta conversa até o cliente enviar uma nova mensagem?")) return;
        const status = byId("recovery-status");
        status.className = "status muted";
        status.textContent = "Pulando conversa…";
        if (button) button.disabled = true;
        try {
          const result = await fetchJson("/admin/api/recovery/skip", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-Admin-CSRF": csrfToken },
            body: JSON.stringify({
              phone,
              expected_last_message_id: Number(expectedLastMessageId),
            })
          });
          if (recoveryDraft?.phone === phone) {
            recoveryDraft = null;
            renderRecoveryEditor();
          }
          await loadRecoveryQueue();
          await loadAudit();
          status.className = "status success";
          status.textContent = result.message || "Conversa pulada até chegar uma nova mensagem do cliente.";
        } catch (error) {
          status.className = "status error";
          status.textContent = error.message;
          if (button) button.disabled = false;
        }
      }

      function closeRecoveryEditor() {
        recoveryDraft = null;
        renderRecoveryEditor();
      }

      async function sendRecoveryMessage() {
        if (!recoveryDraft) return;
        const message = byId("recovery-message").value.trim();
        const status = byId("recovery-editor-status");
        if (!message) {
          status.className = "status error";
          status.textContent = "Escreva uma mensagem antes de enviar.";
          return;
        }
        const confirmation = `Enviar esta mensagem para ${recoveryDraft.phone}?\n\n${message}`;
        if (!window.confirm(confirmation)) return;
        status.className = "status muted";
        status.textContent = "Enviando…";
        byId("send-recovery-message").disabled = true;
        try {
          const response = await fetch("/admin/api/recovery/send", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-Admin-CSRF": csrfToken },
            body: JSON.stringify({
              phone: recoveryDraft.phone,
              message,
              expected_last_message_id: recoveryDraft.last_message_id
            })
          });
          const result = await response.json();
          if (!response.ok) throw new Error(result.detail || "Não foi possível enviar a resposta.");
          status.className = "status success";
          status.textContent = result.message;
          recoveryDraft = null;
          renderRecoveryEditor();
          await Promise.all([loadRecoveryQueue(), loadHumanQueue(), loadDashboard()]);
        } catch (error) {
          status.className = "status error";
          status.textContent = error.message;
          byId("send-recovery-message").disabled = false;
        }
      }

      async function loadAudit() {
        const status = byId("audit-status");
        status.className = "status muted";
        status.textContent = "Atualizando auditoria…";
        byId("refresh-audit").disabled = true;
        try {
          const result = await fetchJson("/admin/api/audit?limit=50");
          auditEvents = result.items || [];
          renderAudit();
          status.textContent = `${auditEvents.length} evento(s) carregado(s).`;
        } catch (error) {
          status.className = "status error";
          status.textContent = error.message;
        } finally {
          byId("refresh-audit").disabled = false;
        }
      }

      async function loadOperationalPanel() {
        await Promise.all([loadDashboard(), loadHumanQueue(), loadRecoveryQueue(), loadAudit()]);
      }

      async function loadCatalog() {
        const status = byId("catalog-status");
        status.className = "status muted";
        status.textContent = "Atualizando catálogo…";
        byId("refresh-catalog").disabled = true;
        try {
          const response = await fetch("/admin/api/catalog", { credentials: "same-origin" });
          if (!response.ok) throw new Error("Não foi possível carregar o catálogo.");
          catalog = await response.json();
          renderSummary();
          renderCatalogFilters();
          renderTable();
          status.textContent = `${catalog.total_modelos || 0} opção(ões) carregada(s).`;
        } catch (error) {
          status.className = "status error";
          status.textContent = error.message;
        } finally {
          byId("refresh-catalog").disabled = false;
        }
      }

      function updatePhoneField() {
        const individual = byId("command-action").value !== "release_all";
        byId("phone-field").hidden = !individual;
        byId("command-phone").required = individual;
        if (!individual) byId("command-phone").value = "";
      }

      function prepareCommand(action, phone) {
        byId("command-action").value = action;
        byId("command-phone").value = phone || "";
        updatePhoneField();
        byId("commands-title").scrollIntoView({ behavior: "smooth", block: "start" });
        invalidateCommandPreview();
        byId("command-preview").focus();
      }

      async function loadSessions() {
        const status = byId("sessions-status");
        status.className = "status muted";
        status.textContent = "Atualizando sessões…";
        try {
          const result = await fetchJson("/admin/api/sessions");
          const body = byId("sessions-body");
          body.replaceChildren();
          const items = result.items || [];
          if (!items.length) {
            const row = document.createElement("tr");
            const cell = document.createElement("td");
            cell.colSpan = 4;
            cell.className = "empty";
            cell.textContent = "Nenhuma sessão por navegador registrada.";
            row.append(cell);
            body.append(row);
          } else {
            items.forEach((item) => {
              const row = document.createElement("tr");
              appendCell(row, item.operator || "—");
              appendCell(row, formatDate(item.created_at), "nowrap");
              appendCell(row, formatDate(item.last_seen_at), "nowrap");
              appendCell(row, formatDate(item.expires_at), "nowrap");
              body.append(row);
            });
          }
          status.textContent = `${items.length} sessão(ões) ativa(s).`;
        } catch (error) {
          status.className = "status error";
          status.textContent = error.message;
        }
      }

      async function refreshSource(source) {
        const justification = window.prompt("Informe uma justificativa para atualizar a fonte:", "Atualização manual de monitoramento");
        if (!justification || !justification.trim()) return;
        try {
          const response = await fetch("/admin/api/monitoring/refresh", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-Admin-CSRF": csrfToken },
            body: JSON.stringify({ source, justification: justification.trim() })
          });
          const result = await response.json();
          if (!response.ok) throw new Error(result.detail || "Não foi possível atualizar a fonte.");
          byId("operations-status").className = "status success";
          byId("operations-status").textContent = result.message;
          await Promise.all([loadDashboard(), loadCatalog()]);
        } catch (error) {
          byId("operations-status").className = "status error";
          byId("operations-status").textContent = error.message;
        }
      }

      function commandValues() {
        return {
          action: byId("command-action").value,
          phone: byId("command-phone").value.trim(),
          justification: byId("command-justification").value.trim()
        };
      }

      function invalidateCommandPreview() {
        byId("command-submit").disabled = true;
        byId("command-submit").dataset.previewKey = "";
        byId("command-preview-box").hidden = true;
      }

      async function previewCommand() {
        const values = commandValues();
        const status = byId("command-status");
        if (values.action !== "release_all" && !values.phone) {
          status.className = "status error";
          status.textContent = "Informe o telefone da conversa.";
          return;
        }
        if (!values.justification) {
          status.className = "status error";
          status.textContent = "Informe uma justificativa para a ação.";
          return;
        }
        const button = byId("command-preview");
        status.className = "status muted";
        status.textContent = "Calculando impacto…";
        button.disabled = true;
        try {
          const response = await fetch("/admin/api/commands/preview", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-Admin-CSRF": csrfToken },
            body: JSON.stringify({ action: values.action, phone: values.phone || null })
          });
          const result = await response.json();
          if (!response.ok) throw new Error(result.detail || "Não foi possível pré-visualizar o comando.");
          const box = byId("command-preview-box");
          box.hidden = false;
          box.textContent = `${result.message} Impacto estimado: ${result.affected_count} conversa(s).`;
          byId("command-submit").disabled = false;
          byId("command-submit").dataset.previewKey = JSON.stringify({ action: values.action, phone: values.phone });
          status.textContent = "Prévia pronta. Revise o impacto e confirme a execução.";
        } catch (error) {
          status.className = "status error";
          status.textContent = error.message;
        } finally {
          button.disabled = false;
        }
      }

      async function executeCommand() {
        const values = commandValues();
        const button = byId("command-submit");
        if (button.disabled) return;
        const previewKey = button.dataset.previewKey || "";
        const currentKey = JSON.stringify({ action: values.action, phone: values.phone });
        if (previewKey !== currentKey) {
          byId("command-status").className = "status error";
          byId("command-status").textContent = "Faça uma nova prévia depois de alterar os dados.";
          invalidateCommandPreview();
          return;
        }
        const confirmation = `Executar agora?\n\n${byId("command-preview-box").textContent}`;
        if (!window.confirm(confirmation)) return;
        const status = byId("command-status");
        status.className = "status muted";
        status.textContent = "Executando…";
        button.disabled = true;
        try {
          const response = await fetch("/admin/api/commands", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-Admin-CSRF": csrfToken },
            body: JSON.stringify({ action: values.action, phone: values.phone || null, justification: values.justification })
          });
          const result = await response.json();
          if (!response.ok) throw new Error(result.detail || "Não foi possível executar o comando.");
          status.className = "status success";
          status.textContent = result.message;
          invalidateCommandPreview();
          await loadOperationalPanel();
        } catch (error) {
          status.className = "status error";
          status.textContent = error.message;
          button.disabled = false;
        }
      }

      async function loadControl() {
        try {
          const result = await fetchJson("/admin/api/control");
          byId("control-role").textContent = `Perfil: ${result.role || "owner"}`;
          const owner = result.permissions?.owner_controls !== false;
          byId("admin-control-form").querySelectorAll("input, select, button").forEach((element) => { element.disabled = !owner; });
          const state = result.state || {};
          const labels = { active: "Ativo", paused: "Pausado", maintenance: "Em manutenção" };
          byId("bot-control-status").textContent = `${labels[state.mode] || state.mode || "Ativo"}${state.reason ? ` · ${state.reason}` : ""}`;
        } catch (error) {
          byId("control-status").className = "status error";
          byId("control-status").textContent = error.message;
        }
      }

      byId("conversation-lookup-submit").addEventListener("click", lookupConversation);
      byId("conversation-lookup").addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          lookupConversation();
        }
      });
      byId("refresh-catalog").addEventListener("click", loadCatalog);
      byId("export-csv").addEventListener("click", exportCatalogCsv);
      byId("export-pdf").addEventListener("click", exportCatalogPdf);
      sectionDefinitions.forEach(([key]) => byId(`export-${key}`).addEventListener("change", updateExportStatus));
      updateExportStatus();
      byId("catalog-search").addEventListener("input", renderTable);
      byId("refresh-dashboard").addEventListener("click", loadDashboard);
      byId("refresh-queue").addEventListener("click", loadHumanQueue);
      byId("refresh-recovery").addEventListener("click", loadRecoveryQueue);
      byId("recovery-more").addEventListener("click", () => loadRecoveryQueue(true));
      byId("refresh-audit").addEventListener("click", loadAudit);
      byId("human-queue-search").addEventListener("input", renderHumanQueue);
      byId("recovery-search")?.addEventListener("input", () => {
        renderRecoveryQueue();
        window.clearTimeout(recoverySearchTimer);
        recoverySearchTimer = window.setTimeout(() => loadRecoveryQueue(), 250);
      });
      byId("recovery-older-hours").addEventListener("change", loadRecoveryQueue);
      byId("audit-search").addEventListener("input", renderAudit);
      ["catalog-category-filter", "catalog-capacity-filter", "catalog-color-filter", "catalog-condition-filter", "catalog-stock-filter", "catalog-price-min", "catalog-price-max", "catalog-photos-filter"].forEach((id) => {
        byId(id).addEventListener("input", renderTable);
        byId(id).addEventListener("change", renderTable);
      });
      byId("health-grid").addEventListener("click", (event) => {
        const button = event.target.closest("button[data-source]");
        if (button) refreshSource(button.dataset.source);
      });
      byId("refresh-sessions").addEventListener("click", loadSessions);
      byId("human-queue-body").addEventListener("click", (event) => {
        const button = event.target.closest("button[data-action]");
        if (button) prepareCommand(button.dataset.action, button.dataset.phone);
      });
      byId("recovery-queue-body").addEventListener("click", (event) => {
        const button = event.target.closest("button[data-recovery-action]");
        if (!button) return;
        if (button.dataset.recoveryAction === "skip") {
          skipRecovery(
            button.dataset.recoveryPhone,
            button.dataset.recoveryLastMessageId,
            button,
          );
          return;
        }
        prepareRecovery(button.dataset.recoveryPhone);
      });
      byId("close-recovery-editor").addEventListener("click", closeRecoveryEditor);
      byId("skip-recovery-message").addEventListener("click", () => {
        if (recoveryDraft) {
          skipRecovery(recoveryDraft.phone, recoveryDraft.last_message_id, byId("skip-recovery-message"));
        }
      });
      byId("send-recovery-message").addEventListener("click", sendRecoveryMessage);
      byId("recovery-message").addEventListener("input", () => {
        if (recoveryDraft) {
          const message = byId("recovery-message").value.trim();
          const unchangedAttachmentDraft = recoveryDraft.send_allowed === false && message === recoveryDraft.draft;
          byId("send-recovery-message").disabled = !message || unchangedAttachmentDraft;
        }
      });
      byId("command-action").addEventListener("change", () => { updatePhoneField(); invalidateCommandPreview(); });
      byId("command-form").addEventListener("submit", (event) => { event.preventDefault(); previewCommand(); });
      byId("command-preview").addEventListener("click", (event) => { event.preventDefault(); previewCommand(); });
      byId("command-submit").addEventListener("click", executeCommand);
      ["command-action", "command-phone", "command-justification"].forEach((id) => {
        byId(id).addEventListener("input", invalidateCommandPreview);
      });
      byId("admin-control-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const action = byId("control-action").value;
        const reason = byId("control-reason").value.trim();
        const justification = byId("control-justification").value.trim();
        if (!justification) {
          byId("control-status").className = "status error";
          byId("control-status").textContent = "Informe uma justificativa para o controle.";
          return;
        }
        const confirmation = action === "logout_sessions"
          ? "Desconectar todas as sessões administrativas por navegador? Você também precisará entrar novamente."
          : "Aplicar esta alteração ao estado global do robô? As mensagens recebidas ficarão aguardando enquanto ele estiver pausado ou em manutenção.";
        if (!window.confirm(confirmation)) return;
        const status = byId("control-status");
        const button = byId("control-submit");
        status.className = "status muted";
        status.textContent = "Aplicando…";
        button.disabled = true;
        try {
          const response = await fetch("/admin/api/control", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-Admin-CSRF": csrfToken },
            body: JSON.stringify({ action, reason: reason || null, justification })
          });
          const result = await response.json();
          if (!response.ok) throw new Error(result.detail || "Não foi possível aplicar o controle.");
          status.className = "status success";
          status.textContent = result.message;
          if (action === "logout_sessions") {
            status.textContent += " Atualize a página para entrar novamente.";
          } else {
            await Promise.all([loadDashboard(), loadSessions()]);
          }
        } catch (error) {
          status.className = "status error";
          status.textContent = error.message;
        } finally {
          button.disabled = false;
        }
      });

      document.addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof Element)) return;
        const toggle = target.closest("[data-panel-toggle]");
        if (!toggle) return;
        const panel = toggle.closest("section.panel[data-panel-key]");
        if (!panel) return;
        setPanelExpanded(panel, toggle.getAttribute("aria-expanded") !== "true");
      });
      initializeCollapsiblePanels();
      updatePhoneField();
      invalidateCommandPreview();
      loadCatalog();
      loadOperationalPanel();
      loadControl();
      loadSessions();
    })();
  </script>
</body>
</html>
"""


def render_admin_page(csrf_token: str) -> str:
    """Render the static admin shell with its per-configuration CSRF token."""
    token_literal = json.dumps(str(csrf_token), ensure_ascii=True)
    token_literal = token_literal.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return _PAGE_TEMPLATE.replace("__CSRF_TOKEN__", token_literal)


def render_admin_login_page(error: str | None = None) -> str:
    """Render a browser-friendly login form without exposing admin state."""
    error_html = ""
    if error:
        error_html = f'<p class="error" role="alert">{html.escape(error)}</p>'
    return _LOGIN_PAGE_TEMPLATE.replace("__ERROR__", error_html)
