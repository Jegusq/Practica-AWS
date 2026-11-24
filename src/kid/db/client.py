"""Acceso a PostgreSQL.

En AWS se usa la **RDS Data API** (HTTPS + IAM, sin VPC ni drivers ni pool de conexiones): ideal para una
Lambda que consulta un Aurora Serverless v2 que puede estar en pausa. Para desarrollo y tests hay un cliente
equivalente sobre psycopg (``db/local.py``). Ambos cumplen el mismo protocolo, asi que el SQL es el mismo.

Convenciones del SQL compartido: parametros con nombre ``:nombre``; JSON se pasa como cadena y se convierte
en la propia consulta con ``CAST(:x AS jsonb)``; sin ``::`` dentro de la consulta (se usa ``CAST``).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol

log = logging.getLogger("kid.db")

Row = dict[str, Any]
Params = Mapping[str, Any]


class Executor(Protocol):
    def execute(self, sql: str, params: Params | None = None) -> list[Row]: ...

    def execute_many(self, sql: str, param_sets: Sequence[Params]) -> None: ...


class DbClient(Executor, Protocol):
    def transaction(self) -> AbstractContextManager[Executor]: ...


def normalize_param(value: Any) -> Any:
    """Valores Python -> valores que entienden por igual la Data API y psycopg."""
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value
    return value


def parse_json_column(value: Any, default: Any = None) -> Any:
    """La Data API devuelve jsonb como texto; psycopg lo devuelve ya parseado."""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value


# --------------------------------------------------------------------------------------------------
# RDS Data API
# --------------------------------------------------------------------------------------------------

RETRYABLE_CODES = {
    "DatabaseResumingException",  # Aurora saliendo de la auto-pausa (tarda ~15 s)
    "DatabaseUnavailableException",
    "ServiceUnavailableError",
    "ThrottlingException",
}
BATCH_LIMIT = 500


def to_data_api_param(name: str, value: Any) -> dict[str, Any]:
    value = normalize_param(value)
    param: dict[str, Any] = {"name": name}
    if value is None:
        param["value"] = {"isNull": True}
    elif isinstance(value, bool):
        param["value"] = {"booleanValue": value}
    elif isinstance(value, int):
        param["value"] = {"longValue": value}
    elif isinstance(value, float):
        param["value"] = {"doubleValue": value}
    elif isinstance(value, datetime):
        param["value"] = {"stringValue": value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]}
        param["typeHint"] = "TIMESTAMP"
    elif isinstance(value, date):
        param["value"] = {"stringValue": value.isoformat()}
        param["typeHint"] = "DATE"
    else:
        param["value"] = {"stringValue": str(value)}
    return param


class DataApiClient:
    def __init__(
        self,
        cluster_arn: str,
        secret_arn: str,
        database: str,
        *,
        region: str | None = None,
        client: Any = None,
        resume_timeout: float = 90.0,
        sleep=time.sleep,
    ):
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "rds-data",
                region_name=region,
                config=Config(read_timeout=60, retries={"max_attempts": 3}),
            )
        self._rds = client
        self._base = {"resourceArn": cluster_arn, "secretArn": secret_arn}
        self._database = database
        self._resume_timeout = resume_timeout
        self._sleep = sleep

    # -- llamada con reintento mientras Aurora se reanuda ---------------------------------------------
    def _call(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        from botocore.exceptions import ClientError

        deadline = time.monotonic() + self._resume_timeout
        delay = 2.0
        while True:
            try:
                return getattr(self._rds, operation)(**self._base, **kwargs)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                message = exc.response.get("Error", {}).get("Message", "")
                retryable = code in RETRYABLE_CODES or (
                    code == "BadRequestException" and "Communications link failure" in message
                )
                if not retryable or time.monotonic() + delay > deadline:
                    raise
                log.info("Aurora no disponible (%s): reintento en %.0fs", code, delay)
                self._sleep(delay)
                delay = min(delay * 1.5, 10.0)

    def _statement(self, sql: str, params: Params | None, transaction_id: str | None) -> list[Row]:
        kwargs: dict[str, Any] = {"database": self._database, "sql": sql, "formatRecordsAs": "JSON"}
        if params:
            kwargs["parameters"] = [to_data_api_param(k, v) for k, v in params.items()]
        if transaction_id:
            kwargs["transactionId"] = transaction_id
        response = self._call("execute_statement", **kwargs)
        formatted = response.get("formattedRecords")
        return json.loads(formatted) if formatted else []

    def _batch(self, sql: str, param_sets: Sequence[Params], transaction_id: str | None) -> None:
        for start in range(0, len(param_sets), BATCH_LIMIT):
            chunk = param_sets[start : start + BATCH_LIMIT]
            kwargs: dict[str, Any] = {
                "database": self._database,
                "sql": sql,
                "parameterSets": [[to_data_api_param(k, v) for k, v in ps.items()] for ps in chunk],
            }
            if transaction_id:
                kwargs["transactionId"] = transaction_id
            self._call("batch_execute_statement", **kwargs)

    # -- API publica ---------------------------------------------------------------------------------
    def execute(self, sql: str, params: Params | None = None) -> list[Row]:
        return self._statement(sql, params, None)

    def execute_many(self, sql: str, param_sets: Sequence[Params]) -> None:
        if param_sets:
            self._batch(sql, param_sets, None)

    @contextmanager
    def transaction(self) -> Iterator[Executor]:
        tx_id = self._call("begin_transaction", database=self._database)["transactionId"]
        tx = _DataApiTransaction(self, tx_id)
        try:
            yield tx
        except BaseException:
            try:
                self._call("rollback_transaction", transactionId=tx_id)
            except Exception:  # noqa: BLE001 - el error original es el importante
                log.exception("rollback fallido")
            raise
        else:
            self._call("commit_transaction", transactionId=tx_id)


class _DataApiTransaction:
    def __init__(self, client: DataApiClient, transaction_id: str):
        self._client = client
        self._tx = transaction_id

    def execute(self, sql: str, params: Params | None = None) -> list[Row]:
        return self._client._statement(sql, params, self._tx)

    def execute_many(self, sql: str, param_sets: Sequence[Params]) -> None:
        if param_sets:
            self._client._batch(sql, param_sets, self._tx)
