"""Carga en PostgreSQL de lo extraido, sin duplicados aunque un documento se procese dos veces.

Idempotencia: ``documentos.sha256`` es UNIQUE y ``kids.documento_id`` es UNIQUE; ambas inserciones son
``INSERT ... ON CONFLICT DO UPDATE`` y los hijos (escenarios, distribucion) se reemplazan dentro de la misma
transaccion. Repetir la carga deja la base exactamente igual.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from ..extraction.runner import extraction_key
from ..manifest import ManifestEntry, read_manifests
from ..storage import Store
from .client import DbClient, Executor

log = logging.getLogger("kid.db.loader")

_SUFIJOS_LEGALES = {"sgiic", "sa", "sl", "sau", "ag", "gmbh", "ltd", "sicav"}


def normalizar_gestora(nombre: str | None) -> str | None:
    """Clave estable para agrupar variantes del mismo nombre ('BESTINVER GESTION SGIIC, S.A.' ...)."""
    if not nombre:
        return None
    text = unicodedata.normalize("NFKD", nombre)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace(".", "")  # S.G.I.I.C. -> sgiic ; S.A. -> sa
    tokens = [t for t in re.split(r"[^a-z0-9]+", text) if t and t not in _SUFIJOS_LEGALES]
    return " ".join(tokens) or None


def _parse_ts(value: str | None) -> datetime:
    # Siempre UTC y sin zona horaria: la Data API recibe TIMESTAMP, y la columna es TIMESTAMPTZ (sesion UTC).
    if value:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")  # noqa: DTZ007
    return datetime.now(UTC).replace(tzinfo=None)


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


UPSERT_DOCUMENTO = """
INSERT INTO documentos (sha256, fuente, fondo_slug, fondo_nombre, clase_etiqueta, url_pagina, url_pdf,
                        s3_bucket, s3_key, tamano_bytes, descargado_en)
VALUES (:sha256, :fuente, :fondo_slug, :fondo_nombre, :clase_etiqueta, :url_pagina, :url_pdf,
        :s3_bucket, :s3_key, :tamano_bytes, :descargado_en)
ON CONFLICT (sha256) DO UPDATE SET
    fuente = EXCLUDED.fuente, fondo_slug = EXCLUDED.fondo_slug, fondo_nombre = EXCLUDED.fondo_nombre,
    clase_etiqueta = EXCLUDED.clase_etiqueta, url_pagina = EXCLUDED.url_pagina, url_pdf = EXCLUDED.url_pdf,
    s3_bucket = EXCLUDED.s3_bucket, s3_key = EXCLUDED.s3_key, tamano_bytes = EXCLUDED.tamano_bytes,
    descargado_en = EXCLUDED.descargado_en, actualizado_en = now()
RETURNING id
"""

UPSERT_KID = """
INSERT INTO kids (documento_id, isin, nombre_producto, nombre_clase, gestora, gestora_clave, fecha_documento,
                  sri, periodo_mantenimiento_anios, gastos_corrientes_pct, costes_entrada_pct,
                  costes_salida_pct, comision_exito_pct, divisa, objetivo_inversion, modelo_id, extraido_en,
                  avisos, extraccion)
VALUES (:documento_id, :isin, :nombre_producto, :nombre_clase, :gestora, :gestora_clave, :fecha_documento,
        :sri, :periodo_mantenimiento_anios, :gastos_corrientes_pct, :costes_entrada_pct,
        :costes_salida_pct, :comision_exito_pct, :divisa, :objetivo_inversion, :modelo_id, :extraido_en,
        CAST(:avisos AS jsonb), CAST(:extraccion AS jsonb))
ON CONFLICT (documento_id) DO UPDATE SET
    isin = EXCLUDED.isin, nombre_producto = EXCLUDED.nombre_producto, nombre_clase = EXCLUDED.nombre_clase,
    gestora = EXCLUDED.gestora, gestora_clave = EXCLUDED.gestora_clave,
    fecha_documento = EXCLUDED.fecha_documento, sri = EXCLUDED.sri,
    periodo_mantenimiento_anios = EXCLUDED.periodo_mantenimiento_anios,
    gastos_corrientes_pct = EXCLUDED.gastos_corrientes_pct, costes_entrada_pct = EXCLUDED.costes_entrada_pct,
    costes_salida_pct = EXCLUDED.costes_salida_pct, comision_exito_pct = EXCLUDED.comision_exito_pct,
    divisa = EXCLUDED.divisa, objetivo_inversion = EXCLUDED.objetivo_inversion, modelo_id = EXCLUDED.modelo_id,
    extraido_en = EXCLUDED.extraido_en, avisos = EXCLUDED.avisos, extraccion = EXCLUDED.extraccion,
    actualizado_en = now()
