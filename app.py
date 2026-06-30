import io
import zipfile
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


st.set_page_config(
    page_title="SensorPush Pro v9",
    page_icon="📊",
    layout="wide",
)

st.title("📊 SensorPush Pro v9")
st.caption(
    "Modo dual: SensorPush (temperatura + humedad) y Solo Temperatura (neveras, cámaras frías). "
    "Modo nevera incluye sistema de 3 niveles FAO: Seguro / Alerta / Acción, con presets por equipo."
)


# ===========================================================
# UTILIDADES GENERALES
# ===========================================================
def normalize_text(text: str) -> str:
    return (
        str(text).strip().lower()
        .replace("á","a").replace("é","e").replace("í","i")
        .replace("ó","o").replace("ú","u").replace("°","")
        .replace("%","").replace("(","").replace(")","")
        .replace("/","").replace("-"," ").replace("_"," ")
    )

def detect_columns(df: pd.DataFrame):
    norm_map = {col: normalize_text(col) for col in df.columns}
    time_col = temp_col = hum_col = None
    for col, norm in norm_map.items():
        if ("marca de tiempo" in norm) or ("timestamp" in norm) or ("fecha" in norm and "hora" in norm):
            time_col = col; break
    for col, norm in norm_map.items():
        if ("temperatura" in norm) or ("temp" in norm):
            temp_col = col; break
    for col, norm in norm_map.items():
        if ("humedad relativa" in norm) or ("humedad" in norm) or ("relative humidity" in norm):
            hum_col = col; break
    return time_col, temp_col, hum_col

def clean_filename(name: str) -> str:
    name = str(name).strip()
    if not name:
        return "reporte"
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name


# ===========================================================
# CARGA — MODO SENSORPUSH
# ===========================================================
def load_data_sensorpush(uploaded_file):
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix == ".csv":
        try:
            df = pd.read_csv(uploaded_file)
        except Exception:
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, encoding="latin1")
    elif suffix in [".xlsx", ".xls"]:
        df = pd.read_excel(uploaded_file)
    elif suffix == ".zip":
        uploaded_file.seek(0)
        zip_bytes = io.BytesIO(uploaded_file.read())
        with zipfile.ZipFile(zip_bytes, "r") as zf:
            csv_files = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not csv_files:
                raise ValueError("El ZIP no contiene archivos CSV.")
            csv_name = csv_files[0]
            with zf.open(csv_name) as f:
                try:
                    df = pd.read_csv(f)
                except Exception:
                    with zf.open(csv_name) as f2:
                        df = pd.read_csv(f2, encoding="latin1")
    else:
        raise ValueError("Formato no soportado. Usa CSV, Excel o ZIP.")
    return df

def prepare_dataframe_sensorpush(df_raw: pd.DataFrame):
    df = df_raw.copy()
    time_col, temp_col, hum_col = detect_columns(df)
    if not time_col or not temp_col or not hum_col:
        raise ValueError(
            "No pude identificar las columnas de fecha/hora, temperatura y humedad. "
            "Verifica que existan columnas equivalentes a 'Marca de Tiempo', "
            "'TEMPERATURA (°C)' y 'HUMEDAD RELATIVA (%)'."
        )
    df = df[[time_col, temp_col, hum_col]].copy()
    df.columns = ["Marca de Tiempo", "Temperatura", "Humedad"]
    df["Marca de Tiempo"] = pd.to_datetime(df["Marca de Tiempo"], errors="coerce")
    df["Temperatura"] = pd.to_numeric(df["Temperatura"], errors="coerce")
    df["Humedad"] = pd.to_numeric(df["Humedad"], errors="coerce")
    df = df.dropna(subset=["Marca de Tiempo"]).sort_values("Marca de Tiempo").reset_index(drop=True)
    return df


# ===========================================================
# CARGA — MODO SOLO TEMPERATURA (formato nevera)
# índice;fecha;hora;temperatura — sin encabezados, decimal coma
# ===========================================================
def load_data_solo_temp(uploaded_file):
    if Path(uploaded_file.name).suffix.lower() not in [".csv", ".txt"]:
        raise ValueError("Solo se admiten archivos CSV o TXT en modo Solo Temperatura.")
    for enc in ["utf-8-sig", "utf-8", "latin1"]:
        try:
            uploaded_file.seek(0)
            df = pd.read_csv(uploaded_file, sep=";", header=None,
                             encoding=enc, decimal=",", engine="python")
            break
        except Exception:
            continue
    else:
        raise ValueError("No fue posible leer el archivo. Verifica separador ';' y decimal ','.")
    df = df.dropna(axis=1, how="all")
    if df.shape[1] < 4:
        raise ValueError(
            f"Se esperaban al menos 4 columnas (índice, fecha, hora, temperatura) "
            f"pero se encontraron {df.shape[1]}."
        )
    df = df.iloc[:, [1, 2, 3]].copy()
    df.columns = ["_fecha", "_hora", "Temperatura"]
    df["Marca de Tiempo"] = pd.to_datetime(
        df["_fecha"].astype(str).str.strip() + " " + df["_hora"].astype(str).str.strip(),
        errors="coerce"
    )
    df["Temperatura"] = pd.to_numeric(df["Temperatura"], errors="coerce")
    df = df[["Marca de Tiempo", "Temperatura"]].dropna(subset=["Marca de Tiempo"])
    return df.sort_values("Marca de Tiempo").reset_index(drop=True)


# ===========================================================
# CLASIFICACIÓN DE NIVELES (Solo Temperatura)
# Niveles: "Seguro" | "Alerta" | "Acción" | "Sin dato"
# Lógica basada en tabla FAO / Innova Eats:
#   Seguro : temp_low <= T <= temp_high
#   Alerta : zona intermedia entre el límite seguro y el límite de acción,
#            a cualquiera de los dos lados (frío o caliente)
#   Acción : T < action_low  o  T > action_high
# ===========================================================
def classify_nivel(value, temp_low, temp_high, action_low, action_high):
    if pd.isna(value):
        return "Sin dato"
    if temp_low <= value <= temp_high:
        return "Seguro"
    if value < temp_low:
        return "Acción" if value < action_low else "Alerta"
    return "Acción" if value > action_high else "Alerta"

# Color por nivel
NIVEL_COLOR = {
    "Seguro":  "#2ecc71",   # verde
    "Alerta":  "#f39c12",   # amarillo/naranja
    "Acción":  "#e74c3c",   # rojo
    "Sin dato":"#95a5a6",   # gris
}


# ===========================================================
# PROCESAMIENTO COMÚN
# ===========================================================
def apply_resample(df: pd.DataFrame, freq: str, tiene_humedad: bool):
    if freq == "Sin agrupar":
        return df.copy()
    freq_map = {
        "Cada 2 minutos": "2min", "Cada 15 minutos": "15min",
        "Cada 30 minutos": "30min", "Cada 1 hora": "1h",
        "Cada 6 horas": "6h", "Diario": "1d",
    }
    rule = freq_map.get(freq, "2min")
    cols = ["Temperatura", "Humedad"] if tiene_humedad else ["Temperatura"]
    out = df.set_index("Marca de Tiempo")[cols].resample(rule).mean().reset_index()
    return out.dropna(how="all", subset=cols)

