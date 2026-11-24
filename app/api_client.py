"""Cliente de la API para la app Streamlit (la app NO toca la base de datos: todo pasa por la API)."""

from __future__ import annotations

import os
import time
from typing import Any

import requests

# Aurora Serverless v2 puede tardar ~15 s en despertar tras la auto-pausa: timeout holgado + reintentos.
TIMEOUT_S = 90
RETRIES = 3


class ApiError(RuntimeError):
    pass


class KidApi:
    def __init__(self, base_url: str | None = None, session: requests.Session | None = None):
        self.base_url = (base_url or os.environ.get("API_URL", "")).rstrip("/")
        if not self.base_url:
            raise ApiError("Falta la variable de entorno API_URL (URL de la API).")
        self._session = session or requests.Session()

    def get(self, path: str, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v not in (None, "", [])}
        last: str = ""
        for attempt in range(RETRIES):
            try:
                resp = self._session.get(f"{self.base_url}{path}", params=params, timeout=TIMEOUT_S)
            except requests.RequestException as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code == 503:  # base de datos reanudandose
                    last = "la base de datos se esta despertando"
                elif resp.status_code >= 400:
                    raise ApiError(f"{resp.status_code} en {path}: {resp.text[:200]}")
                else:
                    return resp.json()
            if attempt < RETRIES - 1:
                time.sleep(5)
        raise ApiError(f"No se pudo consultar {path}: {last}")

    # -- endpoints ----------------------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        return self.get("/stats")

    def gestoras(self) -> list[dict[str, Any]]:
        return self.get("/gestoras")

    def kids(self, **params: Any) -> dict[str, Any]:
        return self.get("/kids", **params)

    def kid(self, kid_id: int) -> dict[str, Any]:
        return self.get(f"/kids/{kid_id}")

    def pdf_link(self, kid_id: int) -> str | None:
        try:
            return self.get(f"/kids/{kid_id}/pdf")["url"]
        except ApiError:
            return None

    def history(self, isin: str) -> list[dict[str, Any]]:
        return self.get(f"/isin/{isin}")

    def documentos(self, limit: int = 500) -> list[dict[str, Any]]:
        return self.get("/documentos", limit=limit)
