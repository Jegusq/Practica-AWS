"""Bestinver (dificultad baja): cada fondo tiene su ficha con un enlace «Datos fundamentales» a un PDF.

Nota sobre robots.txt: Bestinver declara ``Disallow: /*.pdf$``. Los enlaces que publica la propia ficha
llevan un parametro ``?h=...`` (cache-buster), por lo que NO terminan en ``.pdf`` y la regla literal no
los alcanza. Se respeta exactamente lo que dice el fichero (ver README, apartado de scraping responsable);
si se quiere ser mas conservador basta con no incluir esta fuente (``--sources cobas azvalor``).
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from ...manifest import Candidate
from ..http import PoliteClient, RobotsDisallowed
from .base import clean_url, dedupe, make_soup, norm

log = logging.getLogger("kid.scraping.bestinver")

NAME = "bestinver"
INDEX_URL = "https://www.bestinver.es/fondos-de-inversion/"
_FUND_PATH = re.compile(r"^/fondos-de-inversion/([a-z0-9-]+)/?$")
# Paginas informativas que cuelgan de la misma ruta pero no son fondos.
_NOT_FUNDS = {
    "como-contratar-un-fondo-de-inversion",
    "como-funcionan-y-que-ventajas-tienen",
    "por-que-un-fondo-de-inversion",
}


def parse_index(content: bytes, base_url: str = INDEX_URL) -> list[tuple[str, str]]:
    """Devuelve [(slug, url_ficha)] de los fondos listados en el indice."""
    found: dict[str, str] = {}
    for a in make_soup(content).find_all("a", href=True):
        url = urljoin(base_url, a["href"])
        if not url.startswith("https://www.bestinver.es"):
            continue
        path = url.split("bestinver.es", 1)[1]
        m = _FUND_PATH.match(path)
        if m and m.group(1) not in _NOT_FUNDS:
            found[m.group(1)] = url if url.endswith("/") else url + "/"
    return sorted(found.items())


def parse_fund_page(content: bytes, page_url: str) -> tuple[str, str | None]:
    """Devuelve (nombre_del_fondo, url_del_pdf_de_datos_fundamentales | None)."""
    soup = make_soup(content)
    title = soup.title.get_text(strip=True) if soup.title else ""
    name = re.sub(r"\s*[-|–]\s*BESTINVER\s*$", "", title, flags=re.I).strip() or page_url

    for a in soup.find_all("a", href=True):
        label = norm(a.get("title")) or norm(a.get_text())
        if "datos fundamentales" in label or "datos_fundamentales" in a["href"]:
            return name, clean_url(urljoin(page_url, a["href"]))
    return name, None


class Bestinver:
    name = NAME

    def discover(self, client: PoliteClient) -> list[Candidate]:
        index = client.get(INDEX_URL)
        index.raise_for_status()
        out: list[Candidate] = []
        for slug, url in parse_index(index.content, INDEX_URL):
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
                log.info("%s: sin enlace de datos fundamentales (se omite)", slug)
                continue
            out.append(
                Candidate(
                    fuente=NAME,
                    fondo_slug=slug,
                    fondo_nombre=fondo,
                    url_pagina=url,
                    url_pdf=pdf,
                    clase_etiqueta=None,  # una clase por ficha
                )
            )
        return dedupe(out)
