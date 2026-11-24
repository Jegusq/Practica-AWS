"""Tests del modelo de datos, la carga idempotente y las consultas, contra un PostgreSQL real."""

from __future__ import annotations

import json

import pytest

from kid.db.loader import load_all, normalizar_gestora, upsert_kid
from kid.db.migrate import migrate, split_statements
from kid.db.repository import KidRepository
from kid.extraction.runner import extraction_key
from kid.manifest import write_manifest
from kid.storage import LocalStore
from tests.factories import make_entry, make_payload

MODEL = "eu.anthropic.claude-haiku-5-5"


def count(db, table: str) -> int:
    return db.execute(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]


def seed(db, rows):
    """rows: lista de (entry, overrides_de_datos)."""
    for entry, overrides in rows:
        with db.transaction() as tx:
            upsert_kid(tx, entry, make_payload(**overrides))


class TestNormalizarGestora:
    @pytest.mark.parametrize(
        "a,b",
        [
            ("BESTINVER GESTION SGIIC, S.A.", "Bestinver Gestión, S.A., S.G.I.I.C."),
            ("COBAS ASSET MANAGEMENT, S.G.I.I.C., S.A.", "Cobas Asset Management, SGIIC, S.A."),
        ],
    )
    def test_variants_share_the_same_key(self, a, b):
        assert normalizar_gestora(a) == normalizar_gestora(b)

    def test_key_value_and_none(self):
        assert (
            normalizar_gestora("Azvalor Asset Management, SGIIC, S.A.")
            == "azvalor asset management"
        )
        assert normalizar_gestora(None) is None and normalizar_gestora("S.A.") is None


class TestSchemaAndSplit:
    def test_split_ignores_comments_and_empty_chunks(self):
        sql = "-- comentario; con punto y coma\nCREATE TABLE a (x int);\n\n-- otro\nCREATE TABLE b (y int);\n"
        assert split_statements(sql) == ["CREATE TABLE a (x int)", "CREATE TABLE b (y int)"]

    def test_migrate_is_idempotent(self, pg):
        migrate(pg)
        migrate(pg)
        assert count(pg, "documentos") == 0


class TestLoad:
    def test_upsert_creates_document_kid_and_children(self, pg):
        seed(pg, [(make_entry(), {})])
        assert (
            count(pg, "documentos"),
            count(pg, "kids"),
            count(pg, "escenarios"),
            count(pg, "distribucion_activos"),
        ) == (1, 1, 2, 1)
        kid = pg.execute("SELECT * FROM kids")[0]
        assert kid["isin"] == "ES0119199034" and kid["sri"] == 6
        assert kid["gestora_clave"] == "cobas asset management"
        assert kid["gastos_corrientes_pct"] == pytest.approx(1.85)

    def test_processing_the_same_document_twice_does_not_duplicate(self, pg):
        entry = make_entry()
        for _ in range(3):
            seed(pg, [(entry, {})])
        assert (
            count(pg, "documentos"),
            count(pg, "kids"),
            count(pg, "escenarios"),
            count(pg, "distribucion_activos"),
        ) == (1, 1, 2, 1)

    def test_reprocessing_updates_values_in_place(self, pg):
        entry = make_entry()
        seed(pg, [(entry, {"sri": 5})])
        seed(pg, [(entry, {"sri": 7, "escenarios": []})])
        assert pg.execute("SELECT sri FROM kids")[0]["sri"] == 7
        assert count(pg, "kids") == 1 and count(pg, "escenarios") == 0  # los hijos se reemplazan

    def test_null_fields_are_stored_as_null_not_invented(self, pg):
        seed(
            pg,
            [
                (
                    make_entry(),
                    {
                        "fecha_documento": None,
                        "periodo_mantenimiento_anios": None,
                        "costes_entrada_pct": None,
                        "isin": None,
                    },
                )
            ],
        )
        row = pg.execute("SELECT * FROM kids")[0]
        assert (
            row["fecha_documento"] is None
            and row["periodo_mantenimiento_anios"] is None
            and row["isin"] is None
        )

    def test_failed_transaction_leaves_nothing_behind(self, pg):
        entry = make_entry()
        bad = make_payload(sri=99)  # viola CHECK (sri BETWEEN 1 AND 7)
        with pytest.raises(Exception), pg.transaction() as tx:  # noqa: B017
            upsert_kid(tx, entry, bad)
        assert count(pg, "documentos") == 0 and count(pg, "kids") == 0

    def test_load_all_reads_manifest_and_extractions_from_store(self, pg, tmp_path):
        store = LocalStore(tmp_path)
        entries = [make_entry(slug="a"), make_entry(slug="b"), make_entry(slug="sin-extraccion")]
        write_manifest(store, entries, "20261122T120000Z")
        for e, isin in zip(entries[:2], ["ES0114673033", "ES0119199034"], strict=True):
            payload = make_payload(isin=isin)
            store.put(
                extraction_key(MODEL, e.sha256),
                json.dumps(payload).encode(),
                content_type="application/json",
            )

        report = load_all(pg, store, MODEL)
        assert (report.cargados, len(report.sin_extraccion), report.errores) == (2, 1, [])
        again = load_all(pg, store, MODEL)
        assert again.cargados == 2 and count(pg, "kids") == 2