def compute_sampling_minutes(df: pd.DataFrame):
    if len(df) < 2:
        return None
    diffs = df["Marca de Tiempo"].sort_values().diff().dropna()
    return diffs.median().total_seconds() / 60 if not diffs.empty else None

def summarize_series(series: pd.Series):
    return {
        "mínimo":   float(series.min())  if series.notna().any() else None,
        "máximo":   float(series.max())  if series.notna().any() else None,
        "promedio": float(series.mean()) if series.notna().any() else None,
        "desv_std": float(series.std())  if series.notna().sum() > 1 else None,
    }

def out_of_range_mask(series: pd.Series, low: float, high: float):
    return (series < low) | (series > high)

def compute_compliance(series: pd.Series, low: float, high: float):
    valid = series.dropna()
    if len(valid) == 0:
        return 0.0
    return ((valid >= low) & (valid <= high)).sum() / len(valid) * 100

def classify_state(value, low, high):
    if pd.isna(value): return "Sin dato"
    if value < low:    return "Bajo"
    if value > high:   return "Alto"
    return "En rango"

def build_processed_export_sensorpush(df, temp_low, temp_high, hum_low, hum_high):
    out = df.copy()
    out["Estado_Temperatura"] = out["Temperatura"].apply(lambda x: classify_state(x, temp_low, temp_high))
    out["Temperatura_Fuera_Rango"] = out_of_range_mask(out["Temperatura"], temp_low, temp_high)
    out["Estado_Humedad"] = out["Humedad"].apply(lambda x: classify_state(x, hum_low, hum_high))
    out["Humedad_Fuera_Rango"] = out_of_range_mask(out["Humedad"], hum_low, hum_high)
    return out

def build_processed_export_solo_temp(df, temp_low, temp_high,
                                      action_low, action_high):
    out = df.copy()
    out["Nivel"] = out["Temperatura"].apply(
        lambda x: classify_nivel(x, temp_low, temp_high, action_low, action_high)
    )
    return out

def duration_out_of_range(df: pd.DataFrame, col: str, low: float, high: float):
    work = df[["Marca de Tiempo", col]].dropna().sort_values("Marca de Tiempo").copy()
    if len(work) < 2:
        count = int(out_of_range_mask(work[col], low, high).sum()) if not work.empty else 0
        return {"registros": count, "minutos_estimados": 0.0,
                "porcentaje_registros": 0.0 if work.empty else count / len(work) * 100}
    mask = out_of_range_mask(work[col], low, high)
    step_min = compute_sampling_minutes(work) or 0
    count = int(mask.sum())
    return {"registros": count,
            "minutos_estimados": count * step_min,
            "porcentaje_registros": count / len(work) * 100}

def find_events(df: pd.DataFrame, value_col: str, low: float, high: float, label: str):
    empty_cols = ["Variable","Inicio","Fin","Duración (min)","Tipo","Mínimo","Máximo","Promedio","N registros"]
    work = df[["Marca de Tiempo", value_col]].dropna().copy()
    if work.empty:
        return pd.DataFrame(columns=empty_cols)
    work["Fuera_Rango"] = out_of_range_mask(work[value_col], low, high)
    if not work["Fuera_Rango"].any():
        return pd.DataFrame(columns=empty_cols)
    work["grupo"] = (work["Fuera_Rango"] != work["Fuera_Rango"].shift()).cumsum()
    events = []
    for _, grp in work.groupby("grupo"):
        if not bool(grp["Fuera_Rango"].iloc[0]):
            continue
        start, end = grp["Marca de Tiempo"].iloc[0], grp["Marca de Tiempo"].iloc[-1]
        vmin, vmax, vavg = grp[value_col].min(), grp[value_col].max(), grp[value_col].mean()
        kind = ("Por debajo del límite" if (grp[value_col] < low).all()
                else "Por encima del límite" if (grp[value_col] > high).all()
                else "Mixto")
        events.append({
            "Variable": label, "Inicio": start, "Fin": end,
            "Duración (min)": round((end-start).total_seconds()/60, 1) if len(grp)>1 else 0,
            "Tipo": kind, "Mínimo": round(float(vmin),2),
            "Máximo": round(float(vmax),2), "Promedio": round(float(vavg),2),
            "N registros": int(len(grp)),
        })
    return pd.DataFrame(events)

# Versión con nivel para modo Solo Temperatura
def find_events_niveles(df: pd.DataFrame, temp_low, temp_high, action_low, action_high):
    """Devuelve events_alerta y events_accion como DataFrames separados."""
    empty_cols = ["Nivel","Inicio","Fin","Duración (min)","Tipo","Mínimo","Máximo","Promedio","N registros"]
    work = df[["Marca de Tiempo","Temperatura"]].dropna().copy()
    if work.empty:
        return pd.DataFrame(columns=empty_cols), pd.DataFrame(columns=empty_cols)

    work["Nivel"] = work["Temperatura"].apply(
        lambda x: classify_nivel(x, temp_low, temp_high, action_low, action_high)
    )
    work["Fuera"] = work["Nivel"].isin(["Alerta", "Acción"])

    if not work["Fuera"].any():
        return pd.DataFrame(columns=empty_cols), pd.DataFrame(columns=empty_cols)

    work["grupo"] = (work["Nivel"] != work["Nivel"].shift()).cumsum()
    alerta_events, accion_events = [], []

    for _, grp in work.groupby("grupo"):
        nivel = grp["Nivel"].iloc[0]
        if nivel not in ["Alerta", "Acción"]:
            continue
        start, end = grp["Marca de Tiempo"].iloc[0], grp["Marca de Tiempo"].iloc[-1]
        vals = grp["Temperatura"]
        kind = ("Por debajo del límite" if (vals < temp_low).all()
                else "Por encima del límite" if (vals > temp_high).all()
                else "Mixto")
        row = {
            "Nivel": nivel, "Inicio": start, "Fin": end,
            "Duración (min)": round((end-start).total_seconds()/60,1) if len(grp)>1 else 0,
            "Tipo": kind, "Mínimo": round(float(vals.min()),2),
            "Máximo": round(float(vals.max()),2), "Promedio": round(float(vals.mean()),2),
            "N registros": int(len(grp)),
        }
        if nivel == "Alerta":
            alerta_events.append(row)
        else:
            accion_events.append(row)

    return pd.DataFrame(alerta_events) if alerta_events else pd.DataFrame(columns=empty_cols), \
           pd.DataFrame(accion_events) if accion_events else pd.DataFrame(columns=empty_cols)


