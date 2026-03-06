
import io
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from openpyxl import Workbook
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils.dataframe import dataframe_to_rows


st.set_page_config(
    page_title="SensorPush Pro v4.1",
    page_icon="📊",
    layout="wide",
)

st.title("📊 SensorPush Pro v4.1")
st.caption("Dashboard avanzado con gráficas dinámicas, KPI de cumplimiento, criterio de amplitud térmica/higrométrica y exportación CSV / Excel / PNG / PDF.")


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
        if ("humedad relativa" in norm) or ("humedad" in norm):
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
    else:
        df = pd.read_excel(uploaded_file)

    return df


def prepare_dataframe(df_raw):
    df = df_raw.copy()
    time_col, temp_col, hum_col = detect_columns(df)

    df = df[[time_col, temp_col, hum_col]].copy()
    df.columns = ["Marca de Tiempo", "Temperatura", "Humedad"]

    df["Marca de Tiempo"] = pd.to_datetime(df["Marca de Tiempo"], errors="coerce")
    df["Temperatura"] = pd.to_numeric(df["Temperatura"], errors="coerce")
    df["Humedad"] = pd.to_numeric(df["Humedad"], errors="coerce")

    df = df.dropna(subset=["Marca de Tiempo"]).sort_values("Marca de Tiempo").reset_index(drop=True)
    return df


def out_of_range_mask(series, low, high):
    return (series < low) | (series > high)


def compute_compliance(series, low, high):
    valid = series.dropna()
    if len(valid) == 0:
        return 0
    ok = ((valid >= low) & (valid <= high)).sum()
    return (ok / len(valid)) * 100


def compute_range_delta(series):
    valid = series.dropna()
    if len(valid) == 0:
        return None
    return float(valid.max() - valid.min())


def build_plotly_chart(df, y_col, title, y_label, low, high):
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=df["Marca de Tiempo"],
            y=df[y_col],
            mode="lines",
            name=y_col,
        )
    )

    fig.add_hrect(
        y0=low,
        y1=high,
        opacity=0.12,
        line_width=0,
    )

    fig.add_hline(y=low, line_dash="dash")
    fig.add_hline(y=high, line_dash="dash")

    fig.update_layout(
        title=title,
        xaxis_title="Tiempo",
        yaxis_title=y_label,
        hovermode="x unified",
        height=500,
    )

    fig.update_xaxes(rangeslider_visible=True)

    return fig


def build_excel_table(df):

    wb = Workbook()
    ws = wb.active
    ws.title = "Datos Procesados"

    for r in dataframe_to_rows(df, index=False, header=True):
        ws.append(r)

    tab = Table(displayName="TablaDatos", ref=f"A1:{chr(64+len(df.columns))}{len(df)+1}")

    style = TableStyleInfo(
        name="TableStyleMedium9",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )

    tab.tableStyleInfo = style
    ws.add_table(tab)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return buffer.getvalue()


# -----------------------
# Sidebar
# -----------------------
with st.sidebar:

    uploaded_file = st.file_uploader("Sube CSV o Excel", type=["csv","xlsx","xls"])

    st.subheader("Límites")

    temp_low = st.number_input("Temp mínima", value=20.0)
    temp_high = st.number_input("Temp máxima", value=23.0)

    hum_low = st.number_input("HR mínima", value=30.0)
    hum_high = st.number_input("HR máxima", value=40.0)


if uploaded_file is None:
    st.info("Sube un archivo para comenzar")
    st.stop()

df_raw = load_data(uploaded_file)
df = prepare_dataframe(df_raw)

df["Temp_Fuera_Rango"] = out_of_range_mask(df["Temperatura"], temp_low, temp_high)
df["HR_Fuera_Rango"] = out_of_range_mask(df["Humedad"], hum_low, hum_high)

temp_compliance = compute_compliance(df["Temperatura"], temp_low, temp_high)
hum_compliance = compute_compliance(df["Humedad"], hum_low, hum_high)
temp_delta = compute_range_delta(df["Temperatura"])
hum_delta = compute_range_delta(df["Humedad"])

st.subheader("Resumen ejecutivo")

c1, c2 = st.columns(2)
c1.metric("Cumplimiento temperatura", f"{temp_compliance:.2f}%")
c2.metric("Cumplimiento humedad", f"{hum_compliance:.2f}%")

c3, c4 = st.columns(2)
c3.metric(
    "Δ temperatura (máx - mín)",
    f"{temp_delta:.2f} °C" if temp_delta is not None else "N/D",
    delta="Cumple" if temp_delta is not None and temp_delta <= 2 else "No cumple" if temp_delta is not None else None
)
c4.metric(
    "Δ humedad relativa (máx - mín)",
    f"{hum_delta:.2f} %" if hum_delta is not None else "N/D",
    delta="Cumple" if hum_delta is not None and hum_delta <= 5 else "No cumple" if hum_delta is not None else None
)

if temp_delta is not None:
    if temp_delta <= 2:
        st.success(f"La amplitud de temperatura es {temp_delta:.2f} °C y cumple el criterio de no ser mayor a 2 °C.")
    else:
        st.warning(f"La amplitud de temperatura es {temp_delta:.2f} °C y no cumple el criterio, porque supera 2 °C.")

if hum_delta is not None:
    if hum_delta <= 5:
        st.success(f"La amplitud de humedad relativa es {hum_delta:.2f} % y cumple el criterio de no ser mayor a 5 % HR.")
    else:
        st.warning(f"La amplitud de humedad relativa es {hum_delta:.2f} % y no cumple el criterio, porque supera 5 % HR.")

st.subheader("Gráficas")

fig_temp = build_plotly_chart(
    df,"Temperatura","Temperatura","°C",temp_low,temp_high
)

fig_hum = build_plotly_chart(
    df,"Humedad","Humedad Relativa","%",hum_low,hum_high
)

st.plotly_chart(fig_temp,use_container_width=True)
st.plotly_chart(fig_hum,use_container_width=True)


st.subheader("Datos procesados")

st.dataframe(df,use_container_width=True)


# ----------------------
# DESCARGAS
# ----------------------

csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
excel_bytes = build_excel_table(df)

d1,d2 = st.columns(2)

with d1:
    st.download_button(
        "Descargar CSV",
        data=csv_bytes,
        file_name="datos_procesados.csv",
        mime="text/csv"
    )

with d2:
    st.download_button(
        "Descargar Excel (tabla)",
        data=excel_bytes,
        file_name="datos_procesados_tabla.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
