"""Manifiesto de captura: para cada PDF, de donde viene y cuando se descargo (trazabilidad)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime

from .storage import Store

MANIFEST_PREFIX = "manifests/"


@dataclass
class Candidate:
    """Un KID descubierto en la web de una gestora, todavia sin descargar."""

    fuente: str  # bestinver | azvalor | cobas
    fondo_slug: str
    fondo_nombre: str
    url_pagina: str  # ficha del fondo donde se encontro el enlace
    url_pdf: str  # URL tal cual la publica la web
    clase_etiqueta: str | None = None  # p. ej. "Clase A"
    isin_pista: str | None = None  # ISIN si la web lo da junto al enlace (se contrasta con el PDF)


@dataclass
class ManifestEntry(Candidate):
    estado: str = "ok"  # ok | error | omitido_robots
    descargado_en: str = field(default_factory=lambda: now_iso())
    http_status: int | None = None
    content_type: str | None = None
    tamano_bytes: int | None = None
    sha256: str | None = None
    s3_bucket: str | None = None
    s3_key: str | None = None
    scraper_version: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> ManifestEntry:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_manifest(store: Store, entries: list[ManifestEntry], run_ts: str | None = None) -> str:
    stamp = (
        (run_ts or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")).replace(":", "").replace("-", "")
    )
    key = f"{MANIFEST_PREFIX}{stamp}.jsonl"
    body = "\n".join(json.dumps(asdict(e), ensure_ascii=False) for e in entries) + "\n"
    store.put(key, body.encode("utf-8"), content_type="application/x-ndjson")
    return key


def read_manifests(store: Store) -> list[ManifestEntry]:
    """Une todos los manifiestos y deja una entrada por PDF (sha256); gana la descarga mas reciente."""
    latest: dict[str, ManifestEntry] = {}
    for key in store.list_keys(MANIFEST_PREFIX):
        for line in store.get(key).decode("utf-8").splitlines():
            if not line.strip():
                continue
            entry = ManifestEntry.from_dict(json.loads(line))
            if entry.estado != "ok" or not entry.sha256:
                continue
            prev = latest.get(entry.sha256)
            if prev is None or entry.descargado_en >= prev.descargado_en:
                latest[entry.sha256] = entry
    return sorted(latest.values(), key=lambda e: (e.fuente, e.fondo_slug, e.clase_etiqueta or ""))