# ===========================================================
# VISUALIZACIÓN — SENSORPUSH (un solo rango)
# ===========================================================
def add_limit_band(fig, low, high):
    fig.add_hrect(y0=low, y1=high, opacity=0.12, line_width=0,
                  annotation_text="Rango aceptable", annotation_position="top left")
    fig.add_hline(y=low, line_dash="dash",
                  annotation_text=f"Límite inf.: {low}", annotation_position="bottom left")
    fig.add_hline(y=high, line_dash="dash",
                  annotation_text=f"Límite sup.: {high}", annotation_position="top left")

def build_plotly_chart(df, y_col, title, y_label, low, high, show_markers=True):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["Marca de Tiempo"], y=df[y_col], mode="lines", name=y_col,
        hovertemplate="%{x}<br>%{y:.2f}<extra></extra>",
    ))
    if show_markers:
        flagged = df.loc[out_of_range_mask(df[y_col], low, high),
                         ["Marca de Tiempo", y_col]].dropna()
        if not flagged.empty:
            fig.add_trace(go.Scatter(
                x=flagged["Marca de Tiempo"], y=flagged[y_col],
                mode="markers", name="Fuera de rango",
                hovertemplate="%{x}<br>%{y:.2f}<extra></extra>",
                marker=dict(size=7),
            ))
    add_limit_band(fig, low, high)
    fig.update_layout(title=title, xaxis_title="Tiempo", yaxis_title=y_label,
                      hovermode="x unified", height=500, legend_title_text="Serie",
                      margin=dict(l=20, r=20, t=60, b=20))
    fig.update_xaxes(rangeslider_visible=True, showspikes=True)
    fig.update_yaxes(showspikes=True)
    return fig


# ===========================================================
# VISUALIZACIÓN — SOLO TEMPERATURA (3 niveles con colores)
# ===========================================================
def build_plotly_chart_niveles(df, temp_low, temp_high,
                                action_low, action_high,
                                show_markers=True):
    """
    Gráfica Plotly con 3 zonas de color:
      Verde   → rango seguro   [temp_low, temp_high]
      Amarillo→ zona alerta    [action_low, temp_low) ∪ (temp_high, action_high]
      Rojo    → zona acción    < action_low  o  > action_high
    """
    fig = go.Figure()

    # ---- Zonas de fondo ----
    y_min_plot = min(df["Temperatura"].min(), action_low) - 1
    y_max_plot = max(df["Temperatura"].max(), action_high) + 1

    # Zona ACCIÓN inferior
    fig.add_hrect(y0=y_min_plot, y1=action_low,
                  fillcolor="#e74c3c", opacity=0.08, line_width=0)
    # Zona ALERTA inferior
    fig.add_hrect(y0=action_low, y1=temp_low,
                  fillcolor="#f39c12", opacity=0.10, line_width=0)
    # Zona SEGURA
    fig.add_hrect(y0=temp_low, y1=temp_high,
                  fillcolor="#2ecc71", opacity=0.12, line_width=0,
                  annotation_text="Seguro", annotation_position="top left")
    # Zona ALERTA superior
    fig.add_hrect(y0=temp_high, y1=action_high,
                  fillcolor="#f39c12", opacity=0.10, line_width=0)
    # Zona ACCIÓN superior
    fig.add_hrect(y0=action_high, y1=y_max_plot,
                  fillcolor="#e74c3c", opacity=0.08, line_width=0)

    # ---- Líneas de límite ----
    for y, label, color in [
        (action_low,  f"Acción inf. ({action_low}°C)",  "#c0392b"),
        (temp_low,    f"Seguro inf. ({temp_low}°C)",    "#27ae60"),
        (temp_high,   f"Seguro sup. ({temp_high}°C)",   "#27ae60"),
        (action_high, f"Acción sup. ({action_high}°C)", "#c0392b"),
    ]:
        fig.add_hline(y=y, line_dash="dash", line_color=color,
                      annotation_text=label, annotation_position="bottom right",
                      annotation_font_color=color)

    # ---- Serie principal ----
    fig.add_trace(go.Scatter(
        x=df["Marca de Tiempo"], y=df["Temperatura"],
        mode="lines", name="Temperatura",
        line=dict(color="#2c3e50", width=1.8),
        hovertemplate="%{x}<br>%{y:.2f} °C<extra></extra>",
    ))

    # ---- Marcadores por nivel ----
    if show_markers:
        for nivel, color in [("Alerta", "#f39c12"), ("Acción", "#e74c3c")]:
            mask = df["Temperatura"].apply(
                lambda x: classify_nivel(x, temp_low, temp_high, action_low, action_high)
            ) == nivel
            pts = df.loc[mask, ["Marca de Tiempo","Temperatura"]].dropna()
            if not pts.empty:
                fig.add_trace(go.Scatter(
                    x=pts["Marca de Tiempo"], y=pts["Temperatura"],
                    mode="markers", name=nivel,
                    marker=dict(size=6, color=color),
                    hovertemplate=f"<b>{nivel}</b><br>%{{x}}<br>%{{y:.2f}} °C<extra></extra>",
                ))

    fig.update_layout(
        title="Temperatura — Sistema de niveles FAO",
        xaxis_title="Tiempo", yaxis_title="Temperatura (°C)",
        hovermode="x unified", height=520, legend_title_text="Serie",
        margin=dict(l=20, r=20, t=60, b=20),
        yaxis=dict(range=[y_min_plot, y_max_plot]),
    )
    fig.update_xaxes(rangeslider_visible=True, showspikes=True)
    fig.update_yaxes(showspikes=True)
    return fig


# ===========================================================
# MATPLOTLIB — exportación estática
# ===========================================================
def build_matplotlib_chart(df, y_col, title, y_label, low, high):
    fig, ax = plt.subplots(figsize=(12, 5.5))
    ymin = min(df[y_col].min(), low) if df[y_col].notna().any() else low
    ymax = max(df[y_col].max(), high) if df[y_col].notna().any() else high
    t_min, t_max = df["Marca de Tiempo"].min(), df["Marca de Tiempo"].max()
    if pd.notna(t_min) and pd.notna(t_max) and t_min != t_max:
        ax.add_patch(Rectangle(
            (mdates.date2num(t_min), low),
            width=mdates.date2num(t_max) - mdates.date2num(t_min),
            height=high - low, alpha=0.12, color="#27ae60"
        ))
    ax.plot(df["Marca de Tiempo"], df[y_col], linewidth=1.8, label=y_col, color="#2c3e50")
    ax.axhline(low,  linestyle="--", color="#27ae60", label=f"Límite inf. ({low})")
    ax.axhline(high, linestyle="--", color="#27ae60", label=f"Límite sup. ({high})")
    mask = out_of_range_mask(df[y_col], low, high)
    flagged = df.loc[mask, ["Marca de Tiempo", y_col]].dropna()
    if not flagged.empty:
        ax.scatter(flagged["Marca de Tiempo"], flagged[y_col], s=20, zorder=3,
                   color="#e74c3c", label="Fuera de rango")
    ax.set_title(title); ax.set_xlabel("Tiempo"); ax.set_ylabel(y_label)
    ax.set_ylim(ymin - 0.05*abs(ymax-ymin+1), ymax + 0.05*abs(ymax-ymin+1))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d\n%H:%M"))
    fig.autofmt_xdate(); ax.grid(True, alpha=0.3); ax.legend(); plt.tight_layout()
    return fig

