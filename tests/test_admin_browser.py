from __future__ import annotations

import json
from urllib.parse import unquote

from playwright.sync_api import Page, Route, sync_playwright

from app.admin_page import render_admin_page


CATALOG = {
    "seminovos": [
        {
            "nome": "iPhone 15",
            "capacidade": "128 GB",
            "condicao": "Seminovo",
            "quantidade": 1,
            "precos_brl": [1900.0],
            "cores": ["Preto"],
            "saude_bateria": 95,
            "fotos_disponiveis": 2,
            "disponibilidade": "Em estoque",
        }
    ],
    "lacrados_pronta_entrega": [],
    "lacrados": [],
    "total_modelos": 1,
    "generated_at": "2026-09-15T12:00:00+00:00",
    "sources": {
        "mercado_phone_last_refresh": 100.0,
        "google_sheets_last_refresh": 200.0,
    },
}


def _route_page(route: Route, html: str) -> None:
    request = route.request
    path = request.url.split("?", 1)[0].removeprefix("http://admin.test")
    if path == "/admin":
        route.fulfill(content_type="text/html", body=html)
        return
    if path == "/admin/api/catalog":
        route.fulfill(content_type="application/json", body=json.dumps(CATALOG))
        return
    if path == "/admin/api/catalog.pdf":
        route.fulfill(
            content_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=teste.pdf"},
            body=b"%PDF-1.4 test",
        )
        return
    if path == "/admin/api/catalog.csv":
        route.fulfill(
            content_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=teste.csv"},
            body=b"Categoria;Produto\r\n",
        )
        return
    if path == "/admin/api/recovery/draft" and request.method == "POST":
        route.fulfill(
            content_type="application/json",
            body=json.dumps(
                {
                    "phone": "551196543210",
                    "chat_name": "Maria",
                    "last_message_id": 7,
                    "source_message_id": 7,
                    "category_label": "Compra, preço ou estoque",
                    "confidence": "high",
                    "messages": [
                        {
                            "id": 7,
                            "direction": "inbound",
                            "kind": "text",
                            "text": "Tem iPhone 15?",
                            "created_at": "2026-09-15T12:00:00+00:00",
                        }
                    ],
                    "draft": "Olá! Desculpe a demora. Retomando seu atendimento.",
                    "review_required": False,
                    "review_reason": None,
                }
            ),
        )
        return
    if path == "/admin/api/recovery/send" and request.method == "POST":
        route.fulfill(
            content_type="application/json",
            body=json.dumps(
                {
                    "phone": "551196543210",
                    "sent": True,
                    "suppressed": False,
                    "status": "human_active",
                    "message": "Resposta enviada ao cliente.",
                }
            ),
        )
        return
    if path == "/admin/api/recovery/skip" and request.method == "POST":
        route.fulfill(
            content_type="application/json",
            body=json.dumps(
                {
                    "phone": "551196543210",
                    "skipped": True,
                    "message": "Conversa pulada até chegar uma nova mensagem do cliente.",
                }
            ),
        )
        return

    responses = {
        "/admin/api/dashboard": {
            "generated_at": CATALOG["generated_at"],
            "conversations": {},
            "sources": {},
            "control": {},
            "monitoring": {},
            "permissions": {},
            "role": "owner",
        },
        "/admin/api/conversations": {"items": []},
        "/admin/api/recovery": {
            "generated_at": CATALOG["generated_at"],
            "total": 1,
            "has_more": False,
            "items": [
                {
                    "phone": "551196543210",
                    "phone_aliases": ["551196543210", "5511996543210"],
                    "chat_name": "Maria",
                    "category_label": "Compra, preço ou estoque",
                    "last_message": "Tem iPhone 15?",
                    "last_message_id": 7,
                    "age_hours": 72.0,
                }
            ],
        },
        "/admin/api/audit": {"items": []},
        "/admin/api/control": {
            "permissions": {"owner_controls": True},
            "state": {"mode": "active"},
            "role": "owner",
        },
        "/admin/api/sessions": {"items": []},
    }
    route.fulfill(
        content_type="application/json",
        body=json.dumps(responses.get(path, {})),
    )


def _open_admin_page(page: Page, html: str) -> None:
    page.route("**/*", lambda route: _route_page(route, html))
    page.goto("http://admin.test/admin")


