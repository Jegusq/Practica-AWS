"""Tests de la API HTTP contra un PostgreSQL real (fixture ``pg``)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kid.api.main import create_app
from kid.db.loader import upsert_kid
from kid.db.repository import KidRepository
from tests.factories import make_entry, make_payload


@pytest.fixture
def client(pg):
    rows = [
        (
            make_entry("cobas_iberia", "Clase A"),
            {"isin": "ES0119199034", "sri": 6, "gastos_corrientes_pct": 1.85},
        ),
        (
            make_entry("bestinfond", None, fuente="bestinver"),
            {
                "isin": "ES0114673033",
                "nombre_producto": "BESTINFOND, F.I.",
                "gestora": "BESTINVER GESTION SGIIC, S.A.",
                "sri": 5,
                "gastos_corrientes_pct": 1.6,
            },
        ),
    ]
    for entry, overrides in rows:
        with pg.transaction() as tx:
            upsert_kid(tx, entry, make_payload(**overrides))
    presigned: list[tuple[str, str]] = []

    def presign(bucket: str, key: str) -> str:
        presigned.append((bucket, key))
        return f"https://{bucket}.s3.example/{key}?X-Amz-Signature=abc"

    app = create_app(lambda: KidRepository(pg), presign)
    test_client = TestClient(app)
    test_client.presigned = presigned  # type: ignore[attr-defined]
    return test_client


def test_health_does_not_need_the_database():
    def boom():
        raise RuntimeError("sin base de datos")

    c = TestClient(create_app(boom))
    assert c.get("/health").json()["status"] == "ok"
    assert c.get("/health/db").status_code == 503  # error controlado, no un 500 con traza


def test_health_db(client):
    assert client.get("/health/db").json() == {"status": "ok"}


def test_list_kids_with_filters_and_pagination(client):
    body = client.get("/kids").json()
    assert body["total"] == 2 and body["limit"] == 50 and len(body["items"]) == 2
    assert {"isin", "sri", "gastos_corrientes_pct", "fuente", "gestora"} <= body["items"][0].keys()

    assert client.get("/kids", params={"sri_min": 6}).json()["total"] == 1
    assert (
        client.get("/kids", params={"gestora": "bestinver"}).json()["items"][0]["isin"]
        == "ES0114673033"
    )
    assert (
        client.get("/kids", params={"q": "iberia"}).json()["total"] == 0
    )  # busca en nombre/gestora/isin
    page = client.get(
        "/kids", params={"limit": 1, "offset": 1, "sort": "gastos", "desc": True}
    ).json()
    assert page["total"] == 2 and page["items"][0]["gastos_corrientes_pct"] == 1.6


def test_query_validation(client):
    assert client.get("/kids", params={"sri_min": 9}).status_code == 422
    assert client.get("/kids", params={"limit": 0}).status_code == 422
    assert client.get("/kids", params={"sort": "'; drop table kids; --"}).status_code == 422


def test_kid_detail(client):
    kid_id = client.get("/kids", params={"isin": "ES0119199034"}).json()["items"][0]["id"]
    detail = client.get(f"/kids/{kid_id}").json()
    assert detail["isin"] == "ES0119199034" and detail["sri"] == 6
    assert (
        len(detail["escenarios"]) == 2 and detail["distribucion_activos"][0]["porcentaje"] == 90.0
    )
    assert detail["url_pdf"].startswith("https://web.example/") and detail[
        "descargado_en"
    ].startswith("2026-11-22")
    assert client.get("/kids/999999").status_code == 404


def test_pdf_link_is_a_presigned_temporary_url(client):
    kid_id = client.get("/kids", params={"isin": "ES0119199034"}).json()["items"][0]["id"]
    body = client.get(f"/kids/{kid_id}/pdf").json()
    assert (
        body["url"].startswith("https://bucket-test.s3.example/kids/cobas/")
        and body["caduca_en_s"] == 300
    )
    assert client.presigned[0][0] == "bucket-test"
    assert client.get("/kids/999999/pdf").status_code == 404


def test_isin_history_and_unknown_isin(client):
    assert client.get("/isin/es0114673033").json()[0]["nombre_producto"] == "BESTINFOND, F.I."
    assert client.get("/isin/XX0000000000").status_code == 404


def test_aggregates(client):
    stats = client.get("/stats").json()
    assert stats["documentos"] == 2 and stats["kids_vigentes"] == 2 and stats["gestoras"] == 2
    assert {x["fuente"] for x in stats["por_fuente"]} == {"cobas", "bestinver"}
    assert len(client.get("/gestoras").json()) == 2
    docs = client.get("/documentos").json()
    assert len(docs) == 2 and docs[0]["url_pdf"] and docs[0]["descargado_en"]


def test_openapi_is_published(client):
    spec = client.get("/openapi.json").json()
    assert {"/kids", "/kids/{kid_id}", "/stats", "/gestoras", "/documentos"} <= set(spec["paths"])
