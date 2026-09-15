from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfReader

from app.admin import (
    CATALOG_EXPORT_COLUMNS,
    catalog_csv_bytes,
    catalog_pdf_bytes,
    catalog_pdf_filename,
    public_catalog_payload,
)
from app.config import Settings


SAMPLE_CATALOG = {
    "seminovos": [
        {
            "nome": "iPhone 15",
            "external_id": "secret-used-id",
            "capacidade": "128 GB",
            "condicao": "Seminovo",
            "quantidade": 1,
            "precos_brl": [1900.0],
            "cores": ["Preto"],
            "cor": "Preto",
            "saude_bateria": 95,
            "fotos_disponiveis": 2,
            "imei": "123456789012345",
        }
    ],
    "lacrados_pronta_entrega": [],
    "lacrados": [
        {
            "nome": "iPhone 17",
            "capacidade": "256 GB",
            "condicao": "Lacrado por encomenda",
            "quantidade": None,
            "precos_brl": [4999.0],
            "cores": ["Azul"],
            "cor": "Azul",
            "saude_bateria": None,
            "fotos_disponiveis": 0,
        }
    ],
}


def _pdf_text(pdf_data: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf_data)).pages)


def test_admin_panel_requires_all_three_credentials():
    assert (
        Settings(
            admin_username="",
            admin_password="secret",
            admin_csrf_secret="csrf",
        ).admin_panel_configured
        is False
    )
    assert (
        Settings(
            admin_username="admin",
            admin_password="secret",
            admin_csrf_secret="csrf",
        ).admin_panel_configured
        is True
    )


def test_public_catalog_filters_private_fields_and_keeps_sections():
    payload = public_catalog_payload(
        SAMPLE_CATALOG,
        mercado_refresh=100.0,
        sheets_refresh=200.0,
        generated_at="2026-09-14T12:00:00+00:00",
    )

    assert payload["seminovos"][0]["nome"] == "iPhone 15"
    assert payload["lacrados"][0]["nome"] == "iPhone 17"
    assert "external_id" not in payload["seminovos"][0]
    assert "imei" not in payload["seminovos"][0]
    assert payload["seminovos"][0]["disponibilidade"] == "Em estoque"
    assert payload["lacrados"][0]["disponibilidade"] == "Por encomenda"
    assert payload["total_modelos"] == 2
    assert payload["sources"] == {
        "mercado_phone_last_refresh": 100.0,
        "google_sheets_last_refresh": 200.0,
    }


def test_catalog_csv_has_excel_columns_and_all_sections():
    csv_data = catalog_csv_bytes(
        public_catalog_payload(SAMPLE_CATALOG, 100.0, 200.0, "now")
    )
    assert csv_data.startswith(b"\xef\xbb\xbf")
    text = csv_data.decode("utf-8-sig")
    assert (
        "Categoria;Produto;Capacidade;Condição;Cor(es);Preço(s);Quantidade;"
        "Saúde da bateria;Fotos disponíveis;Disponibilidade"
    ) in text
    assert "Seminovos;iPhone 15" in text
    assert "Lacrados por encomenda;iPhone 17" in text


def test_catalog_csv_can_export_only_selected_sections():
    payload = public_catalog_payload(SAMPLE_CATALOG, 100.0, 200.0, "now")

    csv_data = catalog_csv_bytes(payload, sections=["seminovos"])

    text = csv_data.decode("utf-8-sig")
    assert "Seminovos;iPhone 15" in text
    assert "Lacrados por encomenda;iPhone 17" not in text


def test_catalog_csv_can_export_multiple_selected_sections_in_catalog_order():
    payload = public_catalog_payload(SAMPLE_CATALOG, 100.0, 200.0, "now")

    csv_data = catalog_csv_bytes(payload, sections=["lacrados", "seminovos"])

    text = csv_data.decode("utf-8-sig")
    assert "Seminovos;iPhone 15" in text
    assert "Lacrados por encomenda;iPhone 17" in text
    assert "Lacrados para pronta entrega" not in text


def test_catalog_csv_rejects_unknown_sections():
    payload = public_catalog_payload(SAMPLE_CATALOG, 100.0, 200.0, "now")

    with pytest.raises(ValueError, match=r"Categoria\(s\) de catálogo inválida\(s\)"):
        catalog_csv_bytes(payload, sections=["seminovos", "desconhecida"])


