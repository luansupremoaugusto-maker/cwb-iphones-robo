from __future__ import annotations

from app.admin import (
    catalog_csv_bytes,
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
        "Saúde da bateria;Fotos disponíveis"
    ) in text
    assert "Seminovos;iPhone 15" in text
    assert "Lacrados por encomenda;iPhone 17" in text
