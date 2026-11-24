from __future__ import annotations

import json

import pytest

from kid.manifest import Candidate, read_manifests
from kid.scraping import pipeline
from kid.scraping.http import PoliteClient, RobotsDisallowed
from kid.scraping.robots import RobotsRules
from kid.scraping.sources import azvalor, bestinver, cobas
from kid.storage import LocalStore

UA = "KIDPipelineBot/0.1.0 (test)"


# ------------------------------------------------------------------------------------ robots.txt
class TestRobots:
    BESTINVER = (
        "User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\nDisallow: /*.pdf$\n"
    )

    def test_wildcard_and_end_anchor(self):
        rules = RobotsRules(self.BESTINVER, UA)
        # La regla literal /*.pdf$ prohibe los PDFs "limpios"...
        assert not rules.allowed("/wp-content/uploads/a.pdf")
        # ...pero no una URL con parametros detras (no termina en .pdf).
        assert rules.allowed("/wp-content/uploads/a.pdf?h=0.5")
        assert rules.allowed("/fondos-de-inversion/bestinfond/")

    def test_longest_match_wins_and_allow_wins_ties(self):
        rules = RobotsRules(self.BESTINVER, UA)
        assert not rules.allowed("/wp-admin/options.php")
        assert rules.allowed("/wp-admin/admin-ajax.php")
        tie = RobotsRules("User-agent: *\nDisallow: /x\nAllow: /x\n", UA)
        assert tie.allowed("/x")

    def test_specific_agent_group_beats_wildcard(self):
        text = "User-agent: *\nDisallow: /\n\nUser-agent: KIDPipelineBot\nAllow: /\n"
        assert RobotsRules(text, UA).allowed("/cualquier/cosa")
        assert not RobotsRules(text, "OtroBot/1.0").allowed("/cualquier/cosa")

    def test_empty_disallow_allows_everything(self):
        assert RobotsRules("User-agent: *\nDisallow:\n", UA).allowed("/lo-que-sea")

    def test_comments_and_multiple_agents_in_group(self):
        text = "User-agent: Googlebot\nUser-agent: *  # todos\nDisallow: /privado\n"
        rules = RobotsRules(text, UA)
        assert not rules.allowed("/privado/x")
        assert rules.allowed("/publico")


# ------------------------------------------------------------------------------------ cliente HTTP
class FakeResponse:
    def __init__(self, status=200, text="", content=b"", headers=None, url="http://x"):
        self.status_code = status
        self.text = text or content.decode("utf-8", "ignore")
        self.content = content or text.encode()
        self.headers = headers or {}
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, routes):
        self.routes = routes  # url -> respuesta o lista de respuestas (se consumen en orden)
        self.headers = {}
        self.calls = []

    def get(self, url, timeout=None, allow_redirects=True):
        self.calls.append(url)
        item = self.routes[url]
        if isinstance(item, list):
            item = item.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_client(routes, **kw):
    sleeps: list[float] = []
    now = [0.0]
    session = FakeSession(routes)
    client = PoliteClient(
        "test",
        session=session,
        sleep=lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s)),
        clock=lambda: now[0],
        **kw,
    )
    return client, session, sleeps


