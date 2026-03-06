
import io
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="SensorPush Pro",
    page_icon="🌡️",
    layout="wide",
)

st.title("🌡️ SensorPush Pro")
st.caption("Análisis avanzado de temperatura y humedad con límites dinámicos, eventos fuera de rango y reportes descargables.")


# ---------------------------
# Utilidades
# ---------------------------
def normalize_text(text: str) -> str:
    return (
        str(text)
        .strip()
        .lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("°", "")
        .replace("%", "")
        .replace("(", " ")
        .replace(")", " ")
        .replace("/", " ")
        .replace("-", " ")
        .replace("_", " ")
    )


def detect_columns(df: pd.DataFrame):
    original_cols = list(df.columns)
    norm_map = {col: normalize_text(col) for col in original_cols}

    time_col = None
    temp_col = None
    hum_col = None

    for col, norm in norm_map.items():
        if ("marca de tiempo" in norm) or ("timestamp" in norm) or ("fecha" in norm and "hora" in norm):
            time_col = col
            break

    for col, norm in norm_map.items():
        if ("temperatura" in norm) or ("temp" in norm):
            temp_col = col
            break

    for col, norm in norm_map.items():
        if ("humedad relativa" in norm) or ("humedad" in norm) or ("relative humidity" in norm):
            hum_col = col
            break

    return time_col, temp_col, hum_col


def load_data(uploaded_file):
    suffix = Path(uploaded_file.name).suffix.lower()

    if suffix == ".csv":
        # Intento 1: utf-8
        try:
            df = pd.read_csv(uploaded_file)
        except Exception:
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, encoding="latin1")
    elif suffix in [".xlsx", ".xls"]:
        df = pd.read_excel(uploaded_file)
    else:
        raise ValueError("Formato no soportado. Usa CSV o Excel.")

    return df


def prepare_dataframe(df_raw: pd.DataFrame):
    df = df_raw.copy()
    time_col, temp_col, hum_col = detect_columns(df)

    if not time_col or not temp_col or not hum_col:
        raise ValueError(
            "No pude identificar automáticamente las columnas de fecha/hora, temperatura y humedad. "
            "Verifica que existan columnas equivalentes a 'Marca de Tiempo', 'TEMPERATURA (°C)' y 'HUMEDAD RELATIVA (%)'."
        )

    df = df[[time_col, temp_col, hum_col]].copy()
    df.columns = ["Marca de Tiempo", "Temperatura", "Humedad"]

    df["Marca de Tiempo"] = pd.to_datetime(df["Marca de Tiempo"], errors="coerce")
    df["Temperatura"] = pd.to_numeric(df["Temperatura"], errors="coerce")
    df["Humedad"] = pd.to_numeric(df["Humedad"], errors="coerce")

    df = df.dropna(subset=["Marca de Tiempo"]).sort_values("Marca de Tiempo").reset_index(drop=True)

    return df


def apply_resample(df: pd.DataFrame, freq: str):
    if freq == "Sin agrupar":
        return df.copy()

    freq_map = {
        "Cada 15 minutos": "15min",
        "Cada 30 minutos": "30min",
        "Cada 1 hora": "1h",
        "Cada 6 horas": "6h",
        "Diario": "1d",
    }

    rule = freq_map[freq]
    out = (
        df.set_index("Marca de Tiempo")[["Temperatura", "Humedad"]]
        .resample(rule)
        .mean()
        .reset_index()
    )
    return out.dropna(how="all", subset=["Temperatura", "Humedad"])


def compute_sampling_minutes(df: pd.DataFrame):
    if len(df) < 2:
        return None
    diffs = df["Marca de Tiempo"].sort_values().diff().dropna()
    if diffs.empty:
        return None
    return diffs.median().total_seconds() / 60


def summarize_series(series: pd.Series):
    return {
        "mínimo": float(series.min()) if series.notna().any() else None,
        "máximo": float(series.max()) if series.notna().any() else None,
        "promedio": float(series.mean()) if series.notna().any() else None,
        "desv_std": float(series.std()) if series.notna().sum() > 1 else None,
    }


