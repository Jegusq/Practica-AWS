from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_bytes():
    def read(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    return read


@pytest.fixture
def pg():
    """Cliente sobre un PostgreSQL real, con el esquema recien creado.

    Necesita KID_TEST_DSN (en CI es un contenedor postgres; en local vale PGlite: ver README).
    Si no esta definida, los tests que lo usan se omiten.
    """
    dsn = os.environ.get("KID_TEST_DSN")
    if not dsn:
        pytest.skip("KID_TEST_DSN no definida: se omiten los tests contra PostgreSQL")
    from kid.db.local import PsycopgClient
    from kid.db.migrate import migrate

    client = PsycopgClient(dsn)
    for stmt in (
        "DROP VIEW IF EXISTS kids_vigentes",
        "DROP TABLE IF EXISTS distribucion_activos, escenarios, kids, documentos CASCADE",
    ):
        client.execute(stmt)
    migrate(client)
    yield client
    client.close()
