"""Almacenamiento de objetos: S3 en AWS o una carpeta local para desarrollo.

Organizacion de claves (misma en S3 y en local):

    kids/<fuente>/<fondo>/<clase>/<sha256>.pdf    PDFs originales (inmutables; el hash identifica la version)
    manifests/<AAAAMMDDTHHMMSSZ>.jsonl            una linea por PDF: de donde viene y cuando se bajo
    extractions/<modelo>/<sha256>.json            resultado de la extraccion con IA para cada PDF
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol
from urllib.parse import quote


class Store(Protocol):
    bucket: str

    def put(
        self, key: str, data: bytes, *, content_type: str, metadata: dict[str, str] | None = None
    ) -> None: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def list_keys(self, prefix: str) -> list[str]: ...


def _ascii_metadata(metadata: dict[str, str] | None) -> dict[str, str]:
    """Los metadatos de usuario de S3 solo admiten ASCII: se codifican en porcentaje."""
    return {k.lower(): quote(str(v), safe=":/?&=+-._~") for k, v in (metadata or {}).items()}


class S3Store:
    def __init__(self, bucket: str, region: str | None = None, client=None):
        import boto3

        self.bucket = bucket
        self._s3 = client or boto3.client("s3", region_name=region)

    def put(self, key, data, *, content_type, metadata=None):
        self._s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            Metadata=_ascii_metadata(metadata),
        )

    def get(self, key):
        return self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key):
        from botocore.exceptions import ClientError

        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def list_keys(self, prefix):
        paginator = self._s3.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return sorted(keys)


class LocalStore:
    """Misma interfaz sobre el disco. Util para probar sin AWS (los metadatos van en un .meta.json)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.bucket = "local"

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError(f"clave fuera de la carpeta de datos: {key}")
        return path

    def put(self, key, data, *, content_type, metadata=None):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if metadata:
            meta = path.with_name(path.name + ".meta.json")
            meta.write_text(
                json.dumps(
                    {"content_type": content_type, **metadata}, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )

    def get(self, key):
        return self._path(key).read_bytes()

    def exists(self, key):
        return self._path(key).is_file()

    def list_keys(self, prefix):
        base = self.root.resolve()
        if not base.exists():
            return []
        return sorted(
            rel
            for p in base.rglob("*")
            if p.is_file()
            and not p.name.endswith(".meta.json")
            and (rel := p.relative_to(base).as_posix()).startswith(prefix)
        )


def open_store(bucket: str | None, local_dir: str | None, region: str | None = None) -> Store:
    if local_dir:
        return LocalStore(local_dir)
    if not bucket:
        raise SystemExit(
            "Indica --bucket (o KID_BUCKET) o bien --local-dir para trabajar en local."
        )
    return S3Store(bucket, region)