def out_of_range_mask(series: pd.Series, low: float, high: float):
    return (series < low) | (series > high)


def find_events(df: pd.DataFrame, value_col: str, low: float, high: float, label: str):
    work = df[["Marca de Tiempo", value_col]].dropna().copy()
    if work.empty:
        return pd.DataFrame(columns=[
            "Variable", "Inicio", "Fin", "Duración (min)", "Tipo", "Mínimo", "Máximo", "Promedio", "N registros"
        ])

    work["Fuera_Rango"] = out_of_range_mask(work[value_col], low, high)

    if not work["Fuera_Rango"].any():
        return pd.DataFrame(columns=[
            "Variable", "Inicio", "Fin", "Duración (min)", "Tipo", "Mínimo", "Máximo", "Promedio", "N registros"
        ])

    work["grupo"] = (work["Fuera_Rango"] != work["Fuera_Rango"].shift()).cumsum()
    events = []

    for _, grp in work.groupby("grupo"):
        if not bool(grp["Fuera_Rango"].iloc[0]):
            continue

        start = grp["Marca de Tiempo"].iloc[0]
        end = grp["Marca de Tiempo"].iloc[-1]

        vmin = grp[value_col].min()
        vmax = grp[value_col].max()
        vavg = grp[value_col].mean()

        below = (grp[value_col] < low).all()
        above = (grp[value_col] > high).all()

        if below:
            kind = "Por debajo del límite"
        elif above:
            kind = "Por encima del límite"
        else:
            kind = "Mixto"

        duration_min = (end - start).total_seconds() / 60 if len(grp) > 1 else 0

        events.append({
            "Variable": label,
            "Inicio": start,
            "Fin": end,
            "Duración (min)": round(duration_min, 1),
            "Tipo": kind,
            "Mínimo": round(float(vmin), 2),
            "Máximo": round(float(vmax), 2),
            "Promedio": round(float(vavg), 2),
            "N registros": int(len(grp)),
        })

    return pd.DataFrame(events)


def plot_variable(df: pd.DataFrame, y_col: str, title: str, y_label: str, low: float, high: float):
    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax.plot(df["Marca de Tiempo"], df[y_col], linewidth=1.8, label=y_col)
    ax.axhline(low, linestyle="--", label=f"Límite inferior ({low})")
    ax.axhline(high, linestyle="--", label=f"Límite superior ({high})")

    mask = out_of_range_mask(df[y_col], low, high)
    flagged = df.loc[mask, ["Marca de Tiempo", y_col]].dropna()
    if not flagged.empty:
        ax.scatter(flagged["Marca de Tiempo"], flagged[y_col], s=20, zorder=3, label="Fuera de rango")

    ax.set_title(title)
    ax.set_xlabel("Tiempo")
    ax.set_ylabel(y_label)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d\n%H:%M"))
    fig.autofmt_xdate()
    ax.grid(True, alpha=0.3)
    ax.legend()
    plt.tight_layout()
    return fig


