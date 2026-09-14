from __future__ import annotations

import pytest


def test_admin_page_contains_catalog_controls_and_command_controls():
    from app.admin_page import render_admin_page

    html = render_admin_page("csrf-token")

    assert "Catálogo de disponíveis" in html
    assert "Baixar CSV" in html
    assert "Atualizar catálogo" in html
    assert "Liberar todos os clientes" in html
    assert "X-Admin-CSRF" in html


def test_admin_page_does_not_render_catalog_values_as_inner_html():
    from app.admin_page import render_admin_page

    html = render_admin_page("csrf-token")

    assert "textContent" in html


def test_admin_page_uses_matching_lacrados_stock_counter_id():
    from app.admin_page import render_admin_page

    html = render_admin_page("csrf-token")

    assert 'id="count-lacrados_pronta_entrega"' in html


def test_admin_login_page_contains_browser_friendly_form():
    from app.admin_page import render_admin_login_page

    html = render_admin_login_page()

    assert "Acesso administrativo" in html
    assert '<form method="post" action="/admin/login"' in html
    assert 'name="username"' in html
    assert 'name="password"' in html