def build_matplotlib_chart_niveles(df, temp_low, temp_high,
                                    action_low, action_high):
    fig, ax = plt.subplots(figsize=(12, 5.5))
    y_vals = df["Temperatura"].dropna()
    ymin = min(y_vals.min(), action_low) - 1 if not y_vals.empty else action_low - 1
    ymax = max(y_vals.max(), action_high) + 1 if not y_vals.empty else action_high + 1
    t_min, t_max = df["Marca de Tiempo"].min(), df["Marca de Tiempo"].max()
    if pd.notna(t_min) and pd.notna(t_max) and t_min != t_max:
        w = mdates.date2num(t_max) - mdates.date2num(t_min)
        x0 = mdates.date2num(t_min)
        # Zonas
        ax.add_patch(Rectangle((x0, ymin),        w, action_low-ymin,      alpha=0.08, color="#e74c3c"))
        ax.add_patch(Rectangle((x0, action_low),  w, temp_low-action_low,  alpha=0.10, color="#f39c12"))
        ax.add_patch(Rectangle((x0, temp_low),    w, temp_high-temp_low,   alpha=0.12, color="#2ecc71"))
        ax.add_patch(Rectangle((x0, temp_high),   w, action_high-temp_high,alpha=0.10, color="#f39c12"))
        ax.add_patch(Rectangle((x0, action_high), w, ymax-action_high,     alpha=0.08, color="#e74c3c"))

    ax.plot(df["Marca de Tiempo"], df["Temperatura"], linewidth=1.8,
            label="Temperatura", color="#2c3e50", zorder=5)

    for y, label, color in [
        (action_low,  f"Acción inf. ({action_low})", "#c0392b"),
        (temp_low,    f"Seguro inf. ({temp_low})",   "#27ae60"),
        (temp_high,   f"Seguro sup. ({temp_high})",  "#27ae60"),
        (action_high, f"Acción sup. ({action_high})","#c0392b"),
    ]:
        ax.axhline(y, linestyle="--", color=color, linewidth=0.9, label=label)

    # Marcadores alerta/acción
    for nivel, color in [("Alerta","#f39c12"),("Acción","#e74c3c")]:
        mask = df["Temperatura"].apply(
            lambda x: classify_nivel(x, temp_low, temp_high, action_low, action_high)
        ) == nivel
        pts = df.loc[mask, ["Marca de Tiempo","Temperatura"]].dropna()
        if not pts.empty:
            ax.scatter(pts["Marca de Tiempo"], pts["Temperatura"],
                       s=18, zorder=6, color=color, label=nivel)

    ax.set_title("Temperatura — Sistema de niveles FAO")
    ax.set_xlabel("Tiempo"); ax.set_ylabel("Temperatura (°C)")
    ax.set_ylim(ymin, ymax)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d\n%H:%M"))
    fig.autofmt_xdate(); ax.grid(True, alpha=0.3); ax.legend(fontsize=7); plt.tight_layout()
    return fig

def fig_to_bytes(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    buf.seek(0)
    return buf.getvalue()

def dataframe_to_excel_bytes(df: pd.DataFrame, sheet_name: str = "Datos procesados"):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        ws = writer.sheets[sheet_name]
        for col in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col), default=0)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 35)
    output.seek(0)
    return output.getvalue()


# ===========================================================
# PDF — SENSORPUSH
# ===========================================================
def generate_pdf_report_sensorpush(df, fig_temp, fig_hum, events_df,
                                    temp_limits, hum_limits,
                                    temp_compliance, hum_compliance,
                                    delta_temp=None, delta_hum=None,
                                    temp_delta_ok=None, hum_delta_ok=None):
    pdf_buffer = io.BytesIO()
    with PdfPages(pdf_buffer) as pdf:
        fig, ax = plt.subplots(figsize=(11.69, 8.27)); ax.axis("off")
        ts = summarize_series(df["Temperatura"])
        hs = summarize_series(df["Humedad"])
        start, end = df["Marca de Tiempo"].min(), df["Marca de Tiempo"].max()
        lines = [
            "REPORTE SENSORPUSH PRO V8",
            f"Periodo: {start:%Y-%m-%d %H:%M} a {end:%Y-%m-%d %H:%M}",
            f"Registros: {len(df)}",
            "",
            f"Límites temperatura: {temp_limits[0]} – {temp_limits[1]} °C",
            f"Límites humedad: {hum_limits[0]} – {hum_limits[1]} %",
            "",
            f"Cumplimiento temperatura: {temp_compliance:.2f}%",
            f"Cumplimiento humedad: {hum_compliance:.2f}%",
            f"Δ Temperatura: {delta_temp:.2f} °C  ({'Cumple' if temp_delta_ok else 'No cumple'} ≤2°C)" if delta_temp is not None else "Δ Temperatura: N/D",
            f"Δ HR: {delta_hum:.2f} %  ({'Cumple' if hum_delta_ok else 'No cumple'} ≤5%)" if delta_hum is not None else "Δ HR: N/D",
            "",
            f"Temperatura — mín: {ts['mínimo']:.2f}  máx: {ts['máximo']:.2f}  prom: {ts['promedio']:.2f} °C" if ts["mínimo"] is not None else "Temperatura — sin datos",
            f"Humedad — mín: {hs['mínimo']:.2f}  máx: {hs['máximo']:.2f}  prom: {hs['promedio']:.2f} %" if hs["mínimo"] is not None else "Humedad — sin datos",
            "",
            f"Eventos fuera de rango: {len(events_df)}",
        ]
        ax.text(0.03, 0.97, "\n".join(lines), va="top", ha="left", fontsize=12, family="sans-serif")
        pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)
        pdf.savefig(fig_temp, bbox_inches="tight")
        if fig_hum: pdf.savefig(fig_hum, bbox_inches="tight")
        _pdf_events_table(pdf, events_df)
    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()


