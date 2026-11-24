"""Acceso a la base de datos."""

from __future__ import annotations

from ..config import Settings
from .client import DataApiClient, DbClient


def connect(settings: Settings | None = None) -> DbClient:
    """Cliente segun el entorno: Data API en AWS; PostgreSQL directo si hay KID_DB_DSN (solo local)."""
    settings = settings or Settings.from_env()
    if settings.db_dsn:
        from .local import PsycopgClient

        return PsycopgClient(settings.db_dsn)
    if settings.db_cluster_arn and settings.db_secret_arn:
        return DataApiClient(
            settings.db_cluster_arn,
            settings.db_secret_arn,
            settings.db_name,
            region=settings.region,
        )
    raise RuntimeError(
        "Base de datos sin configurar: define DB_CLUSTER_ARN y DB_SECRET_ARN (AWS) o KID_DB_DSN (local)."
    )
