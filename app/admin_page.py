from __future__ import annotations

import json


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
    .summary-grid { display: grid; gap: 12px; grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .summary-card { background: var(--soft); border: 1px solid var(--line); border-radius: 12px; padding: 15px; }
    .summary-card strong { display: block; font-size: 25px; line-height: 1.15; margin-top: 3px; }
    .summary-card span { color: var(--muted); font-size: 13px; }
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
    .command-grid { align-items: end; display: grid; gap: 12px; grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) auto; }
    .field { display: grid; gap: 5px; }
    label { font-size: 13px; font-weight: 700; }
    .warning { background: #fffaeb; border: 1px solid #fedf89; border-radius: 9px; color: #7a2e0b; font-size: 13px; margin: 14px 0 0; padding: 10px 12px; }
    .empty { color: var(--muted); padding: 22px 12px; text-align: center; }
    @media (max-width: 820px) {
      header { flex-direction: column; }
      .actions { width: 100%; }
      .actions > * { flex: 1; text-align: center; }
      .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .command-grid { grid-template-columns: 1fr; }
      .command-grid button { width: 100%; }
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
        <a class="button-link secondary" href="/admin/api/catalog.csv">Baixar CSV</a>
      </div>
    </header>

    <section class="panel" aria-labelledby="catalog-title">
      <h2 id="catalog-title">Catálogo de disponíveis</h2>
      <p class="muted">A lista usa o mesmo catálogo do robô: estoque físico do Mercado Phone e preços de lacrados por encomenda da planilha.</p>
      <div class="summary-grid" aria-label="Resumo do catálogo">
        <div class="summary-card"><span>Total de opções</span><strong id="catalog-total">—</strong></div>
        <div class="summary-card"><span>Seminovos</span><strong id="count-seminovos">—</strong></div>
        <div class="summary-card"><span>Lacrados em estoque</span><strong id="count-lacrados-pronta-entrega">—</strong></div>
        <div class="summary-card"><span>Lacrados por encomenda</span><strong id="count-lacrados">—</strong></div>
      </div>
      <div class="toolbar">
        <input id="catalog-search" type="search" placeholder="Buscar modelo, capacidade ou cor" autocomplete="off" aria-label="Buscar no catálogo">
        <span id="catalog-status" class="status muted" role="status" aria-live="polite">Carregando catálogo…</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Categoria</th><th>Produto</th><th>Capacidade</th><th>Condição</th><th>Cor(es)</th><th>Preço(s)</th><th>Quantidade</th><th>Bateria</th><th>Fotos</th></tr>
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

    <section class="panel" aria-labelledby="commands-title">
      <h2 id="commands-title">Comandos operacionais</h2>
      <p class="muted">As ações abaixo alteram o estado das conversas e ficam registradas na auditoria.</p>
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
          <button id="command-submit" class="danger" type="submit">Executar comando</button>
        </div>
        <p class="warning">“Liberar todos” reativa somente conversas em atendimento humano; conversas encerradas permanecem encerradas.</p>
        <p id="command-status" class="status" role="status" aria-live="polite"></p>
      </form>
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

      const byId = (id) => document.getElementById(id);
      const formatPrice = (value) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(Number(value));
      const formatDate = (value) => {
        if (!value) return "não carregado";
        const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
        return Number.isNaN(date.getTime()) ? "não disponível" : date.toLocaleString("pt-BR");
      };
      const itemText = (item) => [item.nome, item.capacidade, item.condicao, item.cor, ...(item.cores || [])].join(" ").toLocaleLowerCase();
      const asText = (value, fallback = "—") => value === null || value === undefined || value === "" ? fallback : String(value);
      const batteryText = (value) => value === null || value === undefined || value === "" ? "—" : `${value}%`;

      function flattenItems() {
        return sectionDefinitions.flatMap(([key, label]) => (catalog?.[key] || []).map((item) => ({ key, label, item })));
      }

      function renderSummary() {
        byId("catalog-total").textContent = asText(catalog?.total_modelos, "0");
        sectionDefinitions.forEach(([key]) => { byId(`count-${key}`).textContent = String((catalog?.[key] || []).length); });
        byId("mercado-source").textContent = `Mercado Phone: ${formatDate(catalog?.sources?.mercado_phone_last_refresh)}`;
        byId("sheets-source").textContent = `Google Sheets: ${formatDate(catalog?.sources?.google_sheets_last_refresh)}`;
        byId("generated-source").textContent = `Página gerada: ${formatDate(catalog?.generated_at)}`;
      }

      function renderTable() {
        const body = byId("catalog-body");
        const query = byId("catalog-search").value.trim().toLocaleLowerCase();
        body.replaceChildren();
        const matches = flattenItems().filter(({ item }) => !query || itemText(item).includes(query));
        if (!matches.length) {
          const row = document.createElement("tr");
          const cell = document.createElement("td");
          cell.colSpan = 9;
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
          [label, item.nome, item.capacidade, item.condicao, colors, prices, asText(item.quantidade), batteryText(item.saude_bateria), asText(item.fotos_disponiveis, "0")].forEach((value) => {
            const cell = document.createElement("td");
            cell.textContent = asText(value);
            row.append(cell);
          });
          body.append(row);
        });
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

      byId("refresh-catalog").addEventListener("click", loadCatalog);
      byId("catalog-search").addEventListener("input", renderTable);
      byId("command-action").addEventListener("change", updatePhoneField);
      byId("command-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const action = byId("command-action").value;
        const phone = byId("command-phone").value.trim();
        const needsPhone = action !== "release_all";
        if (needsPhone && !phone) {
          byId("command-status").className = "status error";
          byId("command-status").textContent = "Informe o telefone da conversa.";
          return;
        }
        const confirmation = action === "release_all"
          ? "Liberar todas as conversas em atendimento humano para o robô? Conversas encerradas não serão alteradas."
          : "Executar este comando na conversa informada?";
        if (!window.confirm(confirmation)) return;
        const status = byId("command-status");
        const button = byId("command-submit");
        status.className = "status muted";
        status.textContent = "Executando…";
        button.disabled = true;
        try {
          const response = await fetch("/admin/api/commands", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-Admin-CSRF": csrfToken },
            body: JSON.stringify({ action, phone: phone || null })
          });
          const result = await response.json();
          if (!response.ok) throw new Error(result.detail || "Não foi possível executar o comando.");
          status.className = "status success";
          status.textContent = result.message;
          if (action === "release_all") await loadCatalog();
        } catch (error) {
          status.className = "status error";
          status.textContent = error.message;
        } finally {
          button.disabled = false;
        }
      });

      updatePhoneField();
      loadCatalog();
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
