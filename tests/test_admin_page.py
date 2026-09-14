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


def test_admin_page_contains_dashboard_queue_and_audit_sections():
    from app.admin_page import render_admin_page

    html = render_admin_page("csrf-token")

    assert "Visão geral do atendimento" in html
    assert 'id="human-queue-body"' in html
    assert "Fila de atendimento humano" in html
    assert "Auditoria recente" in html
    assert 'id="audit-body"' in html
    assert "/admin/api/dashboard" in html
    assert "/admin/api/conversations" in html
    assert "/admin/api/audit" in html


def test_admin_page_contains_safe_command_preview_monitoring_filters_and_controls():
    from app.admin_page import render_admin_page

    html = render_admin_page("csrf-token")

    assert "Pré-visualizar impacto" in html
    assert "justification" in html
    assert "catalog-capacity-filter" in html
    assert "catalog-color-filter" in html
    assert "catalog-stock-filter" in html
    assert "admin-control-form" in html
    assert "/admin/api/commands/preview" in html
    assert "/admin/api/sessions" in html
    assert "Atualizar fonte" in html


def test_admin_login_page_contains_browser_friendly_form():
    from app.admin_page import render_admin_login_page

    html = render_admin_login_page()

    assert "Acesso administrativo" in html
    assert '<form method="post" action="/admin/login"' in html
    assert 'name="username"' in html
    assert 'name="password"' in html
