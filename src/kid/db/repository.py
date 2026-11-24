"""Consultas de lectura que usa la API. Solo SELECT: la API no necesita (ni debe) escribir."""

from __future__ import annotations

from typing import Any

from .client import DbClient, Row, parse_json_column

LIST_COLUMNS = """
    k.id, k.isin, k.nombre_producto, k.nombre_clase, k.gestora, k.gestora_clave, k.fecha_documento,
    k.sri, k.periodo_mantenimiento_anios, k.gastos_corrientes_pct, k.costes_entrada_pct,
    k.costes_salida_pct, k.comision_exito_pct, k.divisa, d.fuente, d.fondo_slug
"""

DETAIL_COLUMNS = (
    LIST_COLUMNS
    + """,
    k.objetivo_inversion, k.modelo_id, k.extraido_en, k.avisos,
    d.fondo_nombre, d.clase_etiqueta, d.url_pagina, d.url_pdf, d.sha256, d.tamano_bytes, d.descargado_en
"""
)

SORTS = {
    "gestora": "k.gestora_clave",
    "nombre": "k.nombre_producto",
    "isin": "k.isin",
    "sri": "k.sri",
    "gastos": "k.gastos_corrientes_pct",
    "fecha": "k.fecha_documento",
    "periodo": "k.periodo_mantenimiento_anios",
}


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class KidRepository:
    def __init__(self, db: DbClient):
        self._db = db

    # ------------------------------------------------------------------------------------------------
    def ping(self) -> bool:
        return bool(self._db.execute("SELECT 1 AS ok"))

    def list_kids(
        self,
        *,
        gestora: str | None = None,
        fuente: str | None = None,
        q: str | None = None,
        isin: str | None = None,
        sri_min: int | None = None,
        sri_max: int | None = None,
        gastos_max: float | None = None,
        solo_vigentes: bool = True,
        sort: str = "gestora",
        desc: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[int, list[Row]]:
        where: list[str] = []
        params: dict[str, Any] = {}
        if gestora:
            where.append("k.gestora_clave LIKE :gestora")
            params["gestora"] = _like(gestora.lower())
        if fuente:
            where.append("d.fuente = :fuente")
            params["fuente"] = fuente
        if q:
            # Un nombre de parametro por uso: no se depende de que la Data API admita repetidos.
            where.append(
                "(k.nombre_producto ILIKE :q1 OR k.isin ILIKE :q2 OR k.gestora ILIKE :q3 "
                "OR k.nombre_clase ILIKE :q4)"
            )
            params.update({"q1": _like(q), "q2": _like(q), "q3": _like(q), "q4": _like(q)})
        if isin:
            where.append("k.isin = :isin")
            params["isin"] = isin.upper()
        if sri_min is not None:
            where.append("k.sri >= :sri_min")
            params["sri_min"] = sri_min
        if sri_max is not None:
            where.append("k.sri <= :sri_max")
            params["sri_max"] = sri_max
        if gastos_max is not None:
            where.append("k.gastos_corrientes_pct <= :gastos_max")
            params["gastos_max"] = gastos_max

        source = "kids_vigentes" if solo_vigentes else "kids"
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        base = f"FROM {source} k JOIN documentos d ON d.id = k.documento_id{clause}"

        total = int(self._db.execute(f"SELECT COUNT(*) AS n {base}", params)[0]["n"])
        order_col = SORTS.get(sort, SORTS["gestora"])
        direction = "DESC" if desc else "ASC"
        rows = self._db.execute(
            f"SELECT {LIST_COLUMNS} {base} ORDER BY {order_col} {direction} NULLS LAST, k.id "
            "LIMIT :limit OFFSET :offset",
            {**params, "limit": limit, "offset": offset},
        )
        return total, rows

    def get_kid(self, kid_id: int) -> Row | None:
        rows = self._db.execute(
            f"SELECT {DETAIL_COLUMNS} FROM kids k JOIN documentos d ON d.id = k.documento_id WHERE k.id = :id",
            {"id": kid_id},
        )
        if not rows:
            return None
        kid = rows[0]
        kid["avisos"] = parse_json_column(kid.get("avisos"), [])
        kid["escenarios"] = self._db.execute(
            "SELECT escenario, periodo, valor_final_eur, rentabilidad_anual_pct "
            "FROM escenarios WHERE kid_id = :id ORDER BY id",
            {"id": kid_id},
        )
        kid["distribucion_activos"] = self._db.execute(
            "SELECT categoria, porcentaje FROM distribucion_activos WHERE kid_id = :id ORDER BY porcentaje DESC",
            {"id": kid_id},
        )
        return kid

    def history(self, isin: str) -> list[Row]:
        return self._db.execute(
            f"SELECT {LIST_COLUMNS}, d.descargado_en FROM kids k JOIN documentos d ON d.id = k.documento_id "
            "WHERE k.isin = :isin ORDER BY k.fecha_documento DESC NULLS LAST, k.extraido_en DESC",
            {"isin": isin.upper()},
        )

    def pdf_location(self, kid_id: int) -> tuple[str, str] | None:
        rows = self._db.execute(
            "SELECT d.s3_bucket, d.s3_key FROM kids k JOIN documentos d ON d.id = k.documento_id WHERE k.id = :id",
            {"id": kid_id},
        )
        return (rows[0]["s3_bucket"], rows[0]["s3_key"]) if rows else None

    def gestoras(self) -> list[Row]:
        return self._db.execute(
            """
            SELECT k.gestora_clave AS clave,
                   MODE() WITHIN GROUP (ORDER BY k.gestora) AS nombre,
                   COUNT(*) AS n_kids,
                   ROUND(AVG(k.gastos_corrientes_pct), 3) AS gastos_medio_pct,
                   ROUND(AVG(k.sri), 2) AS sri_medio
            FROM kids_vigentes k
            WHERE k.gestora_clave IS NOT NULL
            GROUP BY k.gestora_clave
            ORDER BY n_kids DESC, nombre
            """
        )

    def stats(self) -> dict[str, Any]:
        totales = self._db.execute(
            """
            SELECT (SELECT COUNT(*) FROM documentos) AS documentos,
                   (SELECT COUNT(*) FROM kids) AS kids,
                   (SELECT COUNT(*) FROM kids_vigentes) AS kids_vigentes,
                   (SELECT COUNT(DISTINCT gestora_clave) FROM kids_vigentes) AS gestoras,
                   (SELECT ROUND(AVG(gastos_corrientes_pct), 3) FROM kids_vigentes) AS gastos_medio_pct,
                   (SELECT ROUND(AVG(sri), 2) FROM kids_vigentes) AS sri_medio,
                   (SELECT MAX(descargado_en) FROM documentos) AS ultima_descarga
            """
        )[0]
        por_sri = self._db.execute(
            "SELECT sri, COUNT(*) AS n FROM kids_vigentes WHERE sri IS NOT NULL GROUP BY sri ORDER BY sri"
        )
        por_fuente = self._db.execute(
            "SELECT fuente, COUNT(*) AS n FROM documentos GROUP BY fuente ORDER BY fuente"
        )
        return {
            **totales,
            "por_sri": por_sri,
            "por_fuente": por_fuente,
            "por_gestora": self.gestoras(),
        }

    def documentos(self, limit: int = 200, offset: int = 0) -> list[Row]:
        return self._db.execute(
            """
            SELECT d.id, d.fuente, d.fondo_slug, d.fondo_nombre, d.clase_etiqueta, d.url_pagina, d.url_pdf,
                   d.sha256, d.tamano_bytes, d.descargado_en, k.id AS kid_id, k.isin
            FROM documentos d LEFT JOIN kids k ON k.documento_id = d.id
            ORDER BY d.fuente, d.fondo_slug, d.clase_etiqueta NULLS FIRST
            LIMIT :limit OFFSET :offset
            """,
            {"limit": limit, "offset": offset},
        )