def fig_to_bytes(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    buf.seek(0)
    return buf.getvalue()


def build_processed_export(df, temp_low, temp_high, hum_low, hum_high):
    out = df.copy()
    out["Temperatura_Fuera_Rango"] = out_of_range_mask(out["Temperatura"], temp_low, temp_high)
    out["Humedad_Fuera_Rango"] = out_of_range_mask(out["Humedad"], hum_low, hum_high)
    return out


def duration_out_of_range(df: pd.DataFrame, col: str, low: float, high: float):
    work = df[["Marca de Tiempo", col]].dropna().sort_values("Marca de Tiempo").copy()
    if len(work) < 2:
        count = int(out_of_range_mask(work[col], low, high).sum()) if not work.empty else 0
        return {"registros": count, "minutos_estimados": 0.0, "porcentaje_registros": 0.0 if work.empty else (count / len(work)) * 100}

    mask = out_of_range_mask(work[col], low, high)
    step_min = compute_sampling_minutes(work) or 0
    count = int(mask.sum())
    pct = (count / len(work)) * 100 if len(work) else 0
    minutes = count * step_min
    return {"registros": count, "minutos_estimados": minutes, "porcentaje_registros": pct}


def generate_pdf_report(df, fig_temp, fig_hum, events_df, temp_limits, hum_limits):
    pdf_buffer = io.BytesIO()

    with PdfPages(pdf_buffer) as pdf:
        # Página 1: Resumen
        fig, ax = plt.subplots(figsize=(11.69, 8.27))  # A4 horizontal aprox.
        ax.axis("off")

        temp_stats = summarize_series(df["Temperatura"])
        hum_stats = summarize_series(df["Humedad"])
        start = df["Marca de Tiempo"].min()
        end = df["Marca de Tiempo"].max()

        lines = [
            "REPORTE SENSORPUSH PRO",
            "",
            f"Periodo analizado: {start:%Y-%m-%d %H:%M} a {end:%Y-%m-%d %H:%M}",
            f"Registros: {len(df)}",
            "",
            f"Límites temperatura: {temp_limits[0]} a {temp_limits[1]} °C",
            f"Límites humedad: {hum_limits[0]} a {hum_limits[1]} %",
            "",
            "Resumen temperatura:",
            f"  - Mínimo: {temp_stats['mínimo']:.2f} °C" if temp_stats["mínimo"] is not None else "  - Sin datos",
            f"  - Máximo: {temp_stats['máximo']:.2f} °C" if temp_stats["máximo"] is not None else "",
            f"  - Promedio: {temp_stats['promedio']:.2f} °C" if temp_stats["promedio"] is not None else "",
            "",
            "Resumen humedad:",
            f"  - Mínimo: {hum_stats['mínimo']:.2f} %" if hum_stats["mínimo"] is not None else "  - Sin datos",
            f"  - Máximo: {hum_stats['máximo']:.2f} %" if hum_stats["máximo"] is not None else "",
            f"  - Promedio: {hum_stats['promedio']:.2f} %" if hum_stats["promedio"] is not None else "",
            "",
            f"Eventos fuera de rango detectados: {len(events_df)}",
        ]

        ax.text(0.03, 0.97, "\n".join(lines), va="top", ha="left", fontsize=12, family="sans-serif")
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # Página 2 y 3: gráficos
        pdf.savefig(fig_temp, bbox_inches="tight")
        pdf.savefig(fig_hum, bbox_inches="tight")

        # Página 4: eventos
        if not events_df.empty:
            fig, ax = plt.subplots(figsize=(11.69, 8.27))
            ax.axis("off")
            ax.set_title("Eventos fuera de rango", pad=15)

            table_df = events_df.copy()
            table_df["Inicio"] = pd.to_datetime(table_df["Inicio"]).dt.strftime("%Y-%m-%d %H:%M")
            table_df["Fin"] = pd.to_datetime(table_df["Fin"]).dt.strftime("%Y-%m-%d %H:%M")

            table = ax.table(
                cellText=table_df.values,
                colLabels=table_df.columns,
                loc="center",
                cellLoc="center",
            )
            table.auto_set_font_size(False)
            table.set_fontsize(7.5)
            table.scale(1, 1.3)
            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)

    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()


# ---------------------------
# Sidebar
# ---------------------------
with st.sidebar:
    st.header("⚙️ Configuración")
    uploaded_file = st.file_uploader("Sube CSV o Excel", type=["csv", "xlsx", "xls"])

    st.subheader("Límites de alarma")
    temp_low = st.number_input("Temperatura mínima (°C)", value=20.0, step=0.5)
    temp_high = st.number_input("Temperatura máxima (°C)", value=23.0, step=0.5)

    hum_low = st.number_input("Humedad mínima (%)", value=30.0, step=1.0)
    hum_high = st.number_input("Humedad máxima (%)", value=40.0, step=1.0)

    st.subheader("Agrupación de datos")
    freq = st.selectbox(
        "Frecuencia de visualización",
        ["Sin agrupar", "Cada 15 minutos", "Cada 30 minutos", "Cada 1 hora", "Cada 6 horas", "Diario"],
        index=0
    )

    st.subheader("Opciones")
    show_raw = st.checkbox("Mostrar datos originales", value=False)
    show_processed = st.checkbox("Mostrar datos procesados", value=True)

    st.markdown("---")
    st.caption("Versión 2: más potente, lista para publicar gratis en Streamlit Community Cloud.")


