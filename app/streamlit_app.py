"""Cuadro de mando de KID de fondos (Streamlit). Consume la API HTTP; no accede a la base de datos."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import altair as alt
import pandas as pd
import streamlit as st

from api_client import ApiError, KidApi

st.set_page_config(page_title="KID de fondos", page_icon="📊", layout="wide")

ESCENARIOS_ORDEN = ["tension", "desfavorable", "moderado", "favorable"]
SRI_COLORES = {1: "🟢", 2: "🟢", 3: "🟡", 4: "🟡", 5: "🟠", 6: "🔴", 7: "🔴"}


# ------------------------------------------------------------------------------------------ datos
@st.cache_resource
def get_api() -> KidApi:
    return KidApi()


@st.cache_data(ttl=300, show_spinner=False)
def _cached_get(path: str, params: tuple[tuple[str, Any], ...]) -> Any:
    return get_api().get(path, **dict(params))


def fetch(path: str, **params: Any) -> Any:
    with st.spinner(
        "Consultando la API… (si la base de datos estaba en pausa, puede tardar ~15 s)"
    ):
        return _cached_get(path, tuple(sorted(params.items())))


def fmt(value: Any, suffix: str = "", digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    return f"{value:.{digits}f}{suffix}" if isinstance(value, float | int) else f"{value}{suffix}"


def kids_frame(items: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(items)
    if df.empty:
        return df
    df["fecha_documento"] = pd.to_datetime(df["fecha_documento"], errors="coerce")
    df["etiqueta"] = (
        df["nombre_producto"].fillna("(sin nombre)")
        + df["nombre_clase"].map(lambda c: f" · {c}" if pd.notna(c) and c else "")
        + df["isin"].map(lambda i: f" [{i}]" if pd.notna(i) and i else "")
    )
    return df


# ------------------------------------------------------------------------------------------ páginas
def page_resumen() -> None:
    st.header("Resumen")
    stats = fetch("/stats")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Documentos descargados", stats["documentos"])
    c2.metric("KID vigentes", stats["kids_vigentes"], help="Última versión de cada ISIN")
    c3.metric("Gestoras", stats["gestoras"])
    c4.metric("Gastos corrientes medios", fmt(stats["gastos_medio_pct"], " %"))
    c5.metric("SRI medio", fmt(stats["sri_medio"]))
    if stats.get("ultima_descarga"):
        st.caption(f"Última descarga de PDFs: {stats['ultima_descarga']}")
    if not stats["kids_vigentes"]:
        st.info("Todavía no hay datos cargados. Ejecuta el pipeline (scrape → extract → load).")
        return

    left, right = st.columns(2)
    por_gestora = pd.DataFrame(stats["por_gestora"])
    with left:
        st.subheader("KID por gestora")
        st.altair_chart(
            alt.Chart(por_gestora)
            .mark_bar()
            .encode(
                y=alt.Y("nombre:N", sort="-x", title=None, axis=alt.Axis(labelLimit=300)),
                x=alt.X("n_kids:Q", title="KID vigentes"),
                tooltip=["nombre", "n_kids"],
            ),
            width="stretch",
        )
    with right:
        st.subheader("Gastos corrientes medios por gestora")
        st.altair_chart(
            alt.Chart(por_gestora.dropna(subset=["gastos_medio_pct"]))
            .mark_bar(color="#e07a5f")
            .encode(
                y=alt.Y("nombre:N", sort="-x", title=None, axis=alt.Axis(labelLimit=300)),
                x=alt.X("gastos_medio_pct:Q", title="% anual"),
                tooltip=["nombre", "gastos_medio_pct", "sri_medio"],
            ),
            width="stretch",
        )

    left, right = st.columns(2)
    sri = (
        pd.DataFrame({"sri": range(1, 8)})
        .merge(pd.DataFrame(stats["por_sri"]), how="left", on="sri")
        .fillna(0)
    )
    with left:
        st.subheader("Distribución por indicador de riesgo (SRI)")
        st.altair_chart(
            alt.Chart(sri)
            .mark_bar(color="#3d405b")
            .encode(
                x=alt.X("sri:O", title="SRI (1 = menor riesgo, 7 = mayor)"),
                y=alt.Y("n:Q", title="KID"),
                tooltip=["sri", "n"],
            ),
            width="stretch",
        )
    with right:
        st.subheader("Coste frente a riesgo")
        df = kids_frame(fetch("/kids", limit=200, sort="gastos")["items"]).dropna(
            subset=["sri", "gastos_corrientes_pct"]
        )
        if df.empty:
            st.caption("Sin datos suficientes.")
        else:
            st.altair_chart(
                alt.Chart(df)
                .mark_circle(size=110, opacity=0.75)
                .encode(
                    x=alt.X("sri:O", title="SRI"),
                    y=alt.Y("gastos_corrientes_pct:Q", title="Gastos corrientes (% anual)"),
                    color=alt.Color("gestora_clave:N", title="Gestora"),
                    tooltip=[
                        "etiqueta",
                        "gestora",
                        "sri",
                        "gastos_corrientes_pct",
                        "periodo_mantenimiento_anios",
                    ],
                ),
                width="stretch",
            )


def show_detail(kid_id: int) -> None:
    kid = fetch(f"/kids/{kid_id}")
    st.divider()
    clase = f" · {kid['nombre_clase']}" if kid.get("nombre_clase") else ""
    st.subheader(f"{kid.get('nombre_producto') or kid.get('fondo_nombre')}{clase}")
    st.caption(
        f"{kid.get('gestora') or '—'} · ISIN {kid.get('isin') or '—'} · documento de {kid.get('fecha_documento') or 'fecha no indicada'}"
    )

    m = st.columns(6)
    sri = kid.get("sri")
    m[0].metric("Riesgo (SRI)", f"{SRI_COLORES.get(sri, '')} {sri} / 7" if sri else "—")
    m[1].metric("Periodo recomendado", fmt(kid.get("periodo_mantenimiento_anios"), " años", 1))
    m[2].metric("Gastos corrientes", fmt(kid.get("gastos_corrientes_pct"), " %"))
    m[3].metric("Costes de entrada", fmt(kid.get("costes_entrada_pct"), " %"))
    m[4].metric("Costes de salida", fmt(kid.get("costes_salida_pct"), " %"))
    m[5].metric("Comisión de éxito", fmt(kid.get("comision_exito_pct"), " %"))
    if kid.get("objetivo_inversion"):
        st.write(f"**Objetivo de inversión:** {kid['objetivo_inversion']}")

    left, right = st.columns(2)
    with left:
        if kid["escenarios"]:
            st.markdown("**Escenarios de rentabilidad** (valor final de 10.000 €)")
            esc = pd.DataFrame(kid["escenarios"])
            st.altair_chart(
                alt.Chart(esc)
                .mark_bar()
                .encode(
                    x=alt.X(
                        "escenario:N",
                        sort=ESCENARIOS_ORDEN,
                        title=None,
                        axis=alt.Axis(labelAngle=0),
                    ),
                    xOffset="periodo:N",
                    y=alt.Y("valor_final_eur:Q", title="€"),
                    color=alt.Color("periodo:N", title="Horizonte"),
                    tooltip=["escenario", "periodo", "valor_final_eur", "rentabilidad_anual_pct"],
                ),
                width="stretch",
            )
        else:
            st.caption("El documento no incluye escenarios (o no se pudieron extraer).")
    with right:
        if kid["distribucion_activos"]:
            st.markdown("**Distribución de activos** (solo si el KID la indica)")
            dist = pd.DataFrame(kid["distribucion_activos"])
            st.altair_chart(
                alt.Chart(dist)
                .mark_arc(innerRadius=50)
                .encode(
                    theta="porcentaje:Q",
                    color=alt.Color("categoria:N", title=None),
                    tooltip=["categoria", "porcentaje"],
                ),
                width="stretch",
            )
        else:
            st.caption("El KID no detalla la distribución de activos.")

    with st.expander("Trazabilidad del dato"):
        st.write(
            f"- **Fuente:** {kid['fuente']} — [ficha de la gestora]({kid['url_pagina']})\n"
            f"- **PDF original:** {kid['url_pdf']}\n"
            f"- **Descargado:** {kid.get('descargado_en')} · SHA-256 `{(kid.get('sha256') or '')[:16]}…`\n"
            f"- **Modelo de extracción:** `{kid.get('modelo_id')}` · extraído {kid.get('extraido_en')}"
        )
        if st.button("Obtener enlace temporal al PDF guardado en S3", key=f"pdf-{kid_id}"):
            link = get_api().pdf_link(kid_id)
            if link:
                st.link_button("Abrir PDF", link)
            else:
                st.warning("El PDF no está disponible en S3 para este registro.")
    if kid["avisos"]:
        with st.expander(f"⚠️ {len(kid['avisos'])} incidencia(s) de validación de la extracción"):
            for aviso in kid["avisos"]:
                st.write(f"- `{aviso}`")
    if kid.get("isin"):
        versiones = fetch(f"/isin/{kid['isin']}")
        if len(versiones) > 1:
            with st.expander(f"Historial de versiones de {kid['isin']} ({len(versiones)})"):
                st.dataframe(
                    pd.DataFrame(versiones)[
                        [
                            "fecha_documento",
                            "gastos_corrientes_pct",
                            "sri",
                            "periodo_mantenimiento_anios",
                        ]
                    ],
                    hide_index=True,
                    width="stretch",
                )


def page_explorador() -> None:
    st.header("Explorador de KID")
    gestoras = fetch("/gestoras")
    f1, f2, f3, f4 = st.columns([2, 2, 1, 1])
    q = f1.text_input("Buscar", placeholder="nombre, clase, gestora o ISIN")
    gestora = f2.selectbox(
        "Gestora",
        options=[None, *[g["clave"] for g in gestoras]],
        format_func=lambda c: (
            "Todas" if c is None else next(g["nombre"] for g in gestoras if g["clave"] == c)
        ),
    )
    fuente = f3.selectbox(
        "Fuente",
        options=[None, "bestinver", "azvalor", "cobas"],
        format_func=lambda x: x or "Todas",
    )
    solo_vigentes = f4.checkbox(
        "Solo vigentes",
        value=True,
        help="Si se desmarca, aparecen también versiones antiguas de un mismo ISIN",
    )
    s1, s2, s3, s4 = st.columns([2, 2, 1, 1])
    sri_min, sri_max = s1.slider("SRI", 1, 7, (1, 7))
    gastos_max = s2.slider("Gastos corrientes máximos (%)", 0.0, 5.0, 5.0, 0.05)
    sort = s3.selectbox(
        "Ordenar por", ["gestora", "nombre", "isin", "sri", "gastos", "fecha", "periodo"], index=0
    )
    desc = s4.checkbox("Descendente", value=False)

    page_size = 25
    params: dict[str, Any] = {
        "q": q or None,
        "gestora": gestora,
        "fuente": fuente,
        "sri_min": sri_min if sri_min > 1 else None,
        "sri_max": sri_max if sri_max < 7 else None,
        "gastos_max": gastos_max if gastos_max < 5.0 else None,
        "solo_vigentes": solo_vigentes,
        "sort": sort,
        "desc": desc,
    }
    first = fetch("/kids", **params, limit=page_size, offset=0)
    total = first["total"]
    pages = max(1, -(-total // page_size))
    page = st.number_input(f"Página (de {pages})", 1, pages, 1) if pages > 1 else 1
    data = (
        first
        if page == 1
        else fetch("/kids", **params, limit=page_size, offset=(page - 1) * page_size)
    )
    st.caption(f"{total} resultado(s)")
    if not data["items"]:
        st.info("Ningún KID cumple los filtros.")
        return

    df = kids_frame(data["items"])
    view = df[
        ["isin", "nombre_producto", "nombre_clase", "gestora", "sri", "gastos_corrientes_pct", "costes_entrada_pct", "costes_salida_pct", "periodo_mantenimiento_anios", "fecha_documento", "fuente"]
    ].rename(
        columns={
            "isin": "ISIN", "nombre_producto": "Fondo", "nombre_clase": "Clase", "gestora": "Gestora", "sri": "SRI",
            "gastos_corrientes_pct": "Gastos %", "costes_entrada_pct": "Entrada %", "costes_salida_pct": "Salida %",
            "periodo_mantenimiento_anios": "Periodo (años)", "fecha_documento": "Fecha", "fuente": "Fuente",
        }
    )  # fmt: skip
    view["Clase"] = view["Clase"].fillna("—")
    event = st.dataframe(
        view,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        column_config={"Fecha": st.column_config.DateColumn(format="DD/MM/YYYY")},
    )
    rows = event.selection.rows
    if rows:
        show_detail(int(df.iloc[rows[0]]["id"]))
    else:
        st.caption("Selecciona una fila para ver la ficha completa del fondo.")


def page_comparador() -> None:
    st.header("Comparador")
    df = kids_frame(fetch("/kids", limit=200, sort="nombre")["items"])
    if df.empty:
        st.info("Sin datos.")
        return
    chosen = st.multiselect(
        "Elige de 2 a 6 clases/fondos para comparar",
        options=df["id"].tolist(),
        format_func=lambda i: df.loc[df["id"] == i, "etiqueta"].iloc[0],
        max_selections=6,
    )
    if len(chosen) < 2:
        st.caption("Selecciona al menos dos.")
        return
    sel = df[df["id"].isin(chosen)]
    table = sel.set_index("etiqueta")[
        [
            "gestora",
            "sri",
            "periodo_mantenimiento_anios",
            "gastos_corrientes_pct",
            "costes_entrada_pct",
            "costes_salida_pct",
            "comision_exito_pct",
            "divisa",
        ]
    ].T
    table.index = [
        "Gestora",
        "SRI",
        "Periodo (años)",
        "Gastos corrientes %",
        "Costes entrada %",
        "Costes salida %",
        "Comisión éxito %",
        "Divisa",
    ]
    st.dataframe(table.astype(str), width="stretch")
    long = sel.melt(
        id_vars="etiqueta",
        value_vars=["gastos_corrientes_pct", "costes_entrada_pct", "costes_salida_pct"],
        var_name="concepto",
        value_name="pct",
    )
    long["concepto"] = long["concepto"].map(
        {
            "gastos_corrientes_pct": "Gastos corrientes",
            "costes_entrada_pct": "Entrada",
            "costes_salida_pct": "Salida",
        }
    )
    st.altair_chart(
        alt.Chart(long.dropna())
        .mark_bar()
        .encode(
            x=alt.X("etiqueta:N", title=None, axis=alt.Axis(labelAngle=-20, labelLimit=220)),
            y=alt.Y("pct:Q", title="%"),
            color="concepto:N",
            xOffset="concepto:N",
            tooltip=["etiqueta", "concepto", "pct"],
        ),
        width="stretch",
    )


def page_trazabilidad() -> None:
    st.header("Trazabilidad de los documentos")
    st.caption(
        "Cada dato procede de un PDF público de la web de una gestora. Aquí se ve de dónde y cuándo se descargó."
    )
    docs = pd.DataFrame(fetch("/documentos", limit=500))
    if docs.empty:
        st.info("Sin documentos.")
        return
    c1, c2 = st.columns(2)
    c1.metric("PDFs", len(docs))
    c2.metric("Fuentes", docs["fuente"].nunique())
    docs["descargado_en"] = pd.to_datetime(docs["descargado_en"], errors="coerce")
    docs["clase_etiqueta"] = docs["clase_etiqueta"].fillna("—")
    st.dataframe(
        docs[
            [
                "fuente",
                "fondo_slug",
                "clase_etiqueta",
                "isin",
                "url_pagina",
                "url_pdf",
                "descargado_en",
                "tamano_bytes",
                "sha256",
            ]
        ],
        hide_index=True,
        width="stretch",
        column_config={
            "url_pagina": st.column_config.LinkColumn("Ficha", display_text="ficha"),
            "url_pdf": st.column_config.LinkColumn("PDF original", display_text="pdf"),
            "descargado_en": st.column_config.DatetimeColumn(
                "Descargado", format="DD/MM/YYYY HH:mm"
            ),
            "tamano_bytes": st.column_config.NumberColumn("Bytes"),
        },
    )


PAGES = {
    "📈 Resumen": page_resumen,
    "🔎 Explorador": page_explorador,
    "⚖️ Comparador": page_comparador,
    "🧾 Trazabilidad": page_trazabilidad,
}


def main() -> None:
    st.sidebar.title("📊 KID de fondos")
    st.sidebar.caption(
        "Del KID al dashboard: datos de documentos de datos fundamentales extraídos con IA (Amazon Bedrock)."
    )
    choice = st.sidebar.radio("Sección", list(PAGES), label_visibility="collapsed")
    try:
        api = get_api()
    except ApiError as exc:
        st.error(str(exc))
        return
    st.sidebar.divider()
    st.sidebar.caption(f"API: `{urlsplit(api.base_url).netloc}`")
    if st.sidebar.button("Refrescar datos"):
        st.cache_data.clear()
    try:
        PAGES[choice]()
    except ApiError as exc:
        st.error(f"No se pudo obtener la información: {exc}")
        st.caption(
            "Si la base de datos estaba en pausa, espera unos segundos y pulsa «Refrescar datos»."
        )


main()
