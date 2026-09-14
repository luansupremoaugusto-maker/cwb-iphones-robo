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
