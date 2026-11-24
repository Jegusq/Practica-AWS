"""Siembra datos SINTETICOS en una base PostgreSQL LOCAL para desarrollar la API y la app sin AWS.

Seguridad: solo funciona si KID_DB_DSN apunta a localhost/127.0.0.1. Nunca se usa en AWS (alli los datos
salen de los KID reales). Los nombres son ficticios a proposito para que no se confundan con datos reales.

    set KID_DB_DSN=postgresql://postgres:postgres@127.0.0.1:54329/postgres?sslmode=disable
    python scripts/seed_demo.py
"""

from __future__ import annotations

import hashlib
import os
import random
import sys
from urllib.parse import urlsplit

from kid.db.loader import upsert_kid
from kid.db.local import PsycopgClient
from kid.db.migrate import migrate
from kid.manifest import ManifestEntry

GESTORAS = {
    "demo_alfa": "Gestora Demo Alfa, S.G.I.I.C., S.A.",
    "demo_beta": "GESTORA DEMO BETA SGIIC, S.A.",
    "demo_gamma": "Gestora Demo Gamma Asset Management, SGIIC, S.A.",
}
FONDOS = [
    "Renta Variable Global",
    "Iberia",
    "Bolsa EEUU",
    "Mixto",
    "Renta Fija Corto",
    "Small Caps",
    "Emergentes",
]
CLASES = [None, "Clase A", "Clase B", "Clase C"]
ESCENARIOS = [("tension", -0.17), ("desfavorable", -0.03), ("moderado", 0.05), ("favorable", 0.11)]


def isin_with_check(body11: str) -> str:
    """Construye un ISIN con digito de control valido a partir de 11 caracteres."""
    digits = "".join(str(int(c, 36)) for c in body11)
    for check in range(10):
        total = 0
        for i, ch in enumerate(reversed(digits + str(check))):
            n = int(ch)
            if i % 2 == 1:
                n = n * 2 - 9 if n * 2 > 9 else n * 2
            total += n
        if total % 10 == 0:
            return body11 + str(check)
    raise AssertionError


def main() -> int:
    dsn = os.environ.get("KID_DB_DSN", "")
    if urlsplit(dsn).hostname not in {"localhost", "127.0.0.1", "::1"}:
        print(
            "Rechazado: KID_DB_DSN debe apuntar a una base LOCAL (localhost). No se siembra nada."
        )
        return 2
    rng = random.Random(42)
    db = PsycopgClient(dsn)
    migrate(db)
    n = 0
    for fuente, gestora in GESTORAS.items():
        for fondo in rng.sample(FONDOS, 5):
            for clase in rng.sample(CLASES, rng.choice([1, 2])):
                slug = f"{fuente}-{fondo}".lower().replace(" ", "-")
                sha = hashlib.sha256(f"{fuente}{fondo}{clase}".encode()).hexdigest()
                sri = rng.randint(2, 7)
                n += 1
                entry = ManifestEntry(
                    fuente=fuente,
                    fondo_slug=slug,
                    fondo_nombre=f"{fondo} (demo)",
                    url_pagina=f"https://demo.invalid/{slug}/",
                    url_pdf=f"https://demo.invalid/{slug}/{(clase or 'unica').lower().replace(' ', '-')}.pdf",
                    clase_etiqueta=clase,
                    sha256=sha,
                    tamano_bytes=rng.randint(100_000, 600_000),
                    s3_bucket="local",
                    s3_key=f"kids/{fuente}/{slug}/{sha}.pdf",
                    descargado_en="2026-11-22T10:00:00Z",
                )
                horizon = rng.choice([3, 5, 8])
                payload = {
                    "modelo_id": "demo-sintetico",
                    "extraido_en": "2026-11-22T11:00:00Z",
                    "avisos": ["campo_obligatorio_vacio: sri"] if rng.random() < 0.08 else [],
                    "datos": {
                        "isin": isin_with_check(f"ES{rng.randint(10**8, 10**9 - 1)}"),
                        "nombre_producto": f"{gestora.split(',')[0].title()} {fondo}, F.I.",
                        "nombre_clase": clase,
                        "gestora": gestora,
                        "fecha_documento": f"2026-{rng.randint(1, 10):02d}-{rng.randint(1, 28):02d}",
                        "sri": sri,
                        "periodo_mantenimiento_anios": float(horizon),
                        "gastos_corrientes_pct": round(0.4 + sri * 0.28 + rng.random() * 0.5, 2),
                        "costes_entrada_pct": 0.0,
                        "costes_salida_pct": rng.choice([0.0, 0.0, 0.5]),
                        "divisa": "EUR",
                        "comision_exito_pct": rng.choice([0.0, 0.0, 9.0]),
                        "objetivo_inversion": f"Fondo sintetico de demostracion ({fondo}).",
                        "escenarios": [
                            {
                                "escenario": name,
                                "periodo": f"{horizon} años",
                                "valor_final_eur": round(
                                    10000 * (1 + ret * (sri / 4)) ** horizon, 0
                                ),
                                "rentabilidad_anual_pct": round(ret * (sri / 4) * 100, 1),
                            }
                            for name, ret in ESCENARIOS
                        ],
                        "distribucion_activos": (
                            [
                                {"categoria": "Renta variable", "porcentaje": 70.0 + sri},
                                {"categoria": "Liquidez", "porcentaje": 30.0 - sri},
                            ]
                            if sri >= 4
                            else []
                        ),
                    },
                }
                with db.transaction() as tx:
                    upsert_kid(tx, entry, payload)
    print(f"Sembrados {n} KID sinteticos en la base local.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
