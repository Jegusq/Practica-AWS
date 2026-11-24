from __future__ import annotations

import json

import pytest

from kid.config import model_slug
from kid.extraction.bedrock import BedrockExtractor, ExtractionError, _document_name
from kid.extraction.runner import extract_all, extraction_key
from kid.extraction.schema import KIDExtraction, bedrock_json_schema, check_bedrock_compat
from kid.extraction.validate import isin_checksum_ok, sanitize
from kid.manifest import write_manifest
from kid.storage import LocalStore
from tests.factories import make_entry, make_payload

ANEXO_A = {
    "isin", "nombre_producto", "nombre_clase", "gestora", "fecha_documento", "sri",
    "periodo_mantenimiento_anios", "gastos_corrientes_pct", "costes_entrada_pct", "costes_salida_pct",
}  # fmt: skip


# ------------------------------------------------------------------------------------ esquema
class TestSchema:
    def test_complies_with_bedrock_structured_output_limits(self):
        assert check_bedrock_compat(bedrock_json_schema()) == []

    def test_contains_all_annex_a_fields_as_required_and_nullable(self):
        schema = bedrock_json_schema()
        assert set(schema["properties"]) >= ANEXO_A
        assert set(schema["required"]) == set(schema["properties"])  # ninguno opcional
        for campo in ANEXO_A:
            kinds = [opt.get("type") for opt in schema["properties"][campo]["anyOf"]]
            assert "null" in kinds, f"{campo} debe poder ser null (no inventar datos)"

    def test_every_object_forbids_additional_properties(self):
        schema = bedrock_json_schema()
        assert schema["additionalProperties"] is False
        assert schema["properties"]["escenarios"]["items"]["additionalProperties"] is False

    def test_checker_detects_violations(self):
        bad = {
            "type": "object",
            "properties": {f"p{i}": {"type": "string", "minLength": 1} for i in range(14)},
        }
        problems = check_bedrock_compat(bad)
        assert any("additionalProperties" in p for p in problems)
        assert any("minLength" in p for p in problems)
        assert any("14 propiedades opcionales" in p for p in problems)

    def test_model_validates_json_with_nulls(self):
        data = KIDExtraction.model_validate_json(
            json.dumps(
                {**make_payload()["datos"], "isin": None, "sri": None, "fecha_documento": None}
            )
        )
        assert data.isin is None and data.fecha_documento is None


# ------------------------------------------------------------------------------------ validacion
class TestValidate:
    @pytest.mark.parametrize(
        "isin", ["US0378331005", "ES0114673033", "ES0119199034", "IE00B4L5Y983"]
    )
    def test_valid_isins(self, isin):
        assert isin_checksum_ok(isin)

    @pytest.mark.parametrize(
        "isin", ["US0378331006", "ES011467303", "es0114673033", "1234567890AB", ""]
    )
    def test_invalid_isins(self, isin):
        assert not isin_checksum_ok(isin)

    def test_out_of_range_values_become_none_with_warning(self):
        data = KIDExtraction.model_validate(
            {
                **make_payload()["datos"],
                "sri": 9,
                "gastos_corrientes_pct": 80.0,
                "isin": "ES0119199035",
            }
        )
        clean, avisos = sanitize(data)
        assert clean.sri is None and clean.gastos_corrientes_pct is None and clean.isin is None
        assert any(a.startswith("sri_fuera_de_rango") for a in avisos)
        assert any(a.startswith("isin_invalido") for a in avisos)

    def test_valid_data_passes_untouched(self):
        data = KIDExtraction.model_validate(make_payload()["datos"])
        clean, avisos = sanitize(data)
        assert clean == data and avisos == []

    def test_isin_hint_mismatch_is_flagged(self):
        data = KIDExtraction.model_validate(make_payload()["datos"])
        _, avisos = sanitize(data, isin_hint="ES0114673033")
        assert any("isin_distinto_de_la_web" in a for a in avisos)

    def test_incoherent_allocation_is_discarded(self):
        data = KIDExtraction.model_validate(
            {
                **make_payload()["datos"],
                "distribucion_activos": [
                    {"categoria": "a", "porcentaje": 90},
                    {"categoria": "b", "porcentaje": 60},
                ],
            }
        )
        clean, avisos = sanitize(data)
        assert clean.distribucion_activos == [] and any("distribucion" in a for a in avisos)


# ------------------------------------------------------------------------------------ Bedrock
class FakeBedrock:
    def __init__(self, text=None, stop="end_turn", blocks=None):
        self.calls = []
        self._text, self._stop, self._blocks = text, stop, blocks

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        blocks = self._blocks or [{"text": self._text}]
        return {
            "output": {"message": {"role": "assistant", "content": blocks}},
            "stopReason": self._stop,
            "usage": {"inputTokens": 7000, "outputTokens": 500, "totalTokens": 7500},
        }