def test_admin_page_controls_work_in_a_real_browser():
    html = render_admin_page("csrf-token")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            _open_admin_page(page, html)

            commands_box = page.locator('section[aria-labelledby="commands-title"]').bounding_box()
            operations_box = page.locator('section[aria-labelledby="operations-title"]').bounding_box()
            assert commands_box and operations_box
            assert commands_box["y"] < operations_box["y"]
            assert page.locator("#phone-field").is_hidden()
            assert page.locator("#phone-field").get_attribute("hidden") is not None
            assert page.locator("#command-phone").get_attribute("required") is None

            assert not page.locator("#export-csv").is_disabled()
            assert not page.locator("#export-pdf").is_disabled()
            page.locator("#export-seminovos").uncheck()
            page.locator("#export-lacrados_pronta_entrega").uncheck()
            page.locator("#export-lacrados").uncheck()
            assert page.locator("#export-csv").is_disabled()
            assert page.locator("#export-pdf").is_disabled()
            assert page.locator("#export-status").inner_text() == (
                "Selecione ao menos uma categoria para exportar."
            )

            page.locator("#export-seminovos").check()
            assert not page.locator("#export-csv").is_disabled()
            assert not page.locator("#export-pdf").is_disabled()
            with page.expect_request(
                lambda request: "/admin/api/catalog.pdf" in request.url
            ) as pdf_request:
                page.locator("#export-pdf").click()
            assert unquote(pdf_request.value.url).endswith(
                "/admin/api/catalog.pdf?sections=seminovos"
            )

            csv_page = browser.new_page()
            _open_admin_page(csv_page, html)
            csv_page.locator("#export-lacrados_pronta_entrega").uncheck()
            with csv_page.expect_request(
                lambda request: "/admin/api/catalog.csv" in request.url
            ) as csv_request:
                csv_page.locator("#export-csv").click()
            assert unquote(csv_request.value.url).endswith(
                "/admin/api/catalog.csv?sections=seminovos,lacrados"
            )
        finally:
            browser.close()


def test_recovery_editor_prepares_and_sends_one_reviewed_message_in_a_real_browser():
    html = render_admin_page("csrf-token")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            _open_admin_page(page, html)
            page.on("dialog", lambda dialog: dialog.accept())

            page.locator(
                '#recovery-queue-body button[data-recovery-action="prepare"]'
            ).click()

            page.locator("#recovery-editor").wait_for(state="visible")
            page.wait_for_function(
                "() => document.querySelector('#recovery-message').value.length > 0"
            )
            assert page.locator("#recovery-message").input_value().startswith("Olá!")
            assert "Tem iPhone 15?" in page.locator("#recovery-history").inner_text()

            with page.expect_request(
                lambda request: request.url.endswith("/admin/api/recovery/send")
                and request.method == "POST"
            ) as send_request:
                page.locator("#send-recovery-message").click()

            assert '"expected_last_message_id":7' in (send_request.value.post_data or "").replace(" ", "")
        finally:
            browser.close()


def test_recovery_editor_can_be_closed_in_a_real_browser():
    html = render_admin_page("csrf-token")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            _open_admin_page(page, html)

            page.locator(
                '#recovery-queue-body button[data-recovery-action="prepare"]'
            ).click()
            page.wait_for_function(
                "() => document.querySelector('#recovery-message').value.length > 0"
            )
            assert page.locator("#recovery-editor").is_visible()

            page.locator("#close-recovery-editor").click()

            assert page.locator("#recovery-editor").is_hidden()
            assert not page.locator("#recovery-editor").is_visible()
        finally:
            browser.close()


def test_recovery_editor_is_above_queue_and_each_row_can_skip_in_a_real_browser():
    html = render_admin_page("csrf-token")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            _open_admin_page(page, html)
            page.on("dialog", lambda dialog: dialog.accept())

            assert page.locator("#recovery-editor").evaluate(
                "(editor) => Boolean(editor.compareDocumentPosition(document.querySelector('#recovery-queue-body')) & Node.DOCUMENT_POSITION_FOLLOWING)"
            )
            skip_button = page.locator(
                '#recovery-queue-body button[data-recovery-action="skip"]'
            ).first
            skip_button.wait_for()

            with page.expect_request(
                lambda request: request.url.endswith("/admin/api/recovery/skip")
                and request.method == "POST"
            ) as skip_request:
                skip_button.click()

            assert '"expected_last_message_id":7' in (
                skip_request.value.post_data or ""
            ).replace(" ", "")
        finally:
            browser.close()


def test_recovery_search_accepts_brazilian_mobile_ninth_digit_alias_in_a_real_browser():
    html = render_admin_page("csrf-token")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            _open_admin_page(page, html)

            with page.expect_request(
                lambda request: "/admin/api/recovery?" in request.url
                and "search=5511996543210" in request.url
            ):
                page.locator("#recovery-search").fill("5511996543210")

            assert page.locator('#recovery-queue-body button[data-recovery-action="prepare"]').count() == 1
        finally:
            browser.close()
