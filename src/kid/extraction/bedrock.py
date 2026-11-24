"""Extraccion con Amazon Bedrock (Converse API) usando SALIDAS ESTRUCTURADAS.

No se le pide al modelo que "devuelva JSON" para parsear texto: se envia el JSON Schema en
``outputConfig.textFormat`` y Bedrock restringe la decodificacion para que la respuesta cumpla el esquema.
El PDF se adjunta tal cual como bloque ``document`` (no hace falta extraer el texto antes).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .schema import KIDExtraction, bedrock_json_schema

SYSTEM_PROMPT = """\
Eres un analista que extrae datos de Documentos de Datos Fundamentales (KID / DFI / PRIIP) de fondos \
de inversion españoles. Recibiras un PDF y devolveras los campos del esquema.

Reglas:
- Usa SOLO lo que figura en el documento. Si un dato no aparece, devuelve null (o lista vacia). \
No lo infieras ni lo inventes.
- Los porcentajes se devuelven como numero en porcentaje (1,52 % -> 1.52), con punto decimal, sin el simbolo %.
- Los importes en euros se devuelven como numero sin separador de miles ni simbolo (10.450 € -> 10450).
- fecha_documento en formato AAAA-MM-DD.
- El SRI es el numero de 1 a 7 del indicador resumido de riesgo.
- gastos_corrientes_pct son las comisiones de gestion y otros costes administrativos o de funcionamiento \
(porcentaje anual). No lo confundas con los costes de operacion/transaccion ni con la comision de exito.
- Si el documento dice expresamente que no cobra costes de entrada, salida o comision de exito, devuelve 0.
- Si el documento contiene varias clases, extrae la clase a la que se refiere el documento (la que \
figura en su cabecera).
"""

USER_PROMPT = "Extrae los datos de este documento de datos fundamentales."

# Limite del bloque document de Converse: 4,5 MB por documento.
MAX_PDF_BYTES = 4_500_000


class ExtractionError(RuntimeError):
    pass


@dataclass
class ExtractionResult:
    data: KIDExtraction
    model_id: str
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str = ""
    latency_s: float = 0.0


def _document_name(label: str) -> str:
    """Bedrock solo admite alfanumericos, espacios, guiones, parentesis y corchetes en el nombre."""
    cleaned = re.sub(r"[^A-Za-z0-9 \-()\[\]]", "-", label)
    return re.sub(r"\s{2,}", " ", cleaned).strip()[:60] or "KID"


class BedrockExtractor:
    def __init__(self, model_id: str, region: str | None = None, client: Any = None):
        self.model_id = model_id
        if client is None:
            import boto3
            from botocore.config import Config

            # La primera peticion con un esquema nuevo compila la gramatica (hasta ~90 s): timeout holgado.
            client = boto3.client(
                "bedrock-runtime",
                region_name=region,
                config=Config(read_timeout=300, retries={"max_attempts": 5, "mode": "adaptive"}),
            )
        self._client = client
        self._schema_json = json.dumps(bedrock_json_schema(), ensure_ascii=False)

    def build_request(self, pdf: bytes, doc_label: str) -> dict[str, Any]:
        return {
            "modelId": self.model_id,
            "system": [{"text": SYSTEM_PROMPT}],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "document": {
                                "name": _document_name(doc_label),
                                "format": "pdf",
                                "source": {"bytes": pdf},
                            }
                        },
                        {"text": USER_PROMPT},
                    ],
                }
            ],
            "inferenceConfig": {"maxTokens": 8192},
            "outputConfig": {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {
                        "jsonSchema": {
                            "schema": self._schema_json,
                            "name": "kid_extraction",
                            "description": "Datos estructurados de un KID de fondo de inversion",
                        }
                    },
                }
            },
        }

    def extract(self, pdf: bytes, doc_label: str = "KID") -> ExtractionResult:
        if len(pdf) > MAX_PDF_BYTES:
            raise ExtractionError(f"PDF de {len(pdf)} bytes: supera el limite de Converse (4,5 MB)")
        started = time.monotonic()
        response = self._client.converse(**self.build_request(pdf, doc_label))
        latency = time.monotonic() - started

        stop_reason = response.get("stopReason", "")
        if stop_reason == "max_tokens":
            raise ExtractionError("la respuesta se corto por maxTokens: el JSON esta incompleto")

        blocks = response["output"]["message"]["content"]
        text = next((b["text"] for b in blocks if "text" in b), None)
        if text is None:
            raise ExtractionError(f"la respuesta no contiene texto (stopReason={stop_reason})")

        try:
            data = KIDExtraction.model_validate_json(text)
        except (
            ValueError
        ) as exc:  # no deberia ocurrir con salidas estructuradas; se registra si pasa
            raise ExtractionError(f"respuesta que no cumple el esquema: {exc}") from exc

        usage = {
            k: int(v) for k, v in response.get("usage", {}).items() if isinstance(v, int | float)
        }
        return ExtractionResult(
            data=data,
            model_id=self.model_id,
            usage=usage,
            stop_reason=stop_reason,
            latency_s=round(latency, 2),
        )