# ---------------------------
# Flujo principal
# ---------------------------
if uploaded_file is None:
    st.info("Sube un archivo CSV o Excel del SensorPush para comenzar.")
    st.stop()

try:
    df_raw = load_data(uploaded_file)
    df = prepare_dataframe(df_raw)
except Exception as e:
    st.error(f"No fue posible procesar el archivo: {e}")
    st.stop()

# filtro por rango de fechas
min_date = df["Marca de Tiempo"].min().to_pydatetime()
max_date = df["Marca de Tiempo"].max().to_pydatetime()

col_a, col_b = st.columns(2)
with col_a:
    start_date = st.date_input("Fecha inicial", value=min_date.date(), min_value=min_date.date(), max_value=max_date.date())
with col_b:
    end_date = st.date_input("Fecha final", value=max_date.date(), min_value=min_date.date(), max_value=max_date.date())

df = df[(df["Marca de Tiempo"].dt.date >= start_date) & (df["Marca de Tiempo"].dt.date <= end_date)].copy()

if df.empty:
    st.warning("No hay datos en el rango de fechas seleccionado.")
    st.stop()

df_view = apply_resample(df, freq)
processed_export = build_processed_export(df_view, temp_low, temp_high, hum_low, hum_high)

temp_out = duration_out_of_range(df_view, "Temperatura", temp_low, temp_high)
hum_out = duration_out_of_range(df_view, "Humedad", hum_low, hum_high)

events_temp = find_events(df_view, "Temperatura", temp_low, temp_high, "Temperatura")
events_hum = find_events(df_view, "Humedad", hum_low, hum_high, "Humedad")
events_df = pd.concat([events_temp, events_hum], ignore_index=True).sort_values("Inicio") if not events_temp.empty or not events_hum.empty else pd.DataFrame()

temp_stats = summarize_series(df_view["Temperatura"])
hum_stats = summarize_series(df_view["Humedad"])

# métricas
st.subheader("📌 Resumen rápido")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Registros analizados", f"{len(df_view):,}".replace(",", "."))
m2.metric("Prom. temperatura", f"{temp_stats['promedio']:.2f} °C" if temp_stats["promedio"] is not None else "N/D")
m3.metric("Prom. humedad", f"{hum_stats['promedio']:.2f} %" if hum_stats["promedio"] is not None else "N/D")
m4.metric("Eventos fuera de rango", f"{len(events_df)}")

t1, t2, t3, t4 = st.columns(4)
t1.metric("Temp. mínima", f"{temp_stats['mínimo']:.2f} °C" if temp_stats["mínimo"] is not None else "N/D")
t2.metric("Temp. máxima", f"{temp_stats['máximo']:.2f} °C" if temp_stats["máximo"] is not None else "N/D")
t3.metric("Humedad mínima", f"{hum_stats['mínimo']:.2f} %" if hum_stats["mínimo"] is not None else "N/D")
t4.metric("Humedad máxima", f"{hum_stats['máximo']:.2f} %" if hum_stats["máximo"] is not None else "N/D")

st.markdown("---")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📈 Gráficos", "🚨 Eventos", "📋 Datos", "⬇️ Descargas", "ℹ️ Diagnóstico"])

