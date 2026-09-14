# Página administrativa do robô: catálogo e comandos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar uma página `/admin` autenticada para consultar/exportar o catálogo completo e executar com confirmação os comandos operacionais do robô.

**Architecture:** Extrair as transições de conversa para `AdminCommandService`, compartilhado pelo WhatsApp e pelo painel. Adicionar rotas FastAPI protegidas por Basic Auth + CSRF, serializadores que expõem somente campos públicos do catálogo e uma página HTML responsiva sem dependências frontend adicionais.

**Tech Stack:** Python 3.11, FastAPI, Pydantic Settings, SQLAlchemy, pytest, HTML/CSS/JavaScript vanilla.

**Spec:** `docs/superpowers/specs/2026-09-14-admin-page-design.md`

## Global Constraints

- O painel só opera com `ADMIN_USERNAME`, `ADMIN_PASSWORD` e `ADMIN_CSRF_SECRET` configurados; sem qualquer um deles, as rotas administrativas respondem `404`.
- O catálogo deve chamar `StoreCatalogCache.list_available_products()` e preservar separadamente estoque físico do Mercado Phone e preços de lacrados por encomenda do Google Sheets.
- Nenhuma rota administrativa pode expor IDs internos, IMEI, serial, URLs de fotos ou dados de conversas.
- Ações permitidas são somente `release_all`, `assume`, `resume` e `close`; não haverá endpoint genérico para executar texto ou shell.
- Toda ação mutável exige Basic Auth, token CSRF e confirmação no navegador; toda ação executada gera auditoria.
- Os comandos existentes no WhatsApp e suas respostas atuais devem continuar funcionando.
- Usar `C:\Projetos\Robo loja ok\.venv\Scripts\python.exe -m pytest` para os testes locais.

---

### Task 1: Extrair o serviço compartilhado de comandos administrativos

**Files:**
- Create: `app/admin.py`
- Create: `tests/test_admin_commands.py`
- Modify: `app/processor.py:300-341`
- Test: `tests/test_bulk_release.py`

**Interfaces:**
- Produces `AdminCommandService(repository).execute(action, operator, channel, phone=None) -> dict[str, Any]`.
- Canonical actions: `release_all`, `assume`, `resume`, `close`.
- Result fields: `action`, `phone`, `status`, `released_count`, `message`.
- Raises `ValueError` for action desconhecida, telefone ausente em ação individual ou telefone fora de 10-15 dígitos.

- [ ] **Step 1: Write the failing tests**

```python
def test_release_all_changes_human_conversations_but_not_closed(runtime):
    service = AdminCommandService(runtime.repository)
    result = service.execute("release_all", operator="admin", channel="web")
    assert result["released_count"] == 2
    assert runtime.repository.get_conversation("5511888888888").status == "bot_active"
    assert runtime.repository.get_conversation("5511666666666").status == "closed"


def test_individual_admin_command_normalizes_phone_and_records_web_audit(runtime):
    service = AdminCommandService(runtime.repository)
    result = service.execute("assume", operator="admin", channel="web", phone="+55 (41) 97777-6666")
    assert result["status"] == "human_active"
    assert runtime.repository.get_conversation("5541977776666").status == "human_active"


def test_admin_command_rejects_unknown_action_and_missing_phone(runtime):
    service = AdminCommandService(runtime.repository)
    with pytest.raises(ValueError):
        service.execute("run_shell", operator="admin", channel="web")
    with pytest.raises(ValueError):
        service.execute("close", operator="admin", channel="web")
```

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run: `C:\Projetos\Robo loja ok\.venv\Scripts\python.exe -m pytest tests/test_admin_commands.py -q --basetemp=.pytest-basetemp-admin-commands-red-20260914`

Expected: FAIL because `app.admin.AdminCommandService` does not exist.

- [ ] **Step 3: Implement the minimal command service**

Implement the four action mappings, call only the existing repository methods, normalize and validate individual phone numbers with `normalize_phone`, write one `admin_command` audit event containing `operator`, `channel`, `action` and `released_count`, and return a deterministic Portuguese confirmation message.

- [ ] **Step 4: Refactor `MessageProcessor` to use the service**

Keep the current authorization check against `settings.admin_phone_set`, map WhatsApp aliases (`retomar_todos`/`liberar_todos` to `release_all`, `assumir` to `assume`, `retomar` to `resume`, `fechar` to `close`), preserve the current Z-API confirmation text, and leave unauthorized commands audited without changing conversation state.