class TestRepository:
    @pytest.fixture
    def repo(self, pg):
        seed(
            pg,
            [
                (
                    make_entry("cobas_iberia", "Clase A"),
                    {
                        "isin": "ES0119199034",
                        "sri": 6,
                        "gastos_corrientes_pct": 1.85,
                        "fecha_documento": "2026-01-15",
                    },
                ),
                (
                    make_entry("cobas_iberia", "Clase A", seed="v2"),
                    {
                        "isin": "ES0119199034",
                        "sri": 6,
                        "gastos_corrientes_pct": 1.70,
                        "fecha_documento": "2026-06-01",
                    },
                ),
                (
                    make_entry("bestinfond", None, fuente="bestinver"),
                    {
                        "isin": "ES0114673033",
                        "nombre_producto": "BESTINFOND, F.I.",
                        "nombre_clase": None,
                        "gestora": "BESTINVER GESTION SGIIC, S.A.",
                        "sri": 5,
                        "gastos_corrientes_pct": 1.60,
                    },
                ),
                (
                    make_entry("renta", None, fuente="azvalor"),
                    {
                        "isin": "ES0112616000",
                        "nombre_producto": "Azvalor Internacional FI",
                        "gestora": "Azvalor Asset Management, SGIIC, S.A.",
                        "sri": 4,
                        "gastos_corrientes_pct": 2.10,
                        "fecha_documento": None,
                    },
                ),
            ],
        )
        return KidRepository(pg)

    def test_only_latest_version_per_isin_by_default(self, repo):
        total, rows = repo.list_kids(limit=50)
        assert total == 3 and len(rows) == 3
        iberia = next(r for r in rows if r["isin"] == "ES0119199034")
        assert (
            iberia["gastos_corrientes_pct"] == pytest.approx(1.70)
            and iberia["fecha_documento"] == "2026-06-01"
        )

    def test_history_lists_all_versions_newest_first(self, repo):
        history = repo.history("es0119199034")
        assert [h["fecha_documento"] for h in history] == ["2026-06-01", "2026-01-15"]
        total_all, _ = repo.list_kids(solo_vigentes=False)
        assert total_all == 4

    def test_filters(self, repo):
        assert repo.list_kids(gestora="bestinver")[0] == 1
        assert repo.list_kids(fuente="azvalor")[0] == 1
        assert repo.list_kids(sri_min=5)[0] == 2
        assert repo.list_kids(sri_min=5, sri_max=5)[0] == 1
        assert repo.list_kids(gastos_max=1.7)[0] == 2
        assert repo.list_kids(isin="es0114673033")[0] == 1
        assert (
            repo.list_kids(q="internacional")[0] == 2
        )  # azvalor internacional + "Cobas Internacional"
        assert repo.list_kids(q="zzz")[0] == 0

    def test_like_wildcards_in_user_input_are_escaped(self, repo):
        assert repo.list_kids(q="%")[0] == 0
        assert repo.list_kids(q="_")[0] == 0

    def test_sorting_pagination_and_nulls_last(self, repo):
        _, rows = repo.list_kids(sort="gastos", desc=True)
        assert [r["gastos_corrientes_pct"] for r in rows] == [2.10, 1.70, 1.60]
        total, page = repo.list_kids(
            sort="gastos", limit=2, offset=2
        )  # ascendente: 1.60, 1.70, 2.10
        assert total == 3 and [r["gastos_corrientes_pct"] for r in page] == [2.10]
        _, by_date = repo.list_kids(sort="fecha", desc=True)
        assert by_date[-1]["fecha_documento"] is None

    def test_detail_includes_children_and_traceability(self, repo):
        kid_id = repo.list_kids(isin="ES0114673033")[1][0]["id"]
        kid = repo.get_kid(kid_id)
        assert kid["url_pdf"].startswith("https://web.example/") and kid[
            "descargado_en"
        ].startswith("2026-11-22")
        assert [e["escenario"] for e in kid["escenarios"]] == ["tension", "moderado"]
        assert kid["distribucion_activos"][0]["categoria"] == "Renta variable"
        assert kid["avisos"] == [] and repo.get_kid(999999) is None

    def test_stats_and_gestoras_group_name_variants(self, repo):
        stats = repo.stats()
        assert (stats["documentos"], stats["kids"], stats["kids_vigentes"], stats["gestoras"]) == (
            4,
            4,
            3,
            3,
        )
        assert {s["fuente"]: s["n"] for s in stats["por_fuente"]} == {
            "azvalor": 1,
            "bestinver": 1,
            "cobas": 2,
        }
        assert [s["sri"] for s in stats["por_sri"]] == [4, 5, 6]
        names = {g["clave"]: g["n_kids"] for g in repo.gestoras()}
        assert names == {
            "cobas asset management": 1,
            "bestinver gestion": 1,
            "azvalor asset management": 1,
        }

    def test_documents_and_pdf_location(self, repo):
        docs = repo.documentos()
        assert len(docs) == 4 and {"url_pdf", "sha256", "descargado_en", "isin"} <= docs[0].keys()
        kid_id = repo.list_kids(isin="ES0114673033")[1][0]["id"]
        bucket, key = repo.pdf_location(kid_id)
        assert bucket == "bucket-test" and key.startswith("kids/bestinver/")
