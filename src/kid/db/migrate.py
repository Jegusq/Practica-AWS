"""Aplica el esquema (db/schema.sql) a la base de datos. Idempotente."""

from __future__ import annotations

import re
from importlib import resources

from .client import DbClient


def load_schema() -> str:
    return resources.files("kid.db").joinpath("schema.sql").read_text(encoding="utf-8")


def split_statements(sql: str) -> list[str]:
    without_comments = "\n".join(line for line in sql.splitlines() if not re.match(r"^\s*--", line))
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def migrate(db: DbClient) -> int:
    statements = split_statements(load_schema())
    for statement in statements:
        db.execute(statement)
    return len(statements)