# ===========================================================
# PDF — SOLO TEMPERATURA (3 niveles)
# ===========================================================
def generate_pdf_report_solo_temp(df, fig_static, events_alerta, events_accion,
                                   temp_low, temp_high, action_low, action_high,
                                   temp_compliance, n_alerta, n_accion,
                                   delta_temp=None, temp_delta_ok=None):
    pdf_buffer = io.BytesIO()
    with PdfPages(pdf_buffer) as pdf:
        fig, ax = plt.subplots(figsize=(11.69, 8.27)); ax.axis("off")
        ts = summarize_series(df["Temperatura"])
        start, end = df["Marca de Tiempo"].min(), df["Marca de Tiempo"].max()
        lines = [
            "REPORTE SENSORPUSH PRO V9 — SOLO TEMPERATURA",
            f"Referencia: Sistema de niveles FAO / Innova Eats",
            "",
            f"Periodo: {start:%Y-%m-%d %H:%M} a {end:%Y-%m-%d %H:%M}",
            f"Registros: {len(df)}",
            "",
            "LÍMITES DE OPERACIÓN:",
            f"  Parámetro seguro (Innova Eats): {temp_low} – {temp_high} °C",
            f"  Alerta y seguimiento:           {action_low} – {temp_low} °C  |  {temp_high} – {action_high} °C",
            f"  Parámetro de acción:            < {action_low} °C  |  > {action_high} °C",
            "",
            "RESUMEN DE CUMPLIMIENTO:",
            f"  % Registros en rango seguro: {temp_compliance:.2f}%",
            f"  Eventos de ALERTA detectados: {n_alerta}",
            f"  Eventos de ACCIÓN detectados: {n_accion}",
            f"  Δ Temperatura: {delta_temp:.2f} °C  ({'Cumple' if temp_delta_ok else 'No cumple'} ≤2°C)" if delta_temp is not None else "  Δ Temperatura: N/D",
            "",
            "ESTADÍSTICAS:",
            f"  Mínimo:  {ts['mínimo']:.2f} °C" if ts["mínimo"] is not None else "  Sin datos",
            f"  Máximo:  {ts['máximo']:.2f} °C" if ts["máximo"] is not None else "",
            f"  Promedio:{ts['promedio']:.2f} °C" if ts["promedio"] is not None else "",
        ]
        ax.text(0.03, 0.97, "\n".join(lines), va="top", ha="left", fontsize=11, family="sans-serif")
        pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)
        pdf.savefig(fig_static, bbox_inches="tight")
        _pdf_events_table(pdf, events_alerta, title="Eventos de ALERTA")
        _pdf_events_table(pdf, events_accion, title="Eventos de ACCIÓN")
    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()

def _pdf_events_table(pdf, events_df, title="Eventos fuera de rango"):
    if events_df is None or events_df.empty:
        return
    fig, ax = plt.subplots(figsize=(11.69, 8.27)); ax.axis("off")
    ax.set_title(title, pad=15, fontsize=13)
    tdf = events_df.copy()
    for col in ["Inicio","Fin"]:
        if col in tdf.columns:
            tdf[col] = pd.to_datetime(tdf[col]).dt.strftime("%Y-%m-%d %H:%M")
    table = ax.table(cellText=tdf.values, colLabels=tdf.columns,
                     loc="center", cellLoc="center")
    table.auto_set_font_size(False); table.set_fontsize(7.5); table.scale(1, 1.3)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


# ===========================================================
# PRESETS DE EQUIPOS — Límites FAO / Innova Eats
# (Seguro = temp_low–temp_high · Acción = fuera de action_low–action_high
#  · Alerta = la zona intermedia, calculada automáticamente)
# ===========================================================
PRESETS_NEVERA = {
    "Personalizado": None,
    "Congelador (precongelados)": {
        "temp_low": -30.0, "temp_high": -10.0,
        "action_low": -30.0, "action_high": -5.0,
    },
    "Nevera de procesos (lácteos)": {
        "temp_low": 2.0, "temp_high": 8.0,
        "action_low": -2.0, "action_high": 10.0,
    },
    "Nevera 1 - Nivel 3 (carne de res)": {
        "temp_low": 2.0, "temp_high": 8.0,
        "action_low": -2.0, "action_high": 10.0,
    },
    "Nevera 1 - Nivel 2 (carne de cerdo)": {
        "temp_low": 2.0, "temp_high": 8.0,
        "action_low": -2.0, "action_high": 10.0,
    },
    "Nevera 1 - Nivel 1 (pollo y aves)": {
        "temp_low": 2.0, "temp_high": 8.0,
        "action_low": -2.0, "action_high": 10.0,
    },
    "Cuarto frío": {
        "temp_low": 2.0, "temp_high": 8.0,
        "action_low": -2.0, "action_high": 10.0,
    },
}

def aplicar_preset():
    """Callback: al cambiar el selector de equipo, sobreescribe los number_input."""
    nombre = st.session_state.get("preset_equipo")
    preset = PRESETS_NEVERA.get(nombre)
    if preset:
        st.session_state["temp_low_input"]    = preset["temp_low"]
        st.session_state["temp_high_input"]   = preset["temp_high"]
        st.session_state["action_low_input"]  = preset["action_low"]
        st.session_state["action_high_input"] = preset["action_high"]


# ===========================================================
# SIDEBAR
# ===========================================================
with st.sidebar:
    st.header("⚙️ Configuración")

    st.subheader("Tipo de archivo")
    modo = st.radio(
        "Selecciona el formato de los datos:",
        ["SensorPush (Temp + Humedad)", "Solo Temperatura (Nevera / Cámara fría)"],
        index=0,
        help=(
            "SensorPush: CSV/Excel/ZIP con columnas Marca de Tiempo, Temperatura, Humedad.\n\n"
            "Solo Temperatura: CSV separador ';', sin encabezados, decimal coma. "
            "Columnas: índice | fecha | hora | temperatura."
        )
    )
    tiene_humedad = modo == "SensorPush (Temp + Humedad)"
    st.markdown("---")

    if tiene_humedad:
        uploaded_file = st.file_uploader("Sube CSV, Excel o ZIP", type=["csv","xlsx","xls","zip"])
    else:
        uploaded_file = st.file_uploader("Sube CSV de temperatura", type=["csv","txt"])

    # ---- Límites ----
    st.subheader("Límites de alarma")
    if tiene_humedad:
        temp_low   = st.number_input("Temperatura mínima (°C)", value=20.0, step=0.5)
        temp_high  = st.number_input("Temperatura máxima (°C)", value=23.0, step=0.5)
        hum_low    = st.number_input("Humedad mínima (%)",      value=30.0, step=1.0)
        hum_high   = st.number_input("Humedad máxima (%)",      value=40.0, step=1.0)
        # Valores dummy para modo solo temp
        action_low = 0.0
        action_high = 100.0
    else:
        st.markdown("**Preset de equipo**")
        preset_choice = st.selectbox(
            "Selecciona el equipo / producto",
            list(PRESETS_NEVERA.keys()),
            key="preset_equipo",
            on_change=aplicar_preset,
            help="Carga automáticamente los límites FAO / Innova Eats de ese equipo "
                 "(según las tablas de control de Global Bild). Puedes ajustar los valores "
                 "manualmente después si lo necesitas."
        )

        st.markdown("**🟢 Rango seguro (Innova Eats)**")
        temp_low  = st.number_input("Temp. mínima segura (°C)", value=2.0,  step=0.5, key="temp_low_input")
        temp_high = st.number_input("Temp. máxima segura (°C)", value=8.0,  step=0.5, key="temp_high_input")
        st.markdown("**🔴 Límites de acción** (fuera de aquí = crítico)")
        action_low  = st.number_input("Acción inferior (°C)",  value=-2.0, step=0.5,
                                       help="Menor a este valor → Acción inmediata", key="action_low_input")
        action_high = st.number_input("Acción superior (°C)", value=10.0,  step=0.5,
                                       help="Mayor a este valor → Acción inmediata", key="action_high_input")
        hum_low = hum_high = 0.0

        # Leyenda visual — Alerta se calcula automáticamente como la zona
        # intermedia entre el rango Seguro y los límites de Acción.
        st.markdown("""
        <div style='font-size:12px; margin-top:8px;'>
        <span style='color:#27ae60'>●</span> Seguro: {tl}–{th} °C<br>
        <span style='color:#e67e22'>●</span> Alerta: {acl}–{tl} °C  |  {th}–{ach} °C<br>
        <span style='color:#c0392b'>●</span> Acción: &lt;{acl} °C | &gt;{ach} °C
        </div>
        """.format(tl=temp_low, th=temp_high,
                   acl=action_low, ach=action_high),
        unsafe_allow_html=True)

    # ---- Agrupación ----
    st.subheader("Agrupación de datos")
    freq = st.selectbox(
        "Frecuencia de visualización",
        ["Sin agrupar","Cada 2 minutos","Cada 15 minutos","Cada 30 minutos",
         "Cada 1 hora","Cada 6 horas","Diario"],
        index=0
    )

    # ---- Visualización ----
    st.subheader("Visualización")
    if tiene_humedad:
        chart_option = st.radio("Mostrar",
            ["Ambas variables","Solo temperatura","Solo humedad"], index=0)
    else:
        chart_option = "Solo temperatura"
    show_markers  = st.checkbox("Marcar puntos fuera de rango", value=True)
    show_raw      = st.checkbox("Mostrar datos originales",     value=False)
    show_processed= st.checkbox("Mostrar datos procesados",     value=True)

    # ---- Nombre de archivos ----
    st.subheader("Nombre de archivos")
    default_name = "sensorpush_reporte" if tiene_humedad else "nevera_reporte"
    base_filename_input = st.text_input("Nombre base", value=default_name,
                                         help="Ej: monitoreo_nevera_mayo_2026")
    base_filename = clean_filename(base_filename_input) or default_name
    st.caption(f"Nombre actual: {base_filename}")
    st.markdown("---")
    st.caption("v9 — Niveles FAO de 3 zonas reales + presets por equipo.")


