"""Utilidades comunes a las fuentes (gestoras)."""

from __future__ import annotations

import re
import unicodedata
from typing import Protocol
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from requests.utils import requote_uri

from ...manifest import Candidate
from ..http import PoliteClient


class Source(Protocol):
    name: str

    def discover(self, client: PoliteClient) -> list[Candidate]:
        """Recorre la web de la gestora y devuelve los KID que encuentra (sin descargarlos)."""
        ...


def make_soup(content: bytes) -> BeautifulSoup:
    return BeautifulSoup(content, "html.parser")


def norm(text: str | None) -> str:
    """Minusculas, sin tildes y con espacios colapsados: para comparar textos de enlaces."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().casefold()


def clean_url(url: str) -> str:
    """Codifica espacios y otros caracteres (Bestinver publica enlaces con un espacio dentro)."""
    return requote_uri(url.strip())


def last_segment(url: str) -> str:
    return [p for p in urlsplit(url).path.split("/") if p][-1]


def dedupe(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    out: list[Candidate] = []
    for cand in candidates:
        if cand.url_pdf not in seen:
            seen.add(cand.url_pdf)
            out.append(cand)
    return out