class TestBedrock:
    MODEL = "eu.anthropic.claude-haiku-5-5"

    def test_request_uses_structured_outputs_not_free_json(self):
        ex = BedrockExtractor(self.MODEL, client=FakeBedrock())
        req = ex.build_request(b"%PDF-1.7", "cobas_iberia Clase A")
        fmt = req["outputConfig"]["textFormat"]
        assert fmt["type"] == "json_schema"
        schema = json.loads(
            fmt["structure"]["jsonSchema"]["schema"]
        )  # el esquema viaja como cadena JSON
        assert schema["additionalProperties"] is False and "isin" in schema["properties"]
        doc = req["messages"][0]["content"][0]["document"]
        assert doc["format"] == "pdf" and doc["source"]["bytes"] == b"%PDF-1.7"
        assert req["modelId"] == self.MODEL
        assert "devuelve JSON" not in json.dumps(req["system"]).lower()

    def test_document_name_is_sanitized(self):
        assert _document_name("cobas_iberia  Clase A!") == "cobas-iberia Clase A-"
        assert _document_name("") == "KID"

    def test_extract_parses_structured_response(self):
        fake = FakeBedrock(text=json.dumps(make_payload()["datos"]))
        result = BedrockExtractor(self.MODEL, client=fake).extract(b"%PDF", "x")
        assert result.data.isin == "ES0119199034" and result.usage["inputTokens"] == 7000

    def test_extract_skips_reasoning_blocks(self):
        blocks = [
            {"reasoningContent": {"reasoningText": {"text": "pienso..."}}},
            {"text": json.dumps(make_payload()["datos"])},
        ]
        result = BedrockExtractor(self.MODEL, client=FakeBedrock(blocks=blocks)).extract(
            b"%PDF", "x"
        )
        assert result.data.sri == 6

    def test_truncated_response_is_an_error(self):
        with pytest.raises(ExtractionError, match="maxTokens"):
            BedrockExtractor(self.MODEL, client=FakeBedrock(text="{", stop="max_tokens")).extract(
                b"%PDF", "x"
            )

    def test_oversized_pdf_rejected_before_calling_the_model(self):
        fake = FakeBedrock(text="{}")
        with pytest.raises(ExtractionError, match="limite"):
            BedrockExtractor(self.MODEL, client=fake).extract(b"0" * 4_600_000, "x")
        assert fake.calls == []


# ------------------------------------------------------------------------------------ lote
class TestRunner:
    MODEL = "eu.anthropic.claude-haiku-5-5"

    def _store_with_pdf(self, tmp_path, **entry_kw):
        store = LocalStore(tmp_path)
        entry = make_entry(**entry_kw)
        store.put(entry.s3_key, b"%PDF-1.7 hola", content_type="application/pdf")
        write_manifest(store, [entry], "20261122T120000Z")
        return store, entry

    def test_writes_extraction_per_model_and_is_idempotent(self, tmp_path):
        store, entry = self._store_with_pdf(tmp_path)
        fake = FakeBedrock(text=json.dumps(make_payload()["datos"]))
        ex = BedrockExtractor(self.MODEL, client=fake)

        first = extract_all(store, ex)
        assert (first.extraidos, first.omitidos, first.errores) == (1, 0, [])
        payload = json.loads(store.get(extraction_key(self.MODEL, entry.sha256)))
        assert payload["datos"]["isin"] == "ES0119199034"
        assert payload["modelo_id"] == self.MODEL and payload["uso"]["inputTokens"] == 7000
        assert f"extractions/{model_slug(self.MODEL)}/" in extraction_key(self.MODEL, entry.sha256)

        second = extract_all(store, ex)
        assert (second.extraidos, second.omitidos) == (0, 1)
        assert len(fake.calls) == 1  # la segunda pasada no vuelve a pagar al modelo

        forced = extract_all(store, ex, force=True)
        assert forced.extraidos == 1 and len(fake.calls) == 2

    def test_sanitized_data_stored_with_original_and_warnings(self, tmp_path):
        store, entry = self._store_with_pdf(tmp_path)
        fake = FakeBedrock(text=json.dumps({**make_payload()["datos"], "sri": 11}))
        extract_all(store, BedrockExtractor(self.MODEL, client=fake))
        payload = json.loads(store.get(extraction_key(self.MODEL, entry.sha256)))
        assert payload["datos"]["sri"] is None and payload["datos_modelo"]["sri"] == 11
        assert any("sri_fuera_de_rango" in a for a in payload["avisos"])

    def test_one_failure_does_not_stop_the_batch(self, tmp_path):
        store = LocalStore(tmp_path)
        entries = [make_entry(slug="a"), make_entry(slug="b")]
        for e in entries:
            store.put(e.s3_key, b"%PDF", content_type="application/pdf")
        write_manifest(store, entries, "20261122T120000Z")

        class Flaky(FakeBedrock):
            def converse(self, **kw):
                if not self.calls:
                    self.calls.append(kw)
                    raise RuntimeError("ThrottlingException")
                return super().converse(**kw)

        report = extract_all(
            store,
            BedrockExtractor(self.MODEL, client=Flaky(text=json.dumps(make_payload()["datos"]))),
        )
        assert report.extraidos == 1 and len(report.errores) == 1