# ===========================================================
# CARGA Y PREPARACIÓN
# ===========================================================
if uploaded_file is None:
    if tiene_humedad:
        st.info("📂 Sube un archivo CSV, Excel o ZIP del SensorPush para comenzar.")
    else:
        st.info(
            "📂 Sube el archivo CSV de temperatura de la nevera.\n\n"
            "**Formato esperado:** separador `;` | sin encabezados | decimal con coma\n"
            "Columnas: `índice ; fecha ; hora ; temperatura`"
        )
    st.stop()

try:
    if tiene_humedad:
        df_raw = load_data_sensorpush(uploaded_file)
        df = prepare_dataframe_sensorpush(df_raw)
    else:
        df = load_data_solo_temp(uploaded_file)
        df_raw = df.copy()
except Exception as e:
    st.error(f"❌ No fue posible procesar el archivo: {e}")
    st.stop()

# Filtro por fechas
min_date = df["Marca de Tiempo"].min().to_pydatetime()
max_date = df["Marca de Tiempo"].max().to_pydatetime()
col_a, col_b = st.columns(2)
with col_a:
    start_date = st.date_input("Fecha inicial", value=min_date.date(),
                               min_value=min_date.date(), max_value=max_date.date())
with col_b:
    end_date = st.date_input("Fecha final",   value=max_date.date(),
                             min_value=min_date.date(), max_value=max_date.date())

df = df[(df["Marca de Tiempo"].dt.date >= start_date) &
        (df["Marca de Tiempo"].dt.date <= end_date)].copy()

if df.empty:
    st.warning("No hay datos en el rango de fechas seleccionado.")
    st.stop()

df_view    = apply_resample(df, freq, tiene_humedad)
df_metrics = df.copy()
temp_stats = summarize_series(df_metrics["Temperatura"])


# ===========================================================
# CÓMPUTOS SEGÚN MODO
# ===========================================================
if tiene_humedad:
    hum_stats      = summarize_series(df_metrics["Humedad"])
    processed_export = build_processed_export_sensorpush(
        df_view, temp_low, temp_high, hum_low, hum_high)
    delta_temp  = (temp_stats["máximo"] - temp_stats["mínimo"]
                   if temp_stats["máximo"] is not None else None)
    delta_hum   = (hum_stats["máximo"] - hum_stats["mínimo"]
                   if hum_stats.get("máximo") is not None else None)
    temp_delta_ok = None if delta_temp is None else delta_temp <= 2
    hum_delta_ok  = None if delta_hum  is None else delta_hum  <= 5
    temp_out     = duration_out_of_range(df_metrics, "Temperatura", temp_low, temp_high)
    hum_out      = duration_out_of_range(df_metrics, "Humedad",     hum_low,  hum_high)
    temp_compliance = compute_compliance(df_metrics["Temperatura"], temp_low, temp_high)
    hum_compliance  = compute_compliance(df_metrics["Humedad"],     hum_low,  hum_high)
    events_temp  = find_events(df_view, "Temperatura", temp_low, temp_high, "Temperatura")
    events_hum   = find_events(df_view, "Humedad",     hum_low,  hum_high,  "Humedad")
    events_df    = (pd.concat([events_temp, events_hum], ignore_index=True).sort_values("Inicio")
                    if not events_temp.empty or not events_hum.empty else pd.DataFrame())

else:
    processed_export = build_processed_export_solo_temp(
        df_view, temp_low, temp_high, action_low, action_high)
    delta_temp = (temp_stats["máximo"] - temp_stats["mínimo"]
                  if temp_stats["máximo"] is not None else None)
    temp_delta_ok = None if delta_temp is None else delta_temp <= 2
    temp_out = duration_out_of_range(df_metrics, "Temperatura", temp_low, temp_high)
    temp_compliance = compute_compliance(df_metrics["Temperatura"], temp_low, temp_high)

    # Cumplimientos por nivel
    niveles_series = df_metrics["Temperatura"].apply(
        lambda x: classify_nivel(x, temp_low, temp_high, action_low, action_high)
    )
    total_valid = df_metrics["Temperatura"].notna().sum()
    n_seguro  = int((niveles_series == "Seguro").sum())
    n_alerta_reg = int((niveles_series == "Alerta").sum())
    n_accion_reg = int((niveles_series == "Acción").sum())
    pct_seguro  = n_seguro  / total_valid * 100 if total_valid else 0
    pct_alerta  = n_alerta_reg / total_valid * 100 if total_valid else 0
    pct_accion  = n_accion_reg / total_valid * 100 if total_valid else 0

    events_alerta, events_accion = find_events_niveles(
        df_view, temp_low, temp_high, action_low, action_high)


