"""Configuracion leida de variables de entorno (sin credenciales en el codigo)."""

from __future__ import annotations

import os
from dataclasses import dataclass

# Perfil de inferencia geografico UE de Claude Haiku 5.5: el modelo mas barato de la familia 5.5,
# suficiente para extraer campos de un KID. Se puede cambiar con BEDROCK_MODEL_ID o --model.
DEFAULT_MODEL_ID = "eu.anthropic.claude-haiku-5-5"


@dataclass(frozen=True)
class Settings:
    region: str
    bucket: str | None
    model_id: str
    db_cluster_arn: str | None
    db_secret_arn: str | None
    db_name: str
    db_dsn: str | None  # solo desarrollo local/tests: PostgreSQL directo en vez de Data API
    contact: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            region=os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "eu-west-3",
            bucket=os.environ.get("KID_BUCKET") or None,
            model_id=os.environ.get("BEDROCK_MODEL_ID") or DEFAULT_MODEL_ID,
            db_cluster_arn=os.environ.get("DB_CLUSTER_ARN") or None,
            db_secret_arn=os.environ.get("DB_SECRET_ARN") or None,
            db_name=os.environ.get("DB_NAME") or "kid",
            db_dsn=os.environ.get("KID_DB_DSN") or None,
            contact=os.environ.get("KID_CONTACT") or "sin-contacto-configurado",
        )


def model_slug(model_id: str) -> str:
    """Nombre de carpeta seguro para un id de modelo (p. ej. 'eu.anthropic.claude-haiku-5-5')."""
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in model_id).strip("-.")
