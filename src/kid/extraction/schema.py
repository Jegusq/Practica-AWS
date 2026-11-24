"""Esquema de extraccion de un KID.

El esquema se define UNA vez en Pydantic y de el salen:
  * el JSON Schema que se envia a Bedrock (salidas estructuradas, ``outputConfig.textFormat``);
  * la validacion posterior de la respuesta;
  * la forma de los datos que se cargan en PostgreSQL.

Limites de las salidas estructuradas de Bedrock que condicionan el diseno (doc. oficial):
  * todo objeto lleva ``additionalProperties: false``;
  * como maximo 12 propiedades *opcionales* (las que no estan en ``required``);
  * como maximo 16 propiedades de tipo union (``anyOf`` / ``["string","null"]``);
  * no se admiten ``minimum``/``maximum``/``minLength``/``maxLength``.

Por eso TODOS los campos son obligatorios en el esquema (``required``) y los que pueden faltar son
*nulables*: el modelo devuelve ``null`` cuando el dato no aparece en el KID (no debe inventarlo).
Los rangos (SRI 1-7, porcentajes...) se comprueban despues en ``validate.py``.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Escenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    escenario: Literal["tension", "desfavorable", "moderado", "favorable"] = Field(
        description="Tipo de escenario de rentabilidad (sin tildes): tension, desfavorable, moderado o favorable."
    )
    periodo: str = Field(
        description="Horizonte de inversion del escenario tal y como figura en el documento, "
        "p. ej. '1 año' o '5 años (periodo de mantenimiento recomendado)'."
    )
    valor_final_eur: float | None = Field(
        description="Lo que podria recibir el inversor tras costes, en euros, sin separador de miles. "
        "null si no aparece."
    )
    rentabilidad_anual_pct: float | None = Field(
        description="Rentabilidad media cada año en porcentaje (p. ej. -3.2 o 6.8). null si no aparece."
    )


class DistribucionActivo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categoria: str = Field(
        description="Categoria de activo o geografia de la distribucion de la cartera, p. ej. 'Renta variable'."
    )
    porcentaje: float = Field(description="Porcentaje de la cartera (0-100).")


class KIDExtraction(BaseModel):
    """Datos extraidos de un Documento de Datos Fundamentales (KID/PRIIP/DFI)."""

    model_config = ConfigDict(extra="forbid")

    # ---- Campos minimos del Anexo A del enunciado ----
    isin: str | None = Field(
        description="Codigo ISIN de la clase (12 caracteres, p. ej. ES0112616000). Si el documento "
        "lista varios ISIN, el de la clase a la que se refiere el documento."
    )
    nombre_producto: str | None = Field(
        description="Nombre del fondo tal y como aparece en el KID (p. ej. 'BESTINFOND, F.I.')."
    )
    nombre_clase: str | None = Field(
        description="Clase o serie del fondo (p. ej. 'Clase A', 'Clase C', 'P Acc EUR'). "
        "null si el documento no indica clase."
    )
    gestora: str | None = Field(
        description="Productor del PRIIP / sociedad gestora (p. ej. 'BESTINVER GESTION SGIIC, S.A.')."
    )
    fecha_documento: date | None = Field(
        description="Fecha del documento / fecha de validez del KID en formato AAAA-MM-DD. "
        "null si no aparece una fecha."
    )
    sri: int | None = Field(
        description="Indicador resumido de riesgo (SRI), entero de 1 a 7. null si no aparece."
    )
    periodo_mantenimiento_anios: float | None = Field(
        description="Periodo de mantenimiento recomendado, expresado en años (p. ej. 5 o 8.5)."
    )
    gastos_corrientes_pct: float | None = Field(
        description="Comisiones de gestion y otros costes administrativos o de funcionamiento, en % "
        "anual del valor de la inversion (p. ej. 1.52). No incluyas los costes de operacion."
    )
    costes_entrada_pct: float | None = Field(
        description="Costes de entrada en %. 0 si el documento indica que no se cobran; "
        "null si no se mencionan."
    )
    costes_salida_pct: float | None = Field(
        description="Costes de salida en %. 0 si el documento indica que no se cobran; "
        "null si no se mencionan."
    )

    # ---- Ampliaciones ----
    divisa: str | None = Field(description="Divisa del producto (codigo ISO 4217, p. ej. EUR).")
    comision_exito_pct: float | None = Field(
        description="Comision de rendimiento / de exito en % si existe; 0 si no se cobra; null si no se menciona."
    )
    objetivo_inversion: str | None = Field(
        description="Resumen en una o dos frases, en español, del objetivo y la politica de inversion."
    )
    escenarios: list[Escenario] = Field(
        description="Escenarios de rentabilidad de la tabla del KID (puede haber varios horizontes). "
        "Lista vacia si el documento no los incluye."
    )
    distribucion_activos: list[DistribucionActivo] = Field(
        description="Distribucion de activos (allocation) SOLO si el texto la indica explicitamente "
        "con porcentajes (p. ej. 'mas del 75% en renta variable'). Lista vacia en caso contrario."
    )


_STRIP_KEYS = {
    "title",
    "default",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "multipleOf",
}


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            merged = copy.deepcopy(defs[name])
            extra = {k: v for k, v in node.items() if k != "$ref"}
            return _inline_refs({**merged, **extra}, defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k not in _STRIP_KEYS}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def bedrock_json_schema() -> dict[str, Any]:
    """JSON Schema compatible con las salidas estructuradas de Bedrock."""
    schema = KIDExtraction.model_json_schema()
    defs = schema.pop("$defs", {})
    schema = _inline_refs(schema, defs)
    schema["additionalProperties"] = False
    return schema


def check_bedrock_compat(schema: dict[str, Any]) -> list[str]:
    """Comprueba localmente las restricciones documentadas por Bedrock. Devuelve la lista de problemas."""
    problems: list[str] = []
    optional = 0
    unions = 0

    def walk(node: Any, path: str) -> None:
        nonlocal optional, unions
        if isinstance(node, dict):
            if node.get("type") == "object":
                if node.get("additionalProperties") is not False:
                    problems.append(f"{path}: falta additionalProperties=false")
                props = node.get("properties", {})
                required = set(node.get("required", []))
                optional += len([p for p in props if p not in required])
                for _name, sub in props.items():
                    if isinstance(sub, dict) and (
                        "anyOf" in sub or isinstance(sub.get("type"), list)
                    ):
                        unions += 1
            for key in node:
                if key in _STRIP_KEYS - {"title", "default"}:
                    problems.append(f"{path}: palabra clave no soportada '{key}'")
            if "$ref" in node:
                problems.append(f"{path}: $ref sin resolver")
            for key, value in node.items():
                walk(value, f"{path}/{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(schema, "#")
    if optional > 12:
        problems.append(f"{optional} propiedades opcionales (maximo 12)")
    if unions > 16:
        problems.append(f"{unions} propiedades de tipo union (maximo 16)")
    return problems
