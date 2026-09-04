# Lab DataLogger Monitor v10

Dashboard para monitoreo de temperatura, humedad y cadena de frío, con soporte multi-formato para distintos equipos de datalogging.

## Modos y formatos soportados

**Temperatura + Humedad**
- SensorPush (CSV / Excel / ZIP) — detección automática de columnas
- Datalogger ambiental del laboratorio nuevo ("Test Report" con cabecera de metadatos y tabla NO/Temp/RH/TIME; suele llegar con extensión `.xls` aunque el contenido es texto plano)

**Solo Temperatura**
- Nevera clásica (CSV separador `;`, sin encabezados, decimal coma)
- Nevera multi-sonda del laboratorio nuevo (log tabulado con `T cham`, `T1-T3`, `Compr.1/2`, `Room temp`, `Evap temp`, `door`...; usa `T cham` como temperatura principal y conserva `Room temp` como referencia)

## Incluye
- Presets de equipo/ambiente (Innova Eats / Global Bild y Laboratorio BD&BE) que autocompletan límites de control
- Sistema de 3 niveles FAO (Seguro / Alerta / Acción) para neveras — activable/desactivable por preset; o rango de control simple (dentro/fuera de rango) para equipos que no lo requieren
- Criterio Δ (variación máxima: Temp ≤2 °C / HR ≤5 %) — también activable/desactivable por preset
- Gráficas interactivas con Plotly, con banda verde de rango aceptable y marcado de puntos fuera de rango
- Selector ambas variables / solo temperatura / solo humedad
- Filtro por fechas y agrupación por intervalo
- KPIs de cumplimiento, eventos fuera de rango y panel de diagnóstico del archivo
- Exportación de gráficos (PNG), datos procesados (CSV / Excel) y reporte (PDF)

## Ejecutar localmente
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Publicación gratis
Sube `app.py`, `requirements.txt` y `README.md` a GitHub y publícala en Streamlit Community Cloud.
