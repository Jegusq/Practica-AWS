"""Cliente PostgreSQL directo (psycopg) con la misma interfaz que ``DataApiClient``.

Solo para desarrollo local y tests: permite ejecutar contra un Postgres cualquiera EXACTAMENTE el mismo SQL
que en AWS. Convierte los parametros ``:nombre`` al estilo de psycopg y normaliza los tipos de salida a lo
que devuelve la Data API (numeros y fechas como tipos JSON).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .client import Executor, Params, Row, normalize_param

# ":nombre" pero no "::tipo" ni "12:30".
_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


def to_psycopg(sql: str) -> str:
    return _PARAM.sub(r"%(\1)s", sql.replace("%", "%%"))


def _normalize_row(row: dict[str, Any]) -> Row:
    out: Row = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            value = float(value)
        elif isinstance(value, datetime):
            value = value.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
        elif isinstance(value, date):
            value = value.isoformat()
        out[key] = value
    return out


class PsycopgClient:
    def __init__(self, dsn: str):
        import psycopg
        from psycopg.rows import dict_row

        # prepare_threshold=None: sin sentencias preparadas en el servidor (compatible con PGlite/pgbouncer).
        self._conn = psycopg.connect(
            dsn, autocommit=True, row_factory=dict_row, prepare_threshold=None
        )

    def close(self) -> None:
        self._conn.close()

    def _run(self, conn, sql: str, params: Params | None) -> list[Row]:
        values = {k: normalize_param(v) for k, v in (params or {}).items()}
        with conn.cursor() as cur:
            cur.execute(to_psycopg(sql) if params else sql, values or None)
            return [_normalize_row(r) for r in cur.fetchall()] if cur.description else []

    def execute(self, sql: str, params: Params | None = None) -> list[Row]:
        return self._run(self._conn, sql, params)

    def execute_many(self, sql: str, param_sets: Sequence[Params]) -> None:
        for params in param_sets:
            self._run(self._conn, sql, params)

    @contextmanager
    def transaction(self) -> Iterator[Executor]:
        with self._conn.transaction():
            yield _PsycopgTransaction(self)


class _PsycopgTransaction:
    def __init__(self, client: PsycopgClient):
        self._client = client

    def execute(self, sql: str, params: Params | None = None) -> list[Row]:
        return self._client.execute(sql, params)

    def execute_many(self, sql: str, param_sets: Sequence[Params]) -> None:
        self._client.execute_many(sql, param_sets)