def test_catalog_pdf_contains_only_requested_category_rows():
    import app.admin as admin

    payload = public_catalog_payload(SAMPLE_CATALOG, 100.0, 200.0, "now")
    pdf_builder = getattr(admin, "catalog_pdf_bytes", None)

    assert callable(pdf_builder), "o exportador PDF do catálogo ainda não existe"
    pdf_data = pdf_builder(payload, sections=["seminovos"])
    text = _pdf_text(pdf_data)

    assert pdf_data.startswith(b"%PDF-")
    assert "iPhone 15" in text
    assert "iPhone 17" not in text
    assert "Fotos disponíveis" in text


def test_catalog_pdf_preserves_zero_values_from_catalog():
    import app.admin as admin

    payload = public_catalog_payload(SAMPLE_CATALOG, 100.0, 200.0, "now")
    pdf_data = admin.catalog_pdf_bytes(payload, sections=["lacrados"])
    text = _pdf_text(pdf_data)

    assert "Fotos disponíveis" in text
    assert "0" in text


def test_catalog_csv_and_pdf_share_the_same_export_columns():
    payload = public_catalog_payload(SAMPLE_CATALOG, 100.0, 200.0, "now")
    csv_header = catalog_csv_bytes(payload).decode("utf-8-sig").splitlines()[0].split(";")

    assert tuple(csv_header) == CATALOG_EXPORT_COLUMNS
    assert "Fotos disponíveis" in _pdf_text(catalog_pdf_bytes(payload))


def test_catalog_pdf_preserves_unicode_catalog_text():
    item = {
        **SAMPLE_CATALOG["seminovos"][0],
        "nome": "iPhone 15 日本語",
        "cores": ["Azul 日本語"],
    }
    payload = public_catalog_payload(
        {"seminovos": [item], "lacrados_pronta_entrega": [], "lacrados": []},
        100.0,
        200.0,
        "now",
    )

    text = _pdf_text(catalog_pdf_bytes(payload, sections=["seminovos"]))

    assert "iPhone 15 日本語" in text
    assert "Azul 日本語" in text
    assert "?" not in text


def test_catalog_pdf_preserves_broader_unicode_catalog_text():
    import app.admin as admin

    if any(admin._CATALOG_PDF_FONTS[key] is None for key in ("arabic", "devanagari", "symbols")):
        pytest.skip("Noto script fonts are supplied by the production container")

    item = {
        **SAMPLE_CATALOG["seminovos"][0],
        "nome": "iPhone 日本語 مرحبا नमस्ते Cafe\u0301 😀",
        "cores": ["Azul 日本語 مرحبا नमस्ते 😀"],
    }
    payload = public_catalog_payload(
        {"seminovos": [item], "lacrados_pronta_entrega": [], "lacrados": []},
        100.0,
        200.0,
        "now",
    )

    text = _pdf_text(catalog_pdf_bytes(payload, sections=["seminovos"]))

    for character in "日本語مرحبا नमस्ते" + "😀":
        assert character in text
    assert "?" not in text


def test_catalog_pdf_does_not_silently_use_an_incompatible_script_fallback(monkeypatch):
    import app.admin as admin

    monkeypatch.setitem(admin._CATALOG_PDF_FONTS, "arabic", None)

    with pytest.raises(admin.CatalogPdfFontUnavailable):
        admin._catalog_pdf_markup("مرحبا")


@pytest.mark.parametrize(
    ("sections", "expected_filename"),
    [
        (None, "catalogo-disponiveis.pdf"),
        (["seminovos"], "catalogo-seminovos.pdf"),
        (["lacrados_pronta_entrega"], "catalogo-lacrados-pronta-entrega.pdf"),
        (["lacrados"], "catalogo-lacrados-por-encomenda.pdf"),
        (["lacrados", "seminovos"], "catalogo-selecionado.pdf"),
    ],
)
def test_catalog_pdf_uses_the_same_category_filename_rules_as_csv(sections, expected_filename):
    assert catalog_pdf_filename(sections) == expected_filename


def test_catalog_pdf_is_landscape_and_repeats_the_exact_csv_header_on_multiple_pages():
    import app.admin as admin

    many_items = [
        {**SAMPLE_CATALOG["seminovos"][0], "nome": f"iPhone usado {index}"}
        for index in range(100)
    ]
    payload = public_catalog_payload(
        {"seminovos": many_items, "lacrados_pronta_entrega": [], "lacrados": []},
        100.0,
        200.0,
        "now",
    )

    pdf_data = admin.catalog_pdf_bytes(payload, sections=["seminovos"])
    reader = PdfReader(BytesIO(pdf_data))

    assert len(reader.pages) >= 2
    assert reader.pages[0].mediabox.width > reader.pages[0].mediabox.height
    for page in reader.pages:
        text = page.extract_text() or ""
        assert "Categoria" in text
        assert "Fotos disponíveis" in text
