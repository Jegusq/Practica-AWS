-- Modelo de datos del pipeline de KID (PostgreSQL / Aurora PostgreSQL).
-- Es idempotente (IF NOT EXISTS / OR REPLACE): se puede ejecutar en cada despliegue.
-- Regla de este fichero: una sentencia por ';' y sin ';' dentro de cadenas (lo trocea migrate.py).

-- Un registro por PDF descargado: trazabilidad de donde viene y cuando se bajo.
-- sha256 UNIQUE evita duplicar un documento aunque se procese dos veces.
CREATE TABLE IF NOT EXISTS documentos (
    id              BIGSERIAL PRIMARY KEY,
    sha256          CHAR(64)    NOT NULL UNIQUE,
    fuente          TEXT        NOT NULL,
    fondo_slug      TEXT        NOT NULL,
    fondo_nombre    TEXT,
    clase_etiqueta  TEXT,
    url_pagina      TEXT,
    url_pdf         TEXT        NOT NULL,
    s3_bucket       TEXT        NOT NULL,
    s3_key          TEXT        NOT NULL,
    tamano_bytes    INTEGER,
    descargado_en   TIMESTAMPTZ NOT NULL,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Datos extraidos con IA: exactamente un registro por documento (UNIQUE documento_id).
CREATE TABLE IF NOT EXISTS kids (
    id                          BIGSERIAL PRIMARY KEY,
    documento_id                BIGINT      NOT NULL UNIQUE REFERENCES documentos (id) ON DELETE CASCADE,
    isin                        CHAR(12),
    nombre_producto             TEXT,
    nombre_clase                TEXT,
    gestora                     TEXT,
    gestora_clave               TEXT,
    fecha_documento             DATE,
    sri                         SMALLINT CHECK (sri BETWEEN 1 AND 7),
    periodo_mantenimiento_anios NUMERIC(5, 2),
    gastos_corrientes_pct       NUMERIC(6, 3),
    costes_entrada_pct          NUMERIC(6, 3),
    costes_salida_pct           NUMERIC(6, 3),
    comision_exito_pct          NUMERIC(6, 3),
    divisa                      CHAR(3),
    objetivo_inversion          TEXT,
    modelo_id                   TEXT        NOT NULL,
    extraido_en                 TIMESTAMPTZ NOT NULL,
    avisos                      JSONB       NOT NULL DEFAULT '[]',
    extraccion                  JSONB       NOT NULL,
    creado_en                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS kids_isin_idx ON kids (isin);

CREATE INDEX IF NOT EXISTS kids_gestora_clave_idx ON kids (gestora_clave);

CREATE INDEX IF NOT EXISTS kids_sri_idx ON kids (sri);

CREATE TABLE IF NOT EXISTS escenarios (
    id                      BIGSERIAL PRIMARY KEY,
    kid_id                  BIGINT NOT NULL REFERENCES kids (id) ON DELETE CASCADE,
    escenario               TEXT   NOT NULL,
    periodo                 TEXT   NOT NULL,
    valor_final_eur         NUMERIC(14, 2),
    rentabilidad_anual_pct  NUMERIC(7, 2)
);

CREATE INDEX IF NOT EXISTS escenarios_kid_idx ON escenarios (kid_id);

CREATE TABLE IF NOT EXISTS distribucion_activos (
    id          BIGSERIAL PRIMARY KEY,
    kid_id      BIGINT        NOT NULL REFERENCES kids (id) ON DELETE CASCADE,
    categoria   TEXT          NOT NULL,
    porcentaje  NUMERIC(6, 2) NOT NULL
);

CREATE INDEX IF NOT EXISTS distribucion_kid_idx ON distribucion_activos (kid_id);

-- Version vigente de cada ISIN: la de fecha mas reciente (y, a igualdad, la extraida mas tarde).
CREATE OR REPLACE VIEW kids_vigentes AS
SELECT DISTINCT ON (COALESCE(k.isin, 'doc-' || CAST(k.id AS TEXT))) k.*
FROM kids k
ORDER BY COALESCE(k.isin, 'doc-' || CAST(k.id AS TEXT)), k.fecha_documento DESC NULLS LAST, k.extraido_en DESC;