RETURNING id
"""

INSERT_ESCENARIO = """
INSERT INTO escenarios (kid_id, escenario, periodo, valor_final_eur, rentabilidad_anual_pct)
VALUES (:kid_id, :escenario, :periodo, :valor_final_eur, :rentabilidad_anual_pct)
"""

INSERT_DISTRIBUCION = """
INSERT INTO distribucion_activos (kid_id, categoria, porcentaje)
VALUES (:kid_id, :categoria, :porcentaje)
"""


@dataclass
class LoadReport:
    cargados: int = 0
    sin_extraccion: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)


def upsert_kid(tx: Executor, entry: ManifestEntry, payload: dict[str, Any]) -> int:
    """Inserta o actualiza un documento y sus datos extraidos. Devuelve el id de la fila de ``kids``."""
    datos = payload["datos"]
    documento_id = tx.execute(
        UPSERT_DOCUMENTO,
        {
            "sha256": entry.sha256,
            "fuente": entry.fuente,
            "fondo_slug": entry.fondo_slug,
            "fondo_nombre": entry.fondo_nombre,
            "clase_etiqueta": entry.clase_etiqueta,
            "url_pagina": entry.url_pagina,
            "url_pdf": entry.url_pdf,
            "s3_bucket": entry.s3_bucket,
            "s3_key": entry.s3_key,
            "tamano_bytes": entry.tamano_bytes,
            "descargado_en": _parse_ts(entry.descargado_en),
        },
    )[0]["id"]

    kid_id = tx.execute(
        UPSERT_KID,
        {
            "documento_id": documento_id,
            "isin": datos.get("isin"),
            "nombre_producto": datos.get("nombre_producto"),
            "nombre_clase": datos.get("nombre_clase"),
            "gestora": datos.get("gestora"),
            "gestora_clave": normalizar_gestora(datos.get("gestora")),
            "fecha_documento": _parse_date(datos.get("fecha_documento")),
            "sri": datos.get("sri"),
            "periodo_mantenimiento_anios": datos.get("periodo_mantenimiento_anios"),
            "gastos_corrientes_pct": datos.get("gastos_corrientes_pct"),
            "costes_entrada_pct": datos.get("costes_entrada_pct"),
            "costes_salida_pct": datos.get("costes_salida_pct"),
            "comision_exito_pct": datos.get("comision_exito_pct"),
            "divisa": datos.get("divisa"),
            "objetivo_inversion": datos.get("objetivo_inversion"),
            "modelo_id": payload["modelo_id"],
            "extraido_en": _parse_ts(payload.get("extraido_en")),
            "avisos": payload.get("avisos", []),
            "extraccion": json.dumps(payload, ensure_ascii=False),
        },
    )[0]["id"]

    # Hijos: se reemplazan por completo para que repetir la carga no duplique filas.
    tx.execute("DELETE FROM escenarios WHERE kid_id = :kid_id", {"kid_id": kid_id})
    tx.execute("DELETE FROM distribucion_activos WHERE kid_id = :kid_id", {"kid_id": kid_id})
    tx.execute_many(
        INSERT_ESCENARIO,
        [
            {
                "kid_id": kid_id,
                "escenario": e["escenario"],
                "periodo": e["periodo"],
                "valor_final_eur": e.get("valor_final_eur"),
                "rentabilidad_anual_pct": e.get("rentabilidad_anual_pct"),
            }
            for e in datos.get("escenarios", [])
        ],
    )
    tx.execute_many(
        INSERT_DISTRIBUCION,
        [
            {"kid_id": kid_id, "categoria": d["categoria"], "porcentaje": d["porcentaje"]}
            for d in datos.get("distribucion_activos", [])
        ],
    )
    return kid_id


def load_all(db: DbClient, store: Store, model_id: str) -> LoadReport:
    report = LoadReport()
    for entry in read_manifests(store):
        key = extraction_key(model_id, entry.sha256 or "")
        if not store.exists(key):
            report.sin_extraccion.append(entry.s3_key or entry.url_pdf)
            continue
        payload = json.loads(store.get(key))
        try:
            with db.transaction() as tx:
                upsert_kid(tx, entry, payload)
            report.cargados += 1
        except Exception as exc:  # un registro malo no debe tumbar la carga completa
            report.errores.append(f"{entry.s3_key}: {type(exc).__name__}: {exc}")
            log.exception("error cargando %s", entry.s3_key)
    return report
