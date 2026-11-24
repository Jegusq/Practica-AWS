"""Azvalor (dificultad media): ficha de cada fondo con un enlace «Folleto simplificado (DFP)».

El nombre del PDF incluye la fecha (p. ej. ``DFP-Iberia-09.01.2026.pdf``), asi que la URL no se puede
deducir: hay que leerla del HTML de cada ficha. ``/fondos/`` redirige a ``/fondos-de-inversion/``.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlsplit

from ...manifest import Candidate
from ..http import PoliteClient, RobotsDisallowed
from .base import clean_url, dedupe, last_segment, make_soup, norm

log = logging.getLogger("kid.scraping.azvalor")

NAME = "azvalor"
INDEX_URL = "https://www.azvalor.com/fondos/"
_FUND_PATH = re.compile(r"^/fondos-de-inversion/([a-z0-9-]+)/?$")


def parse_index(content: bytes, base_url: str) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    for a in make_soup(content).find_all("a", href=True):
        url = urljoin(base_url, a["href"])
        parts = urlsplit(url)
        if parts.netloc.removeprefix("www.") != "azvalor.com":
            continue
        m = _FUND_PATH.match(parts.path)
        if m:
            found[m.group(1)] = f"https://www.azvalor.com{parts.path.rstrip('/')}/"
    return sorted(found.items())


def parse_fund_page(content: bytes, page_url: str) -> tuple[str, str | None]:
    soup = make_soup(content)
    h1 = soup.find("h1")
    name = h1.get_text(" ", strip=True) if h1 else last_segment(page_url)
    for a in soup.find_all("a", href=True):
        label = norm(a.get_text())
        if "folleto simplificado" in label or "(dfp)" in label:
            return name, clean_url(urljoin(page_url, a["href"]))
    # Plan B: el nombre del PDF contiene DFP.
    for a in soup.find_all("a", href=True):
        if re.search(r"/DFP[-_]", a["href"], flags=re.I) and a["href"].lower().endswith(".pdf"):
            return name, clean_url(urljoin(page_url, a["href"]))
    return name, None


class Azvalor:
    name = NAME

    def discover(self, client: PoliteClient) -> list[Candidate]:
        index = client.get(INDEX_URL)  # sigue la redireccion a /fondos-de-inversion/
        index.raise_for_status()
        out: list[Candidate] = []
        for slug, url in parse_index(index.content, index.url):
            try:
                page = client.get(url)
            except RobotsDisallowed:
                log.warning("robots.txt no permite %s", url)
                continue
            if page.status_code != 200:
                log.warning("%s -> HTTP %s", url, page.status_code)
                continue
            fondo, pdf = parse_fund_page(page.content, url)
            if not pdf:
                log.info("%s: sin enlace DFP (se omite)", slug)
                continue
            out.append(
                Candidate(
                    fuente=NAME,
                    fondo_slug=slug,
                    fondo_nombre=fondo,
                    url_pagina=url,
                    url_pdf=pdf,
                    clase_etiqueta=None,
                )
            )
        return dedupe(out)
