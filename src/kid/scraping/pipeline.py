"""Captura: descubrir los KID de cada gestora, descargarlos y guardarlos con su manifiesto."""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from collections import Counter
from dataclasses import asdict

from .. import __version__
from ..manifest import Candidate, ManifestEntry, now_iso, write_manifest
from ..storage import Store
from .http import PoliteClient, RobotsDisallowed
from .sources import ALL_SOURCES

log = logging.getLogger("kid.scraping")


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def build_key(c: Candidate, sha256: str) -> str:
    """kids/<fuente>/<fondo>/<clase>/<sha256>.pdf: el hash hace la clave inmutable e idempotente."""
    clase = slugify(c.clase_etiqueta) if c.clase_etiqueta else "unica"
    return f"kids/{c.fuente}/{c.fondo_slug}/{clase}/{sha256}.pdf"


def discover(
    client: PoliteClient, sources: list[str], limit_per_source: int | None = None
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for name in sources:
        try:
            source = ALL_SOURCES[name]()
        except KeyError as exc:
            raise SystemExit(
                f"Fuente desconocida: {name!r}. Disponibles: {', '.join(ALL_SOURCES)}"
            ) from exc
        found = source.discover(client)
        log.info("%s: %s KID encontrados", name, len(found))
        candidates.extend(found[:limit_per_source] if limit_per_source else found)
    return candidates


def download(
    client: PoliteClient, store: Store, candidates: list[Candidate]
) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    for cand in candidates:
        entry = ManifestEntry(**asdict(cand), scraper_version=__version__)
        try:
            resp = client.get(cand.url_pdf)
            entry.http_status = resp.status_code
            entry.content_type = resp.headers.get("Content-Type")
            body = resp.content
            if resp.status_code != 200:
                raise ValueError(f"HTTP {resp.status_code}")
            if not body.startswith(b"%PDF"):
                raise ValueError(f"la respuesta no es un PDF (Content-Type: {entry.content_type})")
            sha = hashlib.sha256(body).hexdigest()
            key = build_key(cand, sha)
            if not store.exists(key):
                store.put(
                    key,
                    body,
                    content_type="application/pdf",
                    metadata={
                        "fuente": cand.fuente,
                        "fondo": cand.fondo_slug,
                        "clase": cand.clase_etiqueta or "",
                        "url-pdf": cand.url_pdf,
                        "url-pagina": cand.url_pagina,
                        "descargado-en": entry.descargado_en,
                        "sha256": sha,
                    },
                )
                log.info("guardado %s (%s KB)", key, len(body) // 1024)
            else:
                log.info("ya existia %s", key)
            entry.sha256, entry.tamano_bytes = sha, len(body)
            entry.s3_bucket, entry.s3_key = store.bucket, key
        except RobotsDisallowed:
            entry.estado, entry.error = "omitido_robots", "robots.txt no permite la descarga"
            log.warning("omitido por robots.txt: %s", cand.url_pdf)
        except Exception as exc:  # una URL rota no debe parar el lote
            entry.estado, entry.error = "error", str(exc)[:500]
            log.error("error con %s: %s", cand.url_pdf, exc)
        entry.descargado_en = entry.descargado_en or now_iso()
        entries.append(entry)
    return entries


def run(
    store: Store,
    sources: list[str],
    *,
    contact: str,
    delay: float = 2.0,
    limit_per_source: int | None = None,
    dry_run: bool = False,
) -> list[ManifestEntry]:
    client = PoliteClient(contact, delay=delay)
    log.info("User-Agent: %s", client.user_agent)
    candidates = discover(client, sources, limit_per_source)
    if dry_run:
        for c in candidates:
            print(f"{c.fuente:10} {c.fondo_slug:38} {c.clase_etiqueta or '-':8} {c.url_pdf}")
        print(f"\n{len(candidates)} KID descubiertos (dry-run: no se ha descargado nada).")
        return []
    entries = download(client, store, candidates)
    if entries:
        key = write_manifest(store, entries)
        log.info("manifiesto: %s", key)
    summary(entries)
    return entries


def summary(entries: list[ManifestEntry]) -> None:
    ok = [e for e in entries if e.estado == "ok"]
    por_fuente = Counter(e.fuente for e in ok)
    print(f"\nDescargados correctamente: {len(ok)} de {len(entries)}")
    for fuente, n in sorted(por_fuente.items()):
        print(f"  {fuente:10} {n}")
    for e in entries:
        if e.estado != "ok":
            print(f"  [{e.estado}] {e.url_pdf} -> {e.error}")
    if len(ok) < 15 or len(por_fuente) < 2:
        print("AVISO: el enunciado exige al menos 15 documentos de al menos 2 fuentes distintas.")
