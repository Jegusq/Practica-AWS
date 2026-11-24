"""El cliente de la RDS Data API se prueba con un boto3 falso (no hay que tocar AWS)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from botocore.exceptions import ClientError

from kid.db.client import DataApiClient, parse_json_column, to_data_api_param
from kid.db.local import to_psycopg

ARN = "arn:aws:rds:eu-west-1:123456789012:cluster:kid"
SECRET = "arn:aws:secretsmanager:eu-west-1:123456789012:secret:kid-abc"


def client_error(code: str, message: str = "") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "ExecuteStatement")


class FakeRdsData:
    def __init__(self, responses=None):
        self.calls: list[tuple[str, dict]] = []
        self.responses = list(responses or [])

    def _next(self, name, kwargs):
        self.calls.append((name, kwargs))
        item = self.responses.pop(0) if self.responses else {}
        if isinstance(item, Exception):
            raise item
        return item

    def execute_statement(self, **kw):
        return self._next("execute_statement", kw)

    def batch_execute_statement(self, **kw):
        return self._next("batch_execute_statement", kw)

    def begin_transaction(self, **kw):
        return self._next("begin_transaction", kw) or {"transactionId": "tx-1"}

    def commit_transaction(self, **kw):
        return self._next("commit_transaction", kw)

    def rollback_transaction(self, **kw):
        return self._next("rollback_transaction", kw)


def make(responses=None, **kw):
    fake = FakeRdsData(responses)
    sleeps: list[float] = []
    client = DataApiClient(ARN, SECRET, "kid", client=fake, sleep=sleeps.append, **kw)
    return client, fake, sleeps


class TestParams:
    def test_python_types_to_data_api_fields(self):
        assert to_data_api_param("a", None) == {"name": "a", "value": {"isNull": True}}
        assert to_data_api_param("a", True)["value"] == {"booleanValue": True}
        assert to_data_api_param("a", 7)["value"] == {"longValue": 7}
        assert to_data_api_param("a", 1.5)["value"] == {"doubleValue": 1.5}
        assert to_data_api_param("a", "hola")["value"] == {"stringValue": "hola"}
        d = to_data_api_param("a", date(2026, 1, 9))
        assert d["value"] == {"stringValue": "2026-01-09"} and d["typeHint"] == "DATE"
        ts = to_data_api_param("a", datetime(2026, 11, 22, 10, 5, 3, 120000, tzinfo=UTC))
        assert (
            ts["value"] == {"stringValue": "2026-11-22 10:05:03.120"}
            and ts["typeHint"] == "TIMESTAMP"
        )
        assert json.loads(to_data_api_param("a", {"k": "ñ"})["value"]["stringValue"]) == {"k": "ñ"}

    def test_bool_is_not_treated_as_int(self):
        assert "booleanValue" in to_data_api_param("a", False)["value"]

    def test_named_params_to_psycopg_style(self):
        sql = "SELECT * FROM t WHERE a = :a AND b ILIKE :b AND c = CAST(:c AS jsonb) AND t > '12:30' AND x::text = 'y' AND z LIKE '%'"
        assert to_psycopg(sql) == (
            "SELECT * FROM t WHERE a = %(a)s AND b ILIKE %(b)s AND c = CAST(%(c)s AS jsonb) "
            "AND t > '12:30' AND x::text = 'y' AND z LIKE '%%'"
        )

    def test_parse_json_column_handles_both_drivers(self):
        assert parse_json_column('["a"]') == ["a"]
        assert parse_json_column(["a"]) == ["a"]
        assert parse_json_column(None, []) == [] and parse_json_column("no json", []) == []


class TestClient:
    def test_execute_sends_arn_secret_database_and_parses_json_records(self):
        client, fake, _ = make([{"formattedRecords": '[{"id": 1, "nombre": "x"}]'}])
        rows = client.execute("SELECT * FROM kids WHERE id = :id", {"id": 1})
        assert rows == [{"id": 1, "nombre": "x"}]
        name, kw = fake.calls[0]
        assert name == "execute_statement"
        assert kw["resourceArn"] == ARN and kw["secretArn"] == SECRET and kw["database"] == "kid"
        assert kw["formatRecordsAs"] == "JSON"
        assert kw["parameters"] == [{"name": "id", "value": {"longValue": 1}}]

    def test_statement_without_result_set(self):
        client, _, _ = make([{}])
        assert client.execute("CREATE TABLE x (a int)") == []

    def test_waits_while_aurora_resumes_from_auto_pause(self):
        client, fake, sleeps = make(
            [
                client_error("DatabaseResumingException"),
                client_error("DatabaseResumingException"),
                {"formattedRecords": "[]"},
            ]
        )
        assert client.execute("SELECT 1") == []
        assert len(fake.calls) == 3 and len(sleeps) == 2

    def test_gives_up_after_resume_timeout(self):
        client, _, _ = make([client_error("DatabaseResumingException")] * 50, resume_timeout=0.0)
        with pytest.raises(ClientError):
            client.execute("SELECT 1")

    def test_other_errors_are_not_retried(self):
        client, fake, sleeps = make([client_error("BadRequestException", "syntax error")])
        with pytest.raises(ClientError):
            client.execute("SELEC 1")
        assert len(fake.calls) == 1 and sleeps == []

    def test_batch_is_chunked(self):
        client, fake, _ = make()
        client.execute_many("INSERT INTO t VALUES (:a)", [{"a": i} for i in range(1200)])
        assert [len(c[1]["parameterSets"]) for c in fake.calls] == [500, 500, 200]

    def test_empty_batch_makes_no_call(self):
        client, fake, _ = make()
        client.execute_many("INSERT INTO t VALUES (:a)", [])
        assert fake.calls == []

    def test_transaction_commits_and_passes_transaction_id(self):
        client, fake, _ = make()
        with client.transaction() as tx:
            tx.execute("INSERT INTO t VALUES (1)")
            tx.execute_many("INSERT INTO t VALUES (:a)", [{"a": 1}])
        assert [c[0] for c in fake.calls] == [
            "begin_transaction",
            "execute_statement",
            "batch_execute_statement",
            "commit_transaction",
        ]
        assert (
            fake.calls[1][1]["transactionId"] == "tx-1"
            and fake.calls[3][1]["transactionId"] == "tx-1"
        )

    def test_transaction_rolls_back_on_error(self):
        client, fake, _ = make()
        with pytest.raises(ValueError), client.transaction():
            raise ValueError("boom")
        assert [c[0] for c in fake.calls] == ["begin_transaction", "rollback_transaction"]
