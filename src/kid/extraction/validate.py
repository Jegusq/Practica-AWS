"""Validaciones posteriores a la extraccion.

Las salidas estructuradas garantizan la FORMA de la respuesta, no que los datos sean correctos. Aqui se
comprueba lo que el esquema no puede expresar (rangos, digito de control del ISIN...). Un dato invalido se
pone a ``None`` y se anota un aviso: asi no entra basura en la base de datos y queda constancia.
"""

from __future__ import annotations

import re

from .schema import KIDExtraction

ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def isin_checksum_ok(isin: str) -> bool:
    """Digito de control del ISIN (ISO 6166): Luhn sobre las letras expandidas a numeros (A=10 ... Z=35)."""
    if not ISIN_RE.match(isin):
        return False
    digits = "".join(str(int(c, 36)) for c in isin)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def sanitize(
    data: KIDExtraction, *, isin_hint: str | None = None
) -> tuple[KIDExtraction, list[str]]:
    """Devuelve una copia saneada de ``data`` y la lista de avisos generados."""
    avisos: list[str] = []
    clean = data.model_copy(deep=True)

    if clean.isin is not None:
        clean.isin = clean.isin.replace(" ", "").upper()
        if not isin_checksum_ok(clean.isin):
            avisos.append(
                f"isin_invalido: {clean.isin!r} no supera la validacion de formato/digito de control"
            )
            clean.isin = None
    if clean.isin and isin_hint and clean.isin != isin_hint.upper():
        avisos.append(f"isin_distinto_de_la_web: documento={clean.isin} web={isin_hint}")

    if clean.sri is not None and not 1 <= clean.sri <= 7:
        avisos.append(f"sri_fuera_de_rango: {clean.sri}")
        clean.sri = None

    if (
        clean.periodo_mantenimiento_anios is not None
        and not 0 < clean.periodo_mantenimiento_anios <= 50
    ):
        avisos.append(f"periodo_fuera_de_rango: {clean.periodo_mantenimiento_anios}")
        clean.periodo_mantenimiento_anios = None

    for campo in (
        "gastos_corrientes_pct",
        "costes_entrada_pct",
        "costes_salida_pct",
        "comision_exito_pct",
    ):
        valor = getattr(clean, campo)
        if valor is not None and not 0 <= valor <= 25:
            avisos.append(f"{campo}_fuera_de_rango: {valor}")
            setattr(clean, campo, None)

    if clean.divisa is not None:
        clean.divisa = clean.divisa.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", clean.divisa):
            avisos.append(f"divisa_invalida: {clean.divisa!r}")
            clean.divisa = None

    if clean.distribucion_activos:
        total = sum(d.porcentaje for d in clean.distribucion_activos)
        if any(not 0 <= d.porcentaje <= 100 for d in clean.distribucion_activos) or total > 105:
            avisos.append(f"distribucion_activos_incoherente: suma={total:.1f}")
            clean.distribucion_activos = []

    for campo in ("isin", "nombre_producto", "gestora", "sri"):
        if getattr(clean, campo) is None:
            avisos.append(f"campo_obligatorio_vacio: {campo}")

    return clean, avisos