# ===========================================================
# RESUMEN EJECUTIVO
# ===========================================================
st.subheader("📌 Resumen ejecutivo")

if tiene_humedad:
    r1,r2,r3,r4 = st.columns(4)
    r1.metric("Registros analizados",  f"{len(df_metrics):,}".replace(",","."))
    r2.metric("Cumplimiento temp.",    f"{temp_compliance:.2f}%")
    r3.metric("Cumplimiento humedad",  f"{hum_compliance:.2f}%")
    r4.metric("Eventos fuera de rango",f"{len(events_df)}")
    r5,r6,r7,r8 = st.columns(4)
    r5.metric("Prom. temperatura", f"{temp_stats['promedio']:.2f} °C" if temp_stats["promedio"] else "N/D")
    r6.metric("Prom. humedad",     f"{hum_stats['promedio']:.2f} %"  if hum_stats.get("promedio") else "N/D")
    r7.metric("Mín. temperatura",  f"{temp_stats['mínimo']:.2f} °C"  if temp_stats["mínimo"] else "N/D")
    r8.metric("Máx. temperatura",  f"{temp_stats['máximo']:.2f} °C"  if temp_stats["máximo"] else "N/D")
    r9,r10,r11,r12 = st.columns(4)
    r9.metric("Mín. HR",  f"{hum_stats['mínimo']:.2f} %" if hum_stats.get("mínimo") else "N/D")
    r10.metric("Máx. HR", f"{hum_stats['máximo']:.2f} %" if hum_stats.get("máximo") else "N/D")
    r11.metric("Δ Temperatura", f"{delta_temp:.2f} °C" if delta_temp is not None else "N/D",
               delta="Cumple" if temp_delta_ok else "No cumple")
    r12.metric("Δ Humedad relativa", f"{delta_hum:.2f} %" if delta_hum is not None else "N/D",
               delta="Cumple" if hum_delta_ok else "No cumple",
               delta_color="normal" if hum_delta_ok else "inverse")
    st.progress(min((temp_compliance+hum_compliance)/200, 1.0),
                text=f"Cumplimiento global: {(temp_compliance+hum_compliance)/2:.2f}%")
    alertas = []
    if temp_delta_ok is False: alertas.append("Δ Temperatura supera criterio (≤2°C).")
    if hum_delta_ok  is False: alertas.append("Δ HR supera criterio (≤5%).")
    st.warning(" ".join(alertas)) if alertas else st.success("Criterios de Δ cumplidos.")

else:
    # ---- KPIs modo Solo Temperatura con 3 niveles ----
    r1,r2,r3,r4 = st.columns(4)
    r1.metric("Registros analizados", f"{len(df_metrics):,}".replace(",","."))
    r2.metric("🟢 En rango seguro",   f"{pct_seguro:.2f}%",
              delta=f"{n_seguro} registros")
    r3.metric("🟡 En alerta",         f"{pct_alerta:.2f}%",
              delta=f"{n_alerta_reg} registros",
              delta_color="inverse" if n_alerta_reg > 0 else "off")
    r4.metric("🔴 En acción",         f"{pct_accion:.2f}%",
              delta=f"{n_accion_reg} registros",
              delta_color="inverse" if n_accion_reg > 0 else "off")

    r5,r6,r7,r8 = st.columns(4)
    r5.metric("Prom. temperatura", f"{temp_stats['promedio']:.2f} °C" if temp_stats["promedio"] is not None else "N/D")
    r6.metric("Mín. temperatura",  f"{temp_stats['mínimo']:.2f} °C"  if temp_stats["mínimo"]   is not None else "N/D")
    r7.metric("Máx. temperatura",  f"{temp_stats['máximo']:.2f} °C"  if temp_stats["máximo"]   is not None else "N/D")
    r8.metric("Δ Temperatura",     f"{delta_temp:.2f} °C" if delta_temp is not None else "N/D",
              delta="Cumple ≤2°C" if temp_delta_ok else "No cumple ≤2°C")

    r9,r10,r11,r12 = st.columns(4)
    r9.metric("Eventos de alerta",   f"{len(events_alerta)}")
    r10.metric("Eventos de acción",  f"{len(events_accion)}")
    r11.metric("Min. estimados alerta", f"{round(n_alerta_reg * (compute_sampling_minutes(df_metrics) or 0), 1)}")
    r12.metric("Min. estimados acción", f"{round(n_accion_reg  * (compute_sampling_minutes(df_metrics) or 0), 1)}")

    st.progress(min(pct_seguro/100, 1.0),
                text=f"Registros en rango seguro: {pct_seguro:.2f}%")

    if n_accion_reg > 0:
        st.error(f"⚠️ Se detectaron {n_accion_reg} registros en zona de ACCIÓN. Revisión inmediata recomendada.")
    elif n_alerta_reg > 0:
        st.warning(f"Se detectaron {n_alerta_reg} registros en zona de ALERTA. Verificar cadena de frío.")
    else:
        st.success("Todos los registros se encuentran en el rango seguro.")

st.markdown("---")


# ===========================================================
# PESTAÑAS
# ===========================================================
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["📈 Dashboard", "🚨 Eventos", "📋 Datos", "⬇️ Descargas", "ℹ️ Diagnóstico"])

# ---- TAB 1: Dashboard ----
with tab1:
    if tiene_humedad:
        if chart_option in ["Ambas variables","Solo temperatura"]:
            st.markdown("**Temperatura**")
            st.plotly_chart(build_plotly_chart(
                df_view,"Temperatura","Temperatura a lo largo del tiempo",
                "Temperatura (°C)",temp_low,temp_high,show_markers=show_markers),
                use_container_width=True)
        if chart_option in ["Ambas variables","Solo humedad"]:
            st.markdown("**Humedad relativa**")
            st.plotly_chart(build_plotly_chart(
                df_view,"Humedad","Humedad relativa a lo largo del tiempo",
                "Humedad relativa (%)",hum_low,hum_high,show_markers=show_markers),
                use_container_width=True)
        c1,c2 = st.columns(2)
        with c1:
            st.markdown("**Cumplimiento por variable**")
            st.dataframe(pd.DataFrame({
                "Variable":["Temperatura","Humedad"],
                "Cumplimiento (%)":[round(temp_compliance,2),round(hum_compliance,2)]
            }), use_container_width=True, hide_index=True)
        with c2:
            st.markdown("**Interpretación rápida**")
            msgs=[]
            if temp_compliance<100: msgs.append(f"Temp. fuera de criterio en {temp_out['registros']} registros.")
            if hum_compliance<100:  msgs.append(f"HR fuera de criterio en {hum_out['registros']} registros.")
            if temp_delta_ok is False: msgs.append("Δ Temperatura no cumple.")
            if hum_delta_ok  is False: msgs.append("Δ HR no cumple.")
            st.warning(" ".join(msgs)) if msgs else st.success("Todas las mediciones cumplen los límites.")
    else:
        # Gráfica de 3 niveles
        st.plotly_chart(
            build_plotly_chart_niveles(
                df_view, temp_low, temp_high,
                action_low, action_high,
                show_markers=show_markers),
            use_container_width=True)

        # Tabla de cumplimiento por nivel
        st.markdown("**Distribución por nivel**")
        c1,c2 = st.columns(2)
        with c1:
            nivel_df = pd.DataFrame({
                "Nivel":    ["🟢 Seguro","🟡 Alerta","🔴 Acción"],
                "Registros":[n_seguro, n_alerta_reg, n_accion_reg],
                "% del total":[round(pct_seguro,2), round(pct_alerta,2), round(pct_accion,2)],
            })
            st.dataframe(nivel_df, use_container_width=True, hide_index=True)
        with c2:
            st.markdown("**Referencia de límites**")
            ref_df = pd.DataFrame({
                "Parámetro":["Seguro (Innova Eats)","Alerta","Acción"],
                "Rango":[
                    f"{temp_low} – {temp_high} °C",
                    f"{action_low} – {temp_low} °C  |  {temp_high} – {action_high} °C",
                    f"< {action_low} °C  |  > {action_high} °C",
                ]
            })
            st.dataframe(ref_df, use_container_width=True, hide_index=True)

