from __future__ import annotations

import html
import json
from typing import Any


def render_instagram_page(csrf_token: str, status: dict[str, Any]) -> str:
    connected = bool(status.get("connected"))
    enabled = bool(status.get("enabled"))
    username = str(status.get("username") or "")
    last_post = status.get("last_post") if isinstance(status.get("last_post"), dict) else {}
    account_text = f"@{username}" if username else ("Autorização salva; perfil ainda não identificado" if connected else "Não conectado")
    post_text = "Nenhuma publicação automática registrada"
    if last_post:
        post_text = f"Último post: {last_post.get('date', '—')} · {last_post.get('title', 'publicado')}"
    safe_status = {
        "connected": connected,
        "enabled": enabled,
        "account": account_text,
        "last_post": post_text,
        "schedule_time": str(status.get("schedule_time") or "12:00"),
        "timezone": str(status.get("timezone") or "America/Sao_Paulo"),
        "oauth_ready": bool(status.get("oauth_ready")),
        "account_id_configured": bool(status.get("account_id_configured")),
    }
    secret_form = (
        '<form id="app-secret-form" autocomplete="off">'
        '<label for="app-secret">Segredo do app da Meta</label>'
        '<input id="app-secret" type="password" minlength="16" maxlength="512" '
        'autocomplete="new-password" required>'
        '<button class="button primary" type="submit">Salvar segredo com segurança</button>'
        '<p id="secret-message" class="small" role="status" aria-live="polite"></p>'
        '</form>'
    )
    if not status.get("oauth_ready"):
        connect_button = '<button class="button primary" type="button" disabled>Conectar conta Meta</button>'
        setup_notice = (
            '<p class="small">Para liberar a conexão, configure o segredo do app Meta. '
            'Ele será enviado somente a este painel HTTPS e guardado criptografado no banco de dados.</p>'
            f'{secret_form}'
        )
    elif status.get("app_secret_source") == "database":
        connect_button = (
            '<a class="button primary" href="/admin/instagram/connect">Conectar conta Meta</a>'
            if not connected
            else '<a class="button" href="/admin/instagram/connect">Reconectar conta Meta</a>'
        )
        setup_notice = (
            '<details><summary class="small">Substituir o segredo do app</summary>'
            f'{secret_form}</details>'
        )
    else:
        connect_button = (
            '<a class="button primary" href="/admin/instagram/connect">Conectar conta Meta</a>'
            if not connected
            else '<a class="button" href="/admin/instagram/connect">Reconectar conta Meta</a>'
        )
        setup_notice = '<p class="small">Segredo do app configurado no servidor.</p>'
    action_button = (
        '<button id="toggle" class="button danger" type="button">Pausar publicações</button>'
        if enabled
        else '<button id="toggle" class="button primary" type="button">Ativar publicações diárias</button>'
    )
    action_disabled = "" if connected and status.get("account_id_configured") else " disabled"
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Instagram · CWB.IPHONES</title>
<style>
  :root {{ color-scheme: dark; font-family: Inter,Segoe UI,Arial,sans-serif; background:#101827; color:#f5f7fb; }}
  body {{ margin:0; padding:32px 16px; }} main {{ max-width:760px; margin:auto; }}
  .card {{ background:#172236; border:1px solid #30415e; border-radius:18px; padding:28px; margin:18px 0; }}
  h1 {{ margin:0 0 8px; font-size:30px; }} h2 {{ margin:0 0 16px; font-size:18px; }}
  p {{ color:#c5cede; line-height:1.55; }} .state {{ font-size:18px; color:#fff; }}
  .button {{ display:inline-block; border:1px solid #66738b; border-radius:10px; background:#26364f; color:#fff; padding:12px 18px; font-weight:700; cursor:pointer; text-decoration:none; }}
  .primary {{ background:#bd2449; border-color:#bd2449; }} .danger {{ background:#4b2633; border-color:#914159; }}
  button:disabled {{ opacity:.45; cursor:not-allowed; }} .small {{ color:#99a8bf; font-size:13px; }}
  input {{ display:block; width:min(100%,440px); box-sizing:border-box; margin:8px 0 16px; border:1px solid #53617a; border-radius:8px; background:#0e1726; color:white; padding:10px; }}
  #message {{ min-height:24px; color:#ffd1d9; }} a {{ color:#f3a2b5; }}
</style></head><body><main>
  <h1>Instagram da loja</h1>
  <p>Posts informativos serão gerados com a IA, levando em conta as legendas recentes do perfil, e publicados automaticamente uma vez por dia.</p>
  <section class="card">
    <h2>Conta e publicação</h2>
    <p class="state" id="account">{html.escape(account_text)}</p>
    <p id="enabled">{"Publicação diária ativa" if enabled else "Publicação diária pausada"}</p>
    <p id="last">{html.escape(post_text)}</p>
    <p class="small">Horário padrão: {html.escape(safe_status['schedule_time'])} · fuso {html.escape(safe_status['timezone'])}. O app usa apenas instagram_basic e instagram_content_publish. Não acessa anúncios, mensagens nem comentários.</p>
    {setup_notice}
    {connect_button}
    <div style="height:14px"></div>
    <label for="justification">Motivo para ativar ou pausar</label>
    <input id="justification" maxlength="250" value="Configuração da publicação diária do Instagram">
    {action_button}
    <p id="message" role="status" aria-live="polite"></p>
  </section>
  <section class="card"><h2>Como os posts são preparados</h2>
    <p>O conteúdo será educativo e não incluirá preço, estoque, promoção ou garantia. A imagem é montada no servidor e a legenda é gerada pela IA. A análise lê o perfil e as publicações recentes; métricas de alcance exigiriam outra permissão.</p>
    <p class="small">A autorização da conta e a credencial do app ficam no servidor. Nenhum token é mostrado nesta página.</p>
  </section>
</main>
<script>
const csrf = {json.dumps(csrf_token)};
const secretForm = document.getElementById('app-secret-form');
if (secretForm) secretForm.addEventListener('submit', async (event) => {{
  event.preventDefault();
  const input = document.getElementById('app-secret');
  const message = document.getElementById('secret-message');
  const button = secretForm.querySelector('button[type="submit"]');
  button.disabled = true;
  message.textContent = 'Salvando de forma criptografada…';
  try {{
    const response = await fetch('/admin/api/instagram/app-secret', {{
      method: 'POST', headers: {{'Content-Type':'application/json','x-admin-csrf':csrf}},
      body: JSON.stringify({{app_secret: input.value}})
    }});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Não foi possível salvar o segredo.');
    input.value = '';
    message.textContent = data.message;
    setTimeout(() => location.reload(), 800);
  }} catch (error) {{
    message.textContent = error.message;
    button.disabled = false;
  }}
}});
document.getElementById('toggle').addEventListener('click', async () => {{
  const enabled = {str(not enabled).lower()};
  const message = document.getElementById('message');
  message.textContent = 'Salvando…';
  try {{
    const response = await fetch('/admin/api/instagram/control', {{
      method: 'POST', headers: {{'Content-Type':'application/json','x-admin-csrf':csrf}},
      body: JSON.stringify({{action: enabled ? 'enable' : 'pause', justification: document.getElementById('justification').value}})
    }});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Não foi possível alterar o estado.');
    location.reload();
  }} catch (error) {{ message.textContent = error.message; }}
}});
</script></body></html>"""