class TestPoliteClient:
    def test_identifies_itself(self):
        client, _, _ = make_client({})
        assert client.user_agent.startswith("KIDPipelineBot/")
        assert "contacto: test" in client.user_agent

    def test_blocks_disallowed_url_without_requesting_it(self):
        routes = {
            "https://s.example/robots.txt": FakeResponse(
                200, "User-agent: *\nDisallow: /privado\n"
            ),
            "https://s.example/privado/x": FakeResponse(200, "secreto"),
        }
        client, session, _ = make_client(routes)
        with pytest.raises(RobotsDisallowed):
            client.get("https://s.example/privado/x")
        assert "https://s.example/privado/x" not in session.calls

    def test_robots_4xx_means_no_restrictions(self):
        routes = {
            "https://s.example/robots.txt": FakeResponse(418),
            "https://s.example/a": FakeResponse(200, "ok"),
        }
        client, _, _ = make_client(routes)
        assert client.get("https://s.example/a").status_code == 200

    def test_robots_5xx_means_disallowed(self):
        routes = {
            "https://s.example/robots.txt": FakeResponse(503),
            "https://s.example/a": FakeResponse(200),
        }
        client, _, _ = make_client(routes)
        with pytest.raises(RobotsDisallowed):
            client.get("https://s.example/a")

    def test_rate_limit_between_requests_to_same_host(self):
        routes = {
            "https://s.example/robots.txt": FakeResponse(404),
            "https://s.example/a": FakeResponse(200),
            "https://s.example/b": FakeResponse(200),
        }
        client, _, sleeps = make_client(routes, delay=2.0)
        client.get("https://s.example/a")
        client.get("https://s.example/b")
        assert sleeps and all(s == pytest.approx(2.0) for s in sleeps)

    def test_retries_503_then_succeeds(self):
        routes = {
            "https://s.example/robots.txt": FakeResponse(404),
            "https://s.example/a": [FakeResponse(503), FakeResponse(200, "ya")],
        }
        client, session, _ = make_client(routes)
        assert client.get("https://s.example/a").status_code == 200
        assert session.calls.count("https://s.example/a") == 2


# ------------------------------------------------------------------------------------ parsers
class TestBestinver:
    def test_index_lists_only_fund_pages(self, fixture_bytes):
        funds = bestinver.parse_index(fixture_bytes("bestinver_index.html"))
        assert funds == [
            ("bestinfond", "https://www.bestinver.es/fondos-de-inversion/bestinfond/"),
            (
                "bestinver-internacional",
                "https://www.bestinver.es/fondos-de-inversion/bestinver-internacional/",
            ),
        ]

    def test_fund_page_finds_kid_and_encodes_space(self, fixture_bytes):
        name, pdf = bestinver.parse_fund_page(
            fixture_bytes("bestinver_fund.html"), "https://www.bestinver.es/x/"
        )
        assert name == "Bestinfond, Fondo de Inversión en Renta Variable"
        assert pdf == (
            "https://www.bestinver.es/wp-content/uploads/bestinfond_fi_datos_fundamentales.pdf"
            "?h=0.57878700%201795353979"
        )

    def test_fund_page_without_kid(self):
        _, pdf = bestinver.parse_fund_page(
            b"<html><title>X - BESTINVER</title><a href='/a.pdf'>Folleto</a></html>",
            "https://www.bestinver.es/x/",
        )
        assert pdf is None


class TestAzvalor:
    def test_picks_dfp_not_other_pdfs(self, fixture_bytes):
        name, pdf = azvalor.parse_fund_page(
            fixture_bytes("azvalor_fund.html"),
            "https://www.azvalor.com/fondos-de-inversion/azvalor-iberia-fi/",
        )
        assert name == "Azvalor Iberia FI"
        assert pdf == "https://www.azvalor.com/wp-content/uploads/2026/01/DFP-Iberia-09.01.2026.pdf"

    def test_index_links(self):
        html = b'<a href="/fondos-de-inversion/blue-chips-fi/">x</a><a href="/fondos-de-inversion/">i</a><a href="https://otra.com/fondos-de-inversion/z/">z</a>'
        assert azvalor.parse_index(html, "https://www.azvalor.com/fondos-de-inversion/") == [
            ("blue-chips-fi", "https://www.azvalor.com/fondos-de-inversion/blue-chips-fi/")
        ]


class TestCobas:
    def test_sitemap_keeps_only_fund_pages(self, fixture_bytes):
        assert cobas.parse_sitemap(fixture_bytes("cobas_sitemap.xml")) == [
            "https://www.cobasam.com/productos/inversion-libre/cobas_internacional/",
            "https://www.cobasam.com/productos/inversion-libre/cobas_renta/",
        ]

    def test_one_block_per_class(self, fixture_bytes):
        blocks = cobas.parse_fund_page(
            fixture_bytes("cobas_fund.html"),
            "https://www.cobasam.com/productos/inversion-libre/cobas_internacional/",
        )
        assert [(f, c) for f, c, _ in blocks] == [
            ("Cobas Internacional FI", "Clase A"),
            ("Cobas Internacional FI", "Clase B"),
            ("Cobas Renta FI", None),
        ]
        assert blocks[0][2].endswith("Clase+A/dfi-internacional.pdf")
        assert blocks[1][2].endswith("Clase+B/dfi-internacional.pdf")