- [ ] **Step 5: Run the focused and existing command tests**

Run: `C:\Projetos\Robo loja ok\.venv\Scripts\python.exe -m pytest tests/test_admin_commands.py tests/test_bulk_release.py tests/test_acceptance_flow.py -q --basetemp=.pytest-basetemp-admin-commands-green-20260914`

Expected: PASS with no failures.

- [ ] **Step 6: Commit the task**

```powershell
git add -- app/admin.py app/processor.py tests/test_admin_commands.py tests/test_bulk_release.py
git commit -m "refactor: share admin conversation commands"
```

---

### Task 2: Add administrative settings, authentication, CSRF and safe catalog serialization

**Files:**
- Modify: `app/config.py:20-85`
- Modify: `app/admin.py`
- Create: `tests/test_admin_serialization.py`
- Test: `tests/test_admin_commands.py`

**Interfaces:**
- `Settings.admin_username`, `Settings.admin_password`, `Settings.admin_csrf_secret` are optional strings.
- `Settings.admin_panel_configured` is true only when all three values are non-empty after trimming.
- `build_admin_csrf_token(settings) -> str` returns a deterministic HMAC token without exposing the secret.
- `public_catalog_payload(result, mercado_refresh, sheets_refresh, generated_at) -> dict[str, Any]` returns only the public JSON contract.
- `catalog_csv_bytes(payload) -> bytes` returns UTF-8 BOM CSV with semicolon delimiters and stable columns.

- [ ] **Step 1: Write the failing serialization and configuration tests**

```python
def test_admin_panel_requires_all_three_credentials():
    assert Settings(admin_username="", admin_password="secret", admin_csrf_secret="csrf").admin_panel_configured is False
    assert Settings(admin_username="admin", admin_password="secret", admin_csrf_secret="csrf").admin_panel_configured is True


def test_public_catalog_filters_private_fields_and_keeps_sections():
    payload = public_catalog_payload(
        {
            "seminovos": [{"nome": "iPhone 15", "external_id": "secret-id", "precos_brl": [1900.0]}],
            "lacrados_pronta_entrega": [],
            "lacrados": [],
        },
        mercado_refresh=100.0,
        sheets_refresh=200.0,
        generated_at="2026-09-14T12:00:00+00:00",
    )
    assert payload["seminovos"][0]["nome"] == "iPhone 15"
    assert "external_id" not in payload["seminovos"][0]


def test_catalog_csv_has_excel_columns_and_all_sections():
    csv_data = catalog_csv_bytes(public_catalog_payload(SAMPLE_CATALOG, 100.0, 200.0, "now"))
    assert csv_data.startswith(b"\\xef\\xbb\\xbf")
    text = csv_data.decode("utf-8-sig")
    assert "Categoria;Produto;Capacidade;Condição;Cor(es);Preço(s);Quantidade;Saúde da bateria;Fotos disponíveis" in text
    assert "Seminovos;iPhone 15" in text
    assert "Lacrados por encomenda;iPhone 17" in text
```

- [ ] **Step 2: Run the tests and verify the expected failure**

Run: `C:\Projetos\Robo loja ok\.venv\Scripts\python.exe -m pytest tests/test_admin_serialization.py -q --basetemp=.pytest-basetemp-admin-serialization-red-20260914`

Expected: FAIL because the settings fields and serializer functions do not exist.

- [ ] **Step 3: Implement settings and serializers**

Add the three environment-backed settings and a fail-closed property. Whitelist exactly the public item fields (`nome`, `capacidade`, `condicao`, `quantidade`, `precos_brl`, `cores`, `cor`, `saude_bateria`, `fotos_disponiveis`), label the three sections in Portuguese, add generated/source refresh timestamps, and use `csv.DictWriter` with `delimiter=";"`, `lineterminator="\r\n"`, and UTF-8 BOM.

- [ ] **Step 4: Run the focused tests**

Run: `C:\Projetos\Robo loja ok\.venv\Scripts\python.exe -m pytest tests/test_admin_serialization.py tests/test_admin_commands.py -q --basetemp=.pytest-basetemp-admin-serialization-green-20260914`

Expected: PASS with no failures.

- [ ] **Step 5: Commit the task**

```powershell
git add -- app/admin.py app/config.py tests/test_admin_serialization.py
git commit -m "feat: add admin auth settings and catalog export"
```

---

### Task 3: Expose authenticated admin API routes

