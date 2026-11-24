"""Modelos de respuesta de la API (aparecen en la documentacion OpenAPI en /docs)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class KidResumen(BaseModel):
    id: int
    isin: str | None = Field(None, description="ISIN de la clase (12 caracteres)")
    nombre_producto: str | None = None
    nombre_clase: str | None = None
    gestora: str | None = Field(None, description="Gestora tal y como aparece en el KID")
    gestora_clave: str | None = Field(None, description="Nombre normalizado para agrupar gestoras")
    fecha_documento: date | None = None
    sri: int | None = Field(None, description="Indicador resumido de riesgo, de 1 a 7")
    periodo_mantenimiento_anios: float | None = None
    gastos_corrientes_pct: float | None = Field(None, description="% anual")
    costes_entrada_pct: float | None = None
    costes_salida_pct: float | None = None
    comision_exito_pct: float | None = None
    divisa: str | None = None
    fuente: str = Field(description="Web de la gestora de donde se obtuvo el PDF")
    fondo_slug: str


class PaginaKids(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[KidResumen]


class Escenario(BaseModel):
    escenario: str
    periodo: str
    valor_final_eur: float | None = None
    rentabilidad_anual_pct: float | None = None


class DistribucionActivo(BaseModel):
    categoria: str
    porcentaje: float


class KidDetalle(KidResumen):
    objetivo_inversion: str | None = None
    modelo_id: str | None = Field(None, description="Modelo de Bedrock que hizo la extraccion")
    extraido_en: datetime | None = None
    avisos: list[str] = Field(
        default_factory=list, description="Incidencias de validacion de la extraccion"
    )
    fondo_nombre: str | None = None
    clase_etiqueta: str | None = None
    url_pagina: str | None = Field(None, description="Ficha de la gestora donde se encontro el KID")
    url_pdf: str | None = Field(None, description="URL original del PDF")
    sha256: str | None = None
    tamano_bytes: int | None = None
    descargado_en: datetime | None = None
    escenarios: list[Escenario] = Field(default_factory=list)
    distribucion_activos: list[DistribucionActivo] = Field(default_factory=list)


class EnlacePdf(BaseModel):
    url: str = Field(description="URL firmada y temporal del PDF almacenado en S3")
    caduca_en_s: int


class Gestora(BaseModel):
    clave: str
    nombre: str | None = None
    n_kids: int
    gastos_medio_pct: float | None = None
    sri_medio: float | None = None


class ConteoSri(BaseModel):
    sri: int
    n: int


class ConteoFuente(BaseModel):
    fuente: str
    n: int


class Estadisticas(BaseModel):
    documentos: int
    kids: int
    kids_vigentes: int
    gestoras: int
    gastos_medio_pct: float | None = None
    sri_medio: float | None = None
    ultima_descarga: datetime | None = None
    por_sri: list[ConteoSri]
    por_fuente: list[ConteoFuente]
    por_gestora: list[Gestora]


class Documento(BaseModel):
    id: int
    fuente: str
    fondo_slug: str
    fondo_nombre: str | None = None
    clase_etiqueta: str | None = None
    url_pagina: str | None = None
    url_pdf: str
    sha256: str
    tamano_bytes: int | None = None
    descargado_en: datetime
    kid_id: int | None = None
    isin: str | None = None