# ---- TAB 2: Eventos ----
with tab2:
    if tiene_humedad:
        c1,c2 = st.columns(2)
        with c1:
            st.markdown("**Temperatura fuera de rango**")
            if events_temp.empty: st.success("Sin eventos.")
            else: st.dataframe(events_temp, use_container_width=True)
        with c2:
            st.markdown("**Humedad fuera de rango**")
            if events_hum.empty: st.success("Sin eventos.")
            else: st.dataframe(events_hum, use_container_width=True)
        st.markdown("**Todos los eventos**")
        if events_df.empty: st.info("No hay eventos.")
        else: st.dataframe(events_df, use_container_width=True)
    else:
        c1,c2 = st.columns(2)
        with c1:
            st.markdown("### 🟡 Eventos de ALERTA")
            st.caption(f"Temperatura entre {action_low}°C y {temp_low}°C, o entre {temp_high}°C y {action_high}°C")
            if events_alerta.empty:
                st.success("No se detectaron eventos de alerta.")
            else:
                st.dataframe(events_alerta, use_container_width=True)
        with c2:
            st.markdown("### 🔴 Eventos de ACCIÓN")
            st.caption(f"Temperatura < {action_low}°C  o  > {action_high}°C")
            if events_accion.empty:
                st.success("No se detectaron eventos de acción.")
            else:
                st.dataframe(events_accion, use_container_width=True)

# ---- TAB 3: Datos ----
with tab3:
    if show_raw:
        st.markdown("**Datos originales**")
        st.dataframe(df_raw, use_container_width=True)
    if show_processed:
        st.markdown("**Datos procesados**")
        st.dataframe(processed_export, use_container_width=True)

# ---- TAB 4: Descargas ----
with tab4:
    if tiene_humedad:
        fig_temp_static = build_matplotlib_chart(df_view,"Temperatura",
            "Temperatura","Temperatura (°C)",temp_low,temp_high)
        fig_hum_static  = build_matplotlib_chart(df_view,"Humedad",
            "Humedad relativa","Humedad relativa (%)",hum_low,hum_high)
        pdf_bytes = generate_pdf_report_sensorpush(
            df_metrics, fig_temp_static, fig_hum_static, events_df,
            (temp_low,temp_high),(hum_low,hum_high),
            temp_compliance,hum_compliance,
            delta_temp,delta_hum,temp_delta_ok,hum_delta_ok)
        temp_png = fig_to_bytes(fig_temp_static)
        hum_png  = fig_to_bytes(fig_hum_static)
    else:
        fig_temp_static = build_matplotlib_chart_niveles(
            df_view, temp_low, temp_high,
            action_low, action_high)
        pdf_bytes = generate_pdf_report_solo_temp(
            df_metrics, fig_temp_static,
            events_alerta, events_accion,
            temp_low, temp_high, action_low, action_high,
            temp_compliance, len(events_alerta), len(events_accion),
            delta_temp, temp_delta_ok)
        temp_png = fig_to_bytes(fig_temp_static)
        hum_png  = None

    csv_bytes   = processed_export.to_csv(index=False).encode("utf-8-sig")
    excel_bytes = dataframe_to_excel_bytes(processed_export)

    d1,d2 = st.columns(2)
    with d1:
        st.download_button("📥 Gráfico temperatura (PNG)", data=temp_png,
            file_name=f"{base_filename}_grafico_temperatura.png",
            mime="image/png", use_container_width=True)
        st.download_button("📥 Datos procesados (CSV)", data=csv_bytes,
            file_name=f"{base_filename}_datos_procesados.csv",
            mime="text/csv", use_container_width=True)
        st.download_button("📥 Datos procesados (Excel)", data=excel_bytes,
            file_name=f"{base_filename}_datos_procesados.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True)
    with d2:
        if hum_png:
            st.download_button("📥 Gráfico humedad (PNG)", data=hum_png,
                file_name=f"{base_filename}_grafico_humedad.png",
                mime="image/png", use_container_width=True)
        st.download_button("📥 Reporte PDF", data=pdf_bytes,
            file_name=f"{base_filename}_reporte.pdf",
            mime="application/pdf", use_container_width=True)

# ---- TAB 5: Diagnóstico ----
with tab5:
    sampling = compute_sampling_minutes(df)
    st.markdown("**Diagnóstico del archivo**")
    diag = {
        "Modo": "SensorPush" if tiene_humedad else "Solo Temperatura",
        "Registros originales": len(df_raw),
        "Registros válidos en periodo": len(df),
        "Registros en resumen ejecutivo": len(df_metrics),
        "Registros tras agrupación": len(df_view),
        "Paso de muestreo estimado (min)": round(sampling,2) if sampling else None,
        "% temp. fuera de rango seguro": round(temp_out["porcentaje_registros"],2),
        "Min. estimados fuera de rango": round(temp_out["minutos_estimados"],1),
        "Δ Temperatura (°C)": round(delta_temp,2) if delta_temp is not None else None,
        "Cumple Δ Temperatura (≤2°C)": temp_delta_ok,
    }
    if not tiene_humedad:
        diag.update({
            "% registros Seguro":  round(pct_seguro, 2),
            "% registros Alerta":  round(pct_alerta, 2),
            "% registros Acción":  round(pct_accion, 2),
            "Eventos de alerta":   len(events_alerta),
            "Eventos de acción":   len(events_accion),
        })
    else:
        diag.update({
            "% HR fuera de rango": round(hum_out["porcentaje_registros"],2),
            "Δ HR (%)": round(delta_hum,2) if delta_hum is not None else None,
            "Cumple Δ HR (≤5%)": hum_delta_ok,
        })
    st.write(diag)
