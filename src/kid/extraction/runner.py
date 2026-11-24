"""Extraccion por lotes: para cada PDF del manifiesto, llama a Bedrock y guarda el JSON resultante."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ..config import model_slug
from ..manifest import ManifestEntry, now_iso, read_manifests
from ..storage import Store
from .bedrock import BedrockExtractor, ExtractionError
from .validate import sanitize

log = logging.getLogger("kid.extraction")


def extraction_key(model_id: str, sha256: str) -> str:
    return f"extractions/{model_slug(model_id)}/{sha256}.json"


@dataclass
class ExtractionReport:
    extraidos: int = 0
    omitidos: int = 0  # ya extraidos antes (idempotencia)
    errores: list[str] = field(default_factory=list)
    tokens_entrada: int = 0
    tokens_salida: int = 0


def extract_all(
    store: Store,
    extractor: BedrockExtractor,
    *,
    force: bool = False,
    limit: int | None = None,
    entries: list[ManifestEntry] | None = None,
) -> ExtractionReport:
    entries = entries if entries is not None else read_manifests(store)
    report = ExtractionReport()
    for entry in entries[:limit] if limit else entries:
        out_key = extraction_key(extractor.model_id, entry.sha256 or "")
        if not force and store.exists(out_key):
            report.omitidos += 1
            continue
        label = f"{entry.fondo_slug} {entry.clase_etiqueta or ''}".strip()
        try:
            result = extractor.extract(store.get(entry.s3_key or ""), doc_label=label)
        except ExtractionError as exc:
            report.errores.append(f"{entry.s3_key}: {exc}")
            log.error("%s: %s", entry.s3_key, exc)
            continue
        except Exception as exc:  # error de red/permisos: se registra y se sigue con el siguiente
            report.errores.append(f"{entry.s3_key}: {type(exc).__name__}: {exc}")
            log.error("%s: %s: %s", entry.s3_key, type(exc).__name__, exc)
            continue

        clean, avisos = sanitize(result.data, isin_hint=entry.isin_pista)
        payload = {
            "sha256": entry.sha256,
            "modelo_id": result.model_id,
            "extraido_en": now_iso(),
            "latencia_s": result.latency_s,
            "uso": result.usage,
            "avisos": avisos,
            "datos": clean.model_dump(mode="json"),
            "datos_modelo": result.data.model_dump(mode="json"),  # respuesta original, sin sanear
        }
        store.put(
            out_key,
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            content_type="application/json",
        )
        report.extraidos += 1
        report.tokens_entrada += result.usage.get("inputTokens", 0)
        report.tokens_salida += result.usage.get("outputTokens", 0)
        log.info(
            "%s -> isin=%s sri=%s avisos=%s (%.1fs)",
            label,
            clean.isin,
            clean.sri,
            len(avisos),
            result.latency_s,
        )
    return report