**Files:**
- Modify: `app/main.py:1-155`
- Modify: `app/admin.py`
- Create: `tests/test_admin_app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- `GET /admin` returns the page only after valid Basic Auth.
- `GET /admin/api/catalog` calls `current.cache.list_available_products()` and returns the safe catalog payload.
- `GET /admin/api/catalog.csv` returns the same data as an attachment named `catalogo-disponiveis.csv`.
- `POST /admin/api/commands` accepts `{"action": "release_all|assume|resume|close", "phone": "..."}` and returns the command result.

- [ ] **Step 1: Write the failing route tests**

```python
def test_admin_routes_fail_closed_without_configuration(fake_runtime):
    fake_runtime.settings = Settings(database_url="sqlite:///:memory:")
    with TestClient(create_app(fake_runtime)) as client:
        assert client.get("/admin").status_code == 404
        assert client.get("/admin/api/catalog").status_code == 404


def test_admin_catalog_requires_basic_auth_and_returns_json_and_csv(configured_runtime):
    with TestClient(create_app(configured_runtime)) as client:
        assert client.get("/admin").status_code == 401
        json_response = client.get("/admin/api/catalog", auth=("admin", "secret"))
        csv_response = client.get("/admin/api/catalog.csv", auth=("admin", "secret"))
    assert json_response.status_code == 200
    assert json_response.json()["seminovos"]
    assert csv_response.status_code == 200
    assert "catalogo-disponiveis.csv" in csv_response.headers["content-disposition"]


def test_admin_command_requires_csrf_and_executes_release_all(configured_runtime):
    with TestClient(create_app(configured_runtime)) as client:
        blocked = client.post("/admin/api/commands", json={"action": "release_all"}, auth=("admin", "secret"))
        token = build_admin_csrf_token(configured_runtime.settings)
        accepted = client.post(
            "/admin/api/commands",
            json={"action": "release_all"},
            headers={"X-Admin-CSRF": token},
            auth=("admin", "secret"),
        )
    assert blocked.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["released_count"] == 2
```

- [ ] **Step 2: Run the tests and verify the expected failure**

Run: `C:\Projetos\Robo loja ok\.venv\Scripts\python.exe -m pytest tests/test_admin_app.py -q --basetemp=.pytest-basetemp-admin-app-red-20260914`

Expected: FAIL because the `/admin` routes do not exist.

- [ ] **Step 3: Implement authentication and routes**

Parse Basic Auth with constant-time comparisons, return `401` plus `WWW-Authenticate: Basic` for invalid configured credentials, return `404` when configuration is incomplete, verify the CSRF header with constant-time comparison, call the serializer for catalog responses, and map `ValueError` to `400` without modifying the database. Log unexpected catalog/command exceptions and return generic `503`/`500` responses.

- [ ] **Step 4: Run focused route and existing app tests**

Run: `C:\Projetos\Robo loja ok\.venv\Scripts\python.exe -m pytest tests/test_admin_app.py tests/test_app.py -q --basetemp=.pytest-basetemp-admin-app-green-20260914`

Expected: PASS with no failures.

- [ ] **Step 5: Commit the task**

```powershell
git add -- app/admin.py app/main.py tests/test_admin_app.py tests/test_app.py
git commit -m "feat: expose authenticated admin API"
```

---

### Task 4: Build the responsive administrative page

**Files:**
- Create: `app/admin_page.py`
- Modify: `app/main.py`
- Create: `tests/test_admin_page.py`
- Test: `tests/test_admin_app.py`

**Interfaces:**
- `render_admin_page(csrf_token: str) -> str` returns complete HTML with no user/catalog data interpolated into JavaScript.
- The page fetches `/admin/api/catalog` with same-origin credentials, renders cards/table using `textContent`, and submits commands with `X-Admin-CSRF`.

- [ ] **Step 1: Write the failing page tests**

```python
def test_admin_page_contains_catalog_controls_and_command_controls():
    html = render_admin_page("csrf-token")
    assert "Catálogo de disponíveis" in html
    assert "Baixar CSV" in html
    assert "Atualizar catálogo" in html
    assert "Liberar todos os clientes" in html
    assert "X-Admin-CSRF" in html


def test_admin_page_does_not_render_catalog_values_as_inner_html():
    html = render_admin_page("csrf-token")
    assert "textContent" in html
