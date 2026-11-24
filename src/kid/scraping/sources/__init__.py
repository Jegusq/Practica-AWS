from __future__ import annotations

from .azvalor import Azvalor
from .base import Source
from .bestinver import Bestinver
from .cobas import Cobas

ALL_SOURCES: dict[str, type[Source]] = {
    "bestinver": Bestinver,
    "azvalor": Azvalor,
    "cobas": Cobas,
}

__all__ = ["ALL_SOURCES", "Azvalor", "Bestinver", "Cobas", "Source"]
