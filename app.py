
import io
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


st.set_page_config(
    page_title="SensorPush Pro v4",
    page_icon="📊",
    layout="wide",
)

st.title("📊 SensorPush Pro v4")
st.caption("Dashboard avanzado con gráficas dinámicas, sombreado fuera de rango, KPI de cumplimiento, criterio Δ temperatura / Δ humedad relativa y exportación de reportes.")


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


def compute_compliance(series: pd.Series, low: float, high: float):
    valid = series.dropna()
    if len(valid) == 0:
        return 0.0
    ok = ((valid >= low) & (valid <= high)).sum()
    return (ok / len(valid)) * 100


def compute_range_delta(series: pd.Series):
    valid = series.dropna()
    if len(valid) == 0:
        return None
    return float(valid.max() - valid.min())


def classify_state(value, low, high):
    if pd.isna(value):
        return "Sin dato"
    if value < low:
        return "Bajo"
    if value > high:
        return "Alto"
    return "En rango"


def build_processed_export(df, temp_low, temp_high, hum_low, hum_high):
    out = df.copy()
    out["Estado_Temperatura"] = out["Temperatura"].apply(lambda x: classify_state(x, temp_low, temp_high))
    out["Estado_Humedad"] = out["Humedad"].apply(lambda x: classify_state(x, hum_low, hum_high))
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


def add_limit_band(fig, low, high):
    fig.add_hrect(
        y0=low,
        y1=high,
        opacity=0.12,
        line_width=0,
        annotation_text="Rango aceptable",
        annotation_position="top left",
    )
    fig.add_hline(y=low, line_dash="dash", annotation_text=f"Límite inferior: {low}", annotation_position="bottom left")
    fig.add_hline(y=high, line_dash="dash", annotation_text=f"Límite superior: {high}", annotation_position="top left")


def build_plotly_chart(df, y_col, title, y_label, low, high, show_markers=True):
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=df["Marca de Tiempo"],
            y=df[y_col],
            mode="lines",
            name=y_col,
            hovertemplate="%{x}<br>%{y:.2f}<extra></extra>",
        )
    )

    if show_markers:
        flagged = df.loc[out_of_range_mask(df[y_col], low, high), ["Marca de Tiempo", y_col]].dropna()
        if not flagged.empty:
            fig.add_trace(
                go.Scatter(
                    x=flagged["Marca de Tiempo"],
                    y=flagged[y_col],
                    mode="markers",
                    name="Fuera de rango",
                    hovertemplate="%{x}<br>%{y:.2f}<extra></extra>",
                    marker=dict(size=7),
                )
            )

    add_limit_band(fig, low, high)

    fig.update_layout(
        title=title,
        xaxis_title="Tiempo",
        yaxis_title=y_label,
        hovermode="x unified",
        height=500,
        legend_title_text="Serie",
        margin=dict(l=20, r=20, t=60, b=20)
    )
    fig.update_xaxes(rangeslider_visible=True, showspikes=True)
    fig.update_yaxes(showspikes=True)
    return fig


def build_matplotlib_chart(df: pd.DataFrame, y_col: str, title: str, y_label: str, low: float, high: float):
    fig, ax = plt.subplots(figsize=(12, 5.5))
    ymin = min(df[y_col].min(), low) if df[y_col].notna().any() else low
    ymax = max(df[y_col].max(), high) if df[y_col].notna().any() else high
    ax.add_patch(Rectangle((mdates.date2num(df["Marca de Tiempo"].min()), low),
                           width=mdates.date2num(df["Marca de Tiempo"].max()) - mdates.date2num(df["Marca de Tiempo"].min()),
                           height=high-low, alpha=0.12))
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
    ax.set_ylim(ymin - 0.05 * abs(ymax - ymin + 1), ymax + 0.05 * abs(ymax - ymin + 1))
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


