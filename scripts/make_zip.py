"""Crea un zip DETERMINISTA (mismo contenido => mismo hash), para que Terraform no despliegue la Lambda
de nuevo cuando no ha cambiado nada: fechas fijas, orden alfabetico y permisos uniformes.

    python scripts/make_zip.py build/lambda build/api.zip
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

FIXED_DATE = (2026, 1, 1, 0, 0, 0)


def main(source: str, target: str) -> None:
    root = Path(source)
    files = sorted(p for p in root.rglob("*") if p.is_file())
    out = Path(target)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(root).as_posix(), FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    print(f"{target}: {len(files)} ficheros, {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
