"""Datos de prueba compartidos."""

from __future__ import annotations

import hashlib
from typing import Any

from kid.manifest import ManifestEntry


def make_entry(
    slug: str = "cobas_iberia", clase: str | None = "Clase A", fuente: str = "cobas", seed: str = ""
) -> ManifestEntry:
    sha = hashlib.sha256(f"{fuente}{slug}{clase}{seed}".encode()).hexdigest()
    return ManifestEntry(
        fuente=fuente,
        fondo_slug=slug,
        fondo_nombre=slug.replace("_", " ").title(),
        url_pagina=f"https://web.example/{slug}/",
        url_pdf=f"https://web.example/{slug}/{clase or 'x'}.pdf",
        clase_etiqueta=clase,
        sha256=sha,
        tamano_bytes=1234,
        s3_bucket="bucket-test",
        s3_key=f"kids/{fuente}/{slug}/{(clase or 'unica').lower().replace(' ', '-')}/{sha}.pdf",
        descargado_en="2026-11-22T10:00:00Z",
    )


def make_payload(**overrides: Any) -> dict[str, Any]:
    datos = {
        "isin": "ES0119199034",
        "nombre_producto": "Cobas Internacional, FI",
        "nombre_clase": "Clase A",
        "gestora": "Cobas Asset Management, S.G.I.I.C., S.A.",
        "fecha_documento": "2026-01-15",
        "sri": 6,
        "periodo_mantenimiento_anios": 5.0,
        "gastos_corrientes_pct": 1.85,
        "costes_entrada_pct": 0.0,
        "costes_salida_pct": 0.0,
        "divisa": "EUR",
        "comision_exito_pct": 0.0,
        "objetivo_inversion": "Renta variable global value.",
        "escenarios": [
            {
                "escenario": "tension",
                "periodo": "5 años",
                "valor_final_eur": 3100.0,
                "rentabilidad_anual_pct": -20.9,
            },
            {
                "escenario": "moderado",
                "periodo": "5 años",
                "valor_final_eur": 13400.0,
                "rentabilidad_anual_pct": 6.0,
            },
        ],
        "distribucion_activos": [{"categoria": "Renta variable", "porcentaje": 90.0}],
    }
    datos.update(overrides)
    return {
        "sha256": "x",
        "modelo_id": "eu.anthropic.claude-haiku-5-5",
        "extraido_en": "2026-11-22T11:00:00Z",
        "latencia_s": 3.2,
        "uso": {"inputTokens": 7000, "outputTokens": 600},
        "avisos": [],
        "datos": datos,
        "datos_modelo": datos,
    }
