"""Cobas AM (dificultad media-alta): ficha de cada fondo con un bloque por clase (A, B, C, D).

Cada bloque es un ``<article class="download-docs">`` con un titulo («Documentacion fondo - Cobas
Internacional FI Clase A») y varios enlaces; el del KID se llama «PRIIP». Los PDFs estan en un bucket
publico de S3 de Cobas (``cobas-static-content``), no bajo cobasam.com.

Las fichas no se enlazan desde ``/productos/`` (la lista se pinta con JavaScript), pero el sitemap de
Yoast las lista: se usa ``product-sitemap.xml`` y se filtra la ruta ``/productos/inversion-libre/``.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from ...manifest import Candidate
from ..http import PoliteClient, RobotsDisallowed
from .base import clean_url, dedupe, last_segment, make_soup, norm

log = logging.getLogger("kid.scraping.cobas")

NAME = "cobas"
SITEMAP_URL = "https://www.cobasam.com/product-sitemap.xml"
_FUND_URL = re.compile(r"https://www\.cobasam\.com/productos/inversion-libre/[a-z0-9_-]+/?")
_CLASS = re.compile(r"\bClase\s+([A-Z])\s*$", flags=re.I)
_PREFIX = re.compile(r"^\s*Documentaci[oó]n\s+fondo\s*[-–]\s*", flags=re.I)


def parse_sitemap(content: bytes) -> list[str]:
    urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", content.decode("utf-8", errors="replace"))
    return sorted({u if u.endswith("/") else u + "/" for u in urls if _FUND_URL.fullmatch(u)})


def parse_fund_page(content: bytes, page_url: str) -> list[tuple[str, str | None, str]]:
    """Devuelve [(nombre_fondo, clase | None, url_pdf_priip)] de todos los bloques de la ficha."""
    out: list[tuple[str, str | None, str]] = []
    for article in make_soup(content).select("article.download-docs"):
        heading = article.find(["h2", "h3", "h4"])
        title = _PREFIX.sub("", heading.get_text(" ", strip=True)) if heading else ""
        m = _CLASS.search(title)
        clase = f"Clase {m.group(1).upper()}" if m else None
        fondo = _CLASS.sub("", title).strip() or last_segment(page_url)
        for a in article.find_all("a", href=True):
            if norm(a.get_text()).startswith("priip"):
                out.append((fondo, clase, clean_url(urljoin(page_url, a["href"]))))
                break
    return out


class Cobas:
    name = NAME

    def discover(self, client: PoliteClient) -> list[Candidate]:
        sitemap = client.get(SITEMAP_URL)
        sitemap.raise_for_status()
        out: list[Candidate] = []
        for url in parse_sitemap(sitemap.content):
            try:
                page = client.get(url)
            except RobotsDisallowed:
                log.warning("robots.txt no permite %s", url)
                continue
            if page.status_code != 200:
                log.warning("%s -> HTTP %s", url, page.status_code)
                continue
            slug = last_segment(url)
            blocks = parse_fund_page(page.content, url)
            if not blocks:
                log.info("%s: sin enlaces PRIIP (se omite)", slug)
            for fondo, clase, pdf in blocks:
                out.append(
                    Candidate(
                        fuente=NAME,
                        fondo_slug=slug,
                        fondo_nombre=fondo,
                        url_pagina=url,
                        url_pdf=pdf,
                        clase_etiqueta=clase,
                    )
                )
        return dedupe(out)
