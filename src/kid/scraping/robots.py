"""Lector de robots.txt con la semantica de RFC 9309 (comodines ``*`` y ``$``, la regla mas larga gana).

``urllib.robotparser`` de la libreria estandar NO entiende ``*`` ni ``$``; reglas como
``Disallow: /*.pdf$`` (la usa Bestinver) pasarian desapercibidas. Por eso se implementa aqui.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class _Rule:
    allow: bool
    pattern: str
    regex: re.Pattern[str]


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.compile(regex + (r"\Z" if anchored else ""))


class RobotsRules:
    """Reglas aplicables a un user-agent concreto dentro de un robots.txt."""

    def __init__(self, text: str, user_agent: str):
        product = user_agent.split("/")[0].strip().lower()
        groups: list[tuple[list[str], list[_Rule]]] = []
        agents: list[str] = []
        rules: list[_Rule] = []
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            name, value = (part.strip() for part in line.split(":", 1))
            name = name.lower()
            if name == "user-agent":
                if in_rules:
                    groups.append((agents, rules))
                    agents, rules, in_rules = [], [], False
                agents.append(value.lower())
            elif name in {"allow", "disallow"}:
                in_rules = True
                if value:  # "Disallow:" vacio = todo permitido
                    rules.append(_Rule(name == "allow", value, _compile(value)))
        if agents:
            groups.append((agents, rules))

        specific = [r for ags, rs in groups for a in ags if a != "*" and a in product for r in rs]
        generic = [r for ags, rs in groups if "*" in ags for r in rs]
        self._rules = specific or generic

    def allowed(self, path_and_query: str) -> bool:
        best: _Rule | None = None
        for rule in self._rules:
            if rule.regex.match(path_and_query) and (
                best is None
                or len(rule.pattern) > len(best.pattern)
                or (len(rule.pattern) == len(best.pattern) and rule.allow)
            ):
                best = rule
        return True if best is None else best.allow


ALLOW_ALL = RobotsRules("", "*")
DISALLOW_ALL = RobotsRules("User-agent: *\nDisallow: /", "*")