# ------------------------------------------------------------------------------------ descarga + manifiesto
PDF_A = b"%PDF-1.7 contenido A"
PDF_B = b"%PDF-1.7 contenido B"


class StubClient:
    """Cliente que sirve respuestas fijas sin red."""

    def __init__(self, bodies):
        self.bodies = bodies

    def get(self, url):
        body = self.bodies[url]
        if isinstance(body, Exception):
            raise body
        return FakeResponse(200, content=body, headers={"Content-Type": "application/pdf"}, url=url)


def cand(slug, url, clase=None, fuente="cobas"):
    return Candidate(
        fuente=fuente,
        fondo_slug=slug,
        fondo_nombre=slug,
        url_pagina="http://p",
        url_pdf=url,
        clase_etiqueta=clase,
    )


class TestDownload:
    def test_stores_pdf_with_coherent_key_and_manifest(self, tmp_path):
        store = LocalStore(tmp_path)
        client = StubClient({"http://x/a.pdf": PDF_A})
        entries = pipeline.download(
            client, store, [cand("cobas_iberia", "http://x/a.pdf", "Clase A")]
        )
        e = entries[0]
        assert e.estado == "ok" and len(e.sha256) == 64
        assert e.s3_key == f"kids/cobas/cobas_iberia/clase-a/{e.sha256}.pdf"
        assert store.get(e.s3_key) == PDF_A
        pipeline.write_manifest(store, entries, "20261122T120000Z")
        assert [m.sha256 for m in read_manifests(store)] == [e.sha256]

    def test_rerun_does_not_duplicate_documents(self, tmp_path):
        store = LocalStore(tmp_path)
        client = StubClient({"http://x/a.pdf": PDF_A})
        for stamp in ("20261122T120000Z", "20261123T120000Z"):
            pipeline.write_manifest(
                store, pipeline.download(client, store, [cand("f", "http://x/a.pdf")]), stamp
            )
        assert len([k for k in store.list_keys("kids/")]) == 1
        assert len(read_manifests(store)) == 1  # dos manifiestos, un solo documento

    def test_non_pdf_and_errors_do_not_stop_the_batch(self, tmp_path):
        store = LocalStore(tmp_path)
        client = StubClient(
            {
                "http://x/html": b"<html>no soy pdf</html>",
                "http://x/boom": RuntimeError("fallo de red"),
                "http://x/ok": PDF_B,
            }
        )
        entries = pipeline.download(
            client,
            store,
            [cand("a", "http://x/html"), cand("b", "http://x/boom"), cand("c", "http://x/ok")],
        )
        assert [e.estado for e in entries] == ["error", "error", "ok"]
        assert "no es un PDF" in entries[0].error

    def test_robots_block_is_recorded(self, tmp_path):
        store = LocalStore(tmp_path)
        client = StubClient({"http://x/a.pdf": RobotsDisallowed("http://x/a.pdf")})
        (entry,) = pipeline.download(client, store, [cand("a", "http://x/a.pdf")])
        assert entry.estado == "omitido_robots"

    def test_manifest_is_jsonl_with_traceability_fields(self, tmp_path):
        store = LocalStore(tmp_path)
        entries = pipeline.download(
            StubClient({"http://x/a.pdf": PDF_A}), store, [cand("a", "http://x/a.pdf")]
        )
        key = pipeline.write_manifest(store, entries, "20261122T120000Z")
        row = json.loads(store.get(key).decode().splitlines()[0])
        assert {"url_pdf", "url_pagina", "descargado_en", "sha256", "fuente"} <= row.keys()