```

- [ ] **Step 2: Run the tests and verify the expected failure**

Run: `C:\Projetos\Robo loja ok\.venv\Scripts\python.exe -m pytest tests/test_admin_page.py -q --basetemp=.pytest-basetemp-admin-page-red-20260914`

Expected: FAIL because `app.admin_page` does not exist.

- [ ] **Step 3: Implement the page**

Create a self-contained HTML page with responsive CSS, summary cards, search field, refresh button, CSV link, source timestamps, catalog table and command form. The command form must hide/disable the phone field for `release_all`, require a phone for individual actions, display a browser confirmation before POST, and show success/error text returned by the API. Use `Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" })` for prices and DOM node `textContent` for every dynamic catalog field.

- [ ] **Step 4: Serve the page from `GET /admin` and run page tests**

Run: `C:\Projetos\Robo loja ok\.venv\Scripts\python.exe -m pytest tests/test_admin_page.py tests/test_admin_app.py -q --basetemp=.pytest-basetemp-admin-page-green-20260914`

Expected: PASS with no failures.

- [ ] **Step 5: Commit the task**

```powershell
git add -- app/admin_page.py app/main.py tests/test_admin_page.py tests/test_admin_app.py
git commit -m "feat: add admin catalog and command page"
```

---

### Task 5: Document deployment configuration and operational behavior

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/deploy.md`
- Test: `tests/test_admin_app.py`

- [ ] **Step 1: Add the configuration example**

Insert the following block near `ADMIN_PHONES` in `.env.example`:

```dotenv
# Painel administrativo privado; mantenha as três variáveis preenchidas em produção.
ADMIN_USERNAME=admin
ADMIN_PASSWORD=troque-por-uma-senha-forte
ADMIN_CSRF_SECRET=troque-por-um-segredo-aleatorio
```

- [ ] **Step 2: Document access and command semantics**

Document `/admin`, HTTPS, Basic Auth, the CSV export, the three catalog sources, and the four command actions. Explicitly state that `release_all` changes only `human_pending`/`human_active`, never `closed`, and that the page does not edit inventory or send client messages.

- [ ] **Step 3: Run documentation/configuration smoke checks**

Run: `rg -n "ADMIN_USERNAME|ADMIN_PASSWORD|ADMIN_CSRF_SECRET|/admin|release_all|catalogo-disponiveis" .env.example README.md docs/deploy.md`

Expected: every configuration and operational term appears in the intended documentation.

- [ ] **Step 4: Commit the task**

```powershell
git add -- .env.example README.md docs/deploy.md
git commit -m "docs: document admin panel configuration"
```

---

### Task 6: Full verification and handoff

**Files:**
- Test: `tests/test_admin_commands.py`
- Test: `tests/test_admin_serialization.py`
- Test: `tests/test_admin_app.py`
- Test: `tests/test_admin_page.py`
- Test: `tests/test_bulk_release.py`
- Test: full `tests/` suite

- [ ] **Step 1: Run the new feature suite serially**

Run: `C:\Projetos\Robo loja ok\.venv\Scripts\python.exe -m pytest tests/test_admin_commands.py tests/test_admin_serialization.py tests/test_admin_app.py tests/test_admin_page.py tests/test_bulk_release.py -q --basetemp=.pytest-basetemp-admin-feature-full-20260914`

Expected: all selected tests pass.

- [ ] **Step 2: Run the complete repository suite with a fresh workspace-local basetemp**

Run: `C:\Projetos\Robo loja ok\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-basetemp-admin-full-20260914`

Expected: exit code 0 and no failures/errors.

- [ ] **Step 3: Inspect the final diff and status**

Run: `git diff HEAD~5..HEAD --stat; git diff HEAD~5..HEAD --check; git status --short`

Expected: only the planned files are committed by this implementation, `git diff --check` is empty, and unrelated pre-existing untracked test artifacts remain untouched.

- [ ] **Step 4: Manually verify the local page contract without external writes**

Run: `C:\Projetos\Robo loja ok\.venv\Scripts\python.exe -c "from app.config import Settings; from app.admin import build_admin_csrf_token; from app.admin_page import render_admin_page; s=Settings(admin_username='admin', admin_password='secret', admin_csrf_secret='csrf'); html=render_admin_page(build_admin_csrf_token(s)); assert '/admin/api/catalog' in html and 'X-Admin-CSRF' in html; print('ADMIN_PAGE_SMOKE_OK')"`

Expected: `ADMIN_PAGE_SMOKE_OK`.

- [ ] **Step 5: Report exact verification evidence and the local URL**

Report the test counts, the protected route behavior, the command semantics, and that production deployment was not performed because the user requested implementation only.
