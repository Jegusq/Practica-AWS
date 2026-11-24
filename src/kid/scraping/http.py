"""Cliente HTTP "educado": se identifica, respeta robots.txt, limita el ritmo y reintenta con espera."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from urllib.parse import urlsplit

import requests

from .. import __version__
from .robots import ALLOW_ALL, DISALLOW_ALL, RobotsRules

log = logging.getLogger("kid.scraping")

RETRY_STATUS = {429, 500, 502, 503, 504}


class RobotsDisallowed(Exception):
    """robots.txt del sitio no permite descargar esta URL."""


class PoliteClient:
    def __init__(
        self,
        contact: str = "sin-contacto-configurado",
        *,
        delay: float = 2.0,
        timeout: float = 30.0,
        max_retries: int = 3,
        respect_robots: bool = True,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.user_agent = (
            f"KIDPipelineBot/{__version__} (proyecto academico MIAX; contacto: {contact})"
        )
        self.delay = delay
        self.timeout = timeout
        self.max_retries = max_retries
        self.respect_robots = respect_robots
        self._session = session or requests.Session()
        self._session.headers.update(
            {"User-Agent": self.user_agent, "Accept-Language": "es-ES,es;q=0.9"}
        )
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotsRules] = {}

    # -- ritmo ---------------------------------------------------------------------------------------
    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.delay - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._clock()

    # -- robots.txt ----------------------------------------------------------------------------------
    def _rules_for(self, url: str) -> RobotsRules:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        robots_url = f"{origin}/robots.txt"
        try:
            self._throttle(parts.netloc)
            resp = self._session.get(robots_url, timeout=self.timeout)
        except requests.RequestException as exc:
            log.warning("robots.txt de %s inaccesible (%s): se asume prohibido", origin, exc)
            rules = DISALLOW_ALL
        else:
            if resp.status_code == 200:
                rules = RobotsRules(resp.text, self.user_agent)
            elif 400 <= resp.status_code < 500:
                # RFC 9309 §2.3.1.3: robots.txt "no disponible" (4xx) => sin restricciones.
                log.warning(
                    "robots.txt de %s responde %s: se tratan como sin restricciones",
                    origin,
                    resp.status_code,
                )
                rules = ALLOW_ALL
            else:
                log.warning(
                    "robots.txt de %s responde %s: se asume prohibido", origin, resp.status_code
                )
                rules = DISALLOW_ALL
        self._robots[origin] = rules
        return rules

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        return self._rules_for(url).allowed(target)

    # -- peticiones ----------------------------------------------------------------------------------
    def get(self, url: str) -> requests.Response:
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        host = urlsplit(url).netloc
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle(host)
            try:
                resp = self._session.get(url, timeout=self.timeout, allow_redirects=True)
            except requests.RequestException as exc:
                last_error = exc
                resp = None
            if resp is not None and resp.status_code not in RETRY_STATUS:
                return resp
            if attempt == self.max_retries:
                break
            retry_after = resp.headers.get("Retry-After") if resp is not None else None
            wait = (
                float(retry_after) if retry_after and retry_after.isdigit() else 2.0 * (2**attempt)
            )
            log.info("reintento %s/%s de %s en %.0fs", attempt + 1, self.max_retries, url, wait)
            self._sleep(min(wait, 60.0))
        if last_error is not None:
            raise last_error
        resp.raise_for_status()  # type: ignore[union-attr]
        return resp  # type: ignore[return-value]