def generate_pdf_report(df, fig_temp, fig_hum, events_df, temp_limits, hum_limits, temp_compliance, hum_compliance):
    pdf_buffer = io.BytesIO()

    with PdfPages(pdf_buffer) as pdf:
        fig, ax = plt.subplots(figsize=(11.69, 8.27))
        ax.axis("off")

        temp_stats = summarize_series(df["Temperatura"])
        hum_stats = summarize_series(df["Humedad"])
        start = df["Marca de Tiempo"].min()
        end = df["Marca de Tiempo"].max()

        lines = [
            "REPORTE SENSORPUSH PRO V4",
            "",
            f"Periodo analizado: {start:%Y-%m-%d %H:%M} a {end:%Y-%m-%d %H:%M}",
            f"Registros: {len(df)}",
            "",
            f"Límites temperatura: {temp_limits[0]} a {temp_limits[1]} °C",
            f"Límites humedad: {hum_limits[0]} a {hum_limits[1]} %",
            "",
            f"Cumplimiento temperatura: {temp_compliance:.2f}%",
            f"Cumplimiento humedad: {hum_compliance:.2f}%",
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

        pdf.savefig(fig_temp, bbox_inches="tight")
        pdf.savefig(fig_hum, bbox_inches="tight")

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

    st.subheader("Visualización")
    chart_option = st.radio(
        "Mostrar",
        ["Ambas variables", "Solo temperatura", "Solo humedad"],
        index=0
    )
    show_markers = st.checkbox("Marcar puntos fuera de rango", value=True)
    show_raw = st.checkbox("Mostrar datos originales", value=False)
    show_processed = st.checkbox("Mostrar datos procesados", value=True)

    st.markdown("---")
    st.caption("v4 añade KPI de cumplimiento, selector de visualización y sombreado de rango aceptable.")


# ---------------------------
# Carga y preparación
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

temp_stats = summarize_series(df_view["Temperatura"])
hum_stats = summarize_series(df_view["Humedad"])

temp_out = duration_out_of_range(df_view, "Temperatura", temp_low, temp_high)
hum_out = duration_out_of_range(df_view, "Humedad", hum_low, hum_high)

temp_compliance = compute_compliance(df_view["Temperatura"], temp_low, temp_high)
hum_compliance = compute_compliance(df_view["Humedad"], hum_low, hum_high)
temp_delta = compute_range_delta(df_view["Temperatura"])
hum_delta = compute_range_delta(df_view["Humedad"])

events_temp = find_events(df_view, "Temperatura", temp_low, temp_high, "Temperatura")
events_hum = find_events(df_view, "Humedad", hum_low, hum_high, "Humedad")
events_df = pd.concat([events_temp, events_hum], ignore_index=True).sort_values("Inicio") if not events_temp.empty or not events_hum.empty else pd.DataFrame()

# ---------------------------
# KPIs
# ---------------------------
st.subheader("📌 Resumen ejecutivo")
r1, r2, r3, r4 = st.columns(4)
r1.metric("Registros analizados", f"{len(df_view):,}".replace(",", "."))
r2.metric("Cumplimiento temp.", f"{temp_compliance:.2f}%")
r3.metric("Cumplimiento humedad", f"{hum_compliance:.2f}%")
r4.metric("Eventos fuera de rango", f"{len(events_df)}")

r5, r6, r7, r8 = st.columns(4)
r5.metric("Prom. temperatura", f"{temp_stats['promedio']:.2f} °C" if temp_stats["promedio"] is not None else "N/D")
r6.metric("Prom. humedad", f"{hum_stats['promedio']:.2f} %" if hum_stats["promedio"] is not None else "N/D")
r7.metric("Min. temp. / Máx. temp.", f"{temp_stats['mínimo']:.2f} / {temp_stats['máximo']:.2f}" if temp_stats["mínimo"] is not None else "N/D")
r8.metric("Min. HR / Máx. HR", f"{hum_stats['mínimo']:.2f} / {hum_stats['máximo']:.2f}" if hum_stats["mínimo"] is not None else "N/D")

r9, r10 = st.columns(2)
r9.metric(
    "Δ temperatura (máx - mín)",
    f"{temp_delta:.2f} °C" if temp_delta is not None else "N/D",
    delta="Cumple ≤ 2 °C" if temp_delta is not None and temp_delta <= 2 else "No cumple > 2 °C" if temp_delta is not None else None
)
r10.metric(
    "Δ humedad relativa (máx - mín)",
    f"{hum_delta:.2f} %" if hum_delta is not None else "N/D",
    delta="Cumple ≤ 5 % HR" if hum_delta is not None and hum_delta <= 5 else "No cumple > 5 % HR" if hum_delta is not None else None
)

if temp_delta is not None:
    if temp_delta <= 2:
        st.success(f"La diferencia entre la temperatura máxima y mínima es {temp_delta:.2f} °C y cumple el criterio de no ser mayor a 2 °C.")
    else:
        st.warning(f"La diferencia entre la temperatura máxima y mínima es {temp_delta:.2f} °C y no cumple el criterio, porque supera 2 °C.")

if hum_delta is not None:
    if hum_delta <= 5:
        st.success(f"La diferencia entre la humedad relativa máxima y mínima es {hum_delta:.2f} % y cumple el criterio de no ser mayor a 5 % HR.")
    else:
        st.warning(f"La diferencia entre la humedad relativa máxima y mínima es {hum_delta:.2f} % y no cumple el criterio, porque supera 5 % HR.")

st.progress(min((temp_compliance + hum_compliance) / 200, 1.0), text=f"Cumplimiento global promedio: {((temp_compliance + hum_compliance)/2):.2f}%")

st.markdown("---")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📈 Dashboard", "🚨 Eventos", "📋 Datos", "⬇️ Descargas", "ℹ️ Diagnóstico"])

with tab1:
    if chart_option in ["Ambas variables", "Solo temperatura"]:
        st.markdown("**Temperatura**")
        fig_temp_plotly = build_plotly_chart(
            df_view, "Temperatura",
            "Temperatura a lo largo del tiempo",
            "Temperatura (°C)",
            temp_low, temp_high,
            show_markers=show_markers
        )
        st.plotly_chart(fig_temp_plotly, use_container_width=True)

    if chart_option in ["Ambas variables", "Solo humedad"]:
        st.markdown("**Humedad relativa**")
        fig_hum_plotly = build_plotly_chart(
            df_view, "Humedad",
            "Humedad relativa a lo largo del tiempo",
            "Humedad relativa (%)",
            hum_low, hum_high,
            show_markers=show_markers
        )
        st.plotly_chart(fig_hum_plotly, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Cumplimiento por variable**")
        gauge_df = pd.DataFrame({
            "Variable": ["Temperatura", "Humedad"],
            "Cumplimiento (%)": [round(temp_compliance, 2), round(hum_compliance, 2)]
        })
        st.dataframe(gauge_df, use_container_width=True, hide_index=True)

    with c2:
        st.markdown("**Interpretación rápida**")
        msgs = []
        if temp_compliance < 100:
            msgs.append(f"Temperatura fuera de criterio en {temp_out['registros']} registros.")
        if hum_compliance < 100:
            msgs.append(f"Humedad fuera de criterio en {hum_out['registros']} registros.")
        if not msgs:
            st.success("Todas las mediciones visibles cumplen con los límites establecidos.")
        else:
            st.warning(" ".join(msgs))

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
    fig_temp_static = build_matplotlib_chart(
        df_view, "Temperatura",
        "Temperatura a lo largo del tiempo",
        "Temperatura (°C)",
        temp_low, temp_high
    )
    fig_hum_static = build_matplotlib_chart(
        df_view, "Humedad",
        "Humedad relativa a lo largo del tiempo",
        "Humedad relativa (%)",
        hum_low, hum_high
    )

    temp_png = fig_to_bytes(fig_temp_static)
    hum_png = fig_to_bytes(fig_hum_static)
    csv_bytes = processed_export.to_csv(index=False).encode("utf-8-sig")
    pdf_bytes = generate_pdf_report(
        df_view,
        fig_temp_static,
        fig_hum_static,
        events_df,
        (temp_low, temp_high),
        (hum_low, hum_high),
        temp_compliance,
        hum_compliance
    )

    d1, d2 = st.columns(2)
    with d1:
        st.download_button(
            "Descargar gráfico de temperatura (PNG)",
            data=temp_png,
            file_name="grafico_temperatura_v4.png",
            mime="image/png",
            use_container_width=True,
        )
        st.download_button(
            "Descargar datos procesados (CSV)",
            data=csv_bytes,
            file_name="datos_procesados_sensorpush_v4.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with d2:
        st.download_button(
            "Descargar gráfico de humedad (PNG)",
            data=hum_png,
            file_name="grafico_humedad_v4.png",
            mime="image/png",
            use_container_width=True,
        )
        st.download_button(
            "Descargar reporte PDF",
            data=pdf_bytes,
            file_name="reporte_sensorpush_v4.pdf",
            mime="application/pdf",
            use_container_width=True,
        )

with tab5:
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

    st.markdown("**Columnas detectadas**")
    st.write({
        "Fecha/hora": "Marca de Tiempo",
        "Temperatura": "Temperatura",
        "Humedad": "Humedad",
    })