with tab1:
    fig_temp = plot_variable(
        df_view, "Temperatura",
        "Temperatura a lo largo del tiempo",
        "Temperatura (°C)",
        temp_low, temp_high
    )
    st.pyplot(fig_temp, use_container_width=True)

    fig_hum = plot_variable(
        df_view, "Humedad",
        "Humedad relativa a lo largo del tiempo",
        "Humedad relativa (%)",
        hum_low, hum_high
    )
    st.pyplot(fig_hum, use_container_width=True)

with tab2:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Temperatura fuera de rango**")
        if events_temp.empty:
            st.success("No se detectaron eventos fuera de rango.")
        else:
            st.dataframe(events_temp, use_container_width=True)

    with c2:
        st.markdown("**Humedad fuera de rango**")
        if events_hum.empty:
            st.success("No se detectaron eventos fuera de rango.")
        else:
            st.dataframe(events_hum, use_container_width=True)

    st.markdown("**Todos los eventos**")
    if events_df.empty:
        st.info("No hay eventos para mostrar.")
    else:
        st.dataframe(events_df, use_container_width=True)

with tab3:
    if show_raw:
        st.markdown("**Datos originales identificados**")
        st.dataframe(df_raw, use_container_width=True)

    if show_processed:
        st.markdown("**Datos procesados**")
        st.dataframe(processed_export, use_container_width=True)

with tab4:
    temp_png = fig_to_bytes(fig_temp)
    hum_png = fig_to_bytes(fig_hum)
    csv_bytes = processed_export.to_csv(index=False).encode("utf-8-sig")

    pdf_bytes = generate_pdf_report(
        df_view,
        fig_temp,
        fig_hum,
        events_df,
        (temp_low, temp_high),
        (hum_low, hum_high),
    )

    d1, d2 = st.columns(2)
    with d1:
        st.download_button(
            "Descargar gráfico de temperatura (PNG)",
            data=temp_png,
            file_name="grafico_temperatura.png",
            mime="image/png",
            use_container_width=True,
        )
        st.download_button(
            "Descargar datos procesados (CSV)",
            data=csv_bytes,
            file_name="datos_procesados_sensorpush.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with d2:
        st.download_button(
            "Descargar gráfico de humedad (PNG)",
            data=hum_png,
            file_name="grafico_humedad.png",
            mime="image/png",
            use_container_width=True,
        )
        st.download_button(
            "Descargar reporte PDF",
            data=pdf_bytes,
            file_name="reporte_sensorpush.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

with tab5:
    st.markdown("**Columnas detectadas**")
    st.write({
        "Fecha/hora": "Marca de Tiempo",
        "Temperatura": "Temperatura",
        "Humedad": "Humedad",
    })

    sampling = compute_sampling_minutes(df)
    st.markdown("**Diagnóstico del archivo**")
    st.write({
        "Registros originales": len(df_raw),
        "Registros válidos en periodo": len(df),
        "Registros mostrados tras agrupación": len(df_view),
        "Paso de muestreo estimado (min)": round(sampling, 2) if sampling is not None else None,
        "Porcentaje temp. fuera de rango": round(temp_out["porcentaje_registros"], 2),
        "Porcentaje humedad fuera de rango": round(hum_out["porcentaje_registros"], 2),
        "Minutos estimados temp. fuera de rango": round(temp_out["minutos_estimados"], 1),
        "Minutos estimados humedad fuera de rango": round(hum_out["minutos_estimados"], 1),
    })

    st.markdown("**Interpretación rápida**")
    if temp_out["registros"] == 0 and hum_out["registros"] == 0:
        st.success("Todos los registros visibles se encuentran dentro de los límites configurados.")
    else:
        msgs = []
        if temp_out["registros"] > 0:
            msgs.append(
                f"Temperatura fuera de rango en {temp_out['registros']} registros "
                f"({temp_out['porcentaje_registros']:.2f}%)."
            )
        if hum_out["registros"] > 0:
            msgs.append(
                f"Humedad fuera de rango en {hum_out['registros']} registros "
                f"({hum_out['porcentaje_registros']:.2f}%)."
            )
        st.warning(" ".join(msgs))
