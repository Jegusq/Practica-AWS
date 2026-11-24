"""API HTTP de solo lectura sobre los KID extraidos (FastAPI; en AWS corre en Lambda con Function URL)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__
from ..config import Settings
from ..db import connect
from ..db.repository import SORTS, KidRepository
from . import schemas

log = logging.getLogger("kid.api")

PDF_URL_TTL_S = 300

RepoFactory = Callable[[], KidRepository]
Presigner = Callable[[str, str], str]


def _default_repo_factory() -> RepoFactory:
    cache: dict[str, KidRepository] = {}

    def factory() -> KidRepository:
        # Se crea en la primera peticion y se reutiliza mientras viva el contenedor de la Lambda.
        if "repo" not in cache:
            cache["repo"] = KidRepository(connect(Settings.from_env()))
        return cache["repo"]

    return factory


def _default_presigner() -> Presigner:
    def presign(bucket: str, key: str) -> str:
        import boto3
        from botocore.config import Config

        s3 = boto3.client(
            "s3",
            region_name=Settings.from_env().region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
        )
        return s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "ResponseContentType": "application/pdf",
                "ResponseContentDisposition": "inline",
            },
            ExpiresIn=PDF_URL_TTL_S,
        )

    return presign


def get_repo(request: Request) -> KidRepository:
    return request.app.state.repo_factory()


# A nivel de modulo: FastAPI resuelve las anotaciones (cadenas, por `from __future__`) en este espacio.
Repo = Annotated[KidRepository, Depends(get_repo)]


def create_app(
    repo_factory: RepoFactory | None = None, presigner: Presigner | None = None
) -> FastAPI:
    app = FastAPI(
        title="API de KID de fondos",
        version=__version__,
        description=(
            "Datos de Documentos de Datos Fundamentales (KID) de fondos de inversion, extraidos con IA "
            "(Amazon Bedrock) a partir de los PDFs publicados por las gestoras. Solo lectura."
        ),
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"]
    )
    app.state.repo_factory = repo_factory or _default_repo_factory()
    app.state.presigner = presigner or _default_presigner()

    @app.middleware("http")
    async def database_unavailable(request: Request, call_next):
        # Aurora Serverless v2 puede estar reanudandose tras la auto-pausa: se avisa con un 503 claro
        # (sin re-lanzar la excepcion; el detalle queda en el log).
        try:
            return await call_next(request)
        except Exception:
            log.exception("error no controlado en %s", request.url.path)
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Base de datos no disponible por ahora (puede estar reanudandose). Reintenta."
                },
            )

    # ---- salud -------------------------------------------------------------------------------------
    @app.get("/health", tags=["salud"], summary="La API responde (no toca la base de datos)")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get(
        "/health/db",
        tags=["salud"],
        summary="Comprueba la base de datos (la despierta si estaba en pausa)",
    )
    def health_db(db: Repo) -> dict[str, str]:
        db.ping()
        return {"status": "ok"}

    # ---- KIDs --------------------------------------------------------------------------------------
    @app.get("/kids", response_model=schemas.PaginaKids, tags=["kids"], summary="Buscar KIDs")
    def list_kids(
        db: Repo,
        gestora: Annotated[str | None, Query(description="Texto contenido en la gestora")] = None,
        fuente: Annotated[str | None, Query(description="bestinver | azvalor | cobas")] = None,
        q: Annotated[
            str | None, Query(description="Busca en nombre, clase, gestora e ISIN")
        ] = None,
        isin: str | None = None,
        sri_min: Annotated[int | None, Query(ge=1, le=7)] = None,
        sri_max: Annotated[int | None, Query(ge=1, le=7)] = None,
        gastos_max: Annotated[
            float | None, Query(ge=0, description="Gastos corrientes maximos (%)")
        ] = None,
        solo_vigentes: Annotated[
            bool, Query(description="Solo la version mas reciente de cada ISIN")
        ] = True,
        sort: Literal["gestora", "nombre", "isin", "sri", "gastos", "fecha", "periodo"] = "gestora",
        desc: bool = False,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> schemas.PaginaKids:
        assert sort in SORTS
        total, rows = db.list_kids(
            gestora=gestora,
            fuente=fuente,
            q=q,
            isin=isin,
            sri_min=sri_min,
            sri_max=sri_max,
            gastos_max=gastos_max,
            solo_vigentes=solo_vigentes,
            sort=sort,
            desc=desc,
            limit=limit,
            offset=offset,
        )
        return schemas.PaginaKids(
            total=total, limit=limit, offset=offset, items=[schemas.KidResumen(**r) for r in rows]
        )

    @app.get(
        "/kids/{kid_id}",
        response_model=schemas.KidDetalle,
        tags=["kids"],
        summary="Detalle de un KID",
    )
    def get_kid(kid_id: int, db: Repo) -> schemas.KidDetalle:
        kid = db.get_kid(kid_id)
        if kid is None:
            raise HTTPException(404, f"No existe el KID {kid_id}")
        return schemas.KidDetalle(**kid)

    @app.get(
        "/kids/{kid_id}/pdf",
        response_model=schemas.EnlacePdf,
        tags=["kids"],
        summary="Enlace temporal al PDF original almacenado en S3",
    )
    def get_pdf(kid_id: int, request: Request, db: Repo) -> schemas.EnlacePdf:
        location = db.pdf_location(kid_id)
        if location is None:
            raise HTTPException(404, f"No existe el KID {kid_id}")
        bucket, key = location
        if bucket == "local":
            raise HTTPException(404, "El PDF no esta en S3 (datos cargados en modo local)")
        return schemas.EnlacePdf(
            url=request.app.state.presigner(bucket, key), caduca_en_s=PDF_URL_TTL_S
        )

    @app.get(
        "/isin/{isin}",
        response_model=list[schemas.KidResumen],
        tags=["kids"],
        summary="Todas las versiones de un ISIN (la mas reciente primero)",
    )
    def by_isin(isin: str, db: Repo) -> list[schemas.KidResumen]:
        rows = db.history(isin)
        if not rows:
            raise HTTPException(404, f"No hay KIDs para el ISIN {isin.upper()}")
        return [schemas.KidResumen(**r) for r in rows]

    # ---- agregados ---------------------------------------------------------------------------------
    @app.get(
        "/gestoras",
        response_model=list[schemas.Gestora],
        tags=["agregados"],
        summary="Gestoras y medias",
    )
    def gestoras(db: Repo) -> list[schemas.Gestora]:
        return [schemas.Gestora(**r) for r in db.gestoras()]

    @app.get(
        "/stats", response_model=schemas.Estadisticas, tags=["agregados"], summary="Resumen global"
    )
    def stats(db: Repo) -> schemas.Estadisticas:
        return schemas.Estadisticas(**db.stats())

    @app.get(
        "/documentos",
        response_model=list[schemas.Documento],
        tags=["trazabilidad"],
        summary="PDFs descargados: de donde vienen y cuando se bajaron",
    )
    def documentos(
        db: Repo,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[schemas.Documento]:
        return [schemas.Documento(**r) for r in db.documentos(limit, offset)]

    return app
