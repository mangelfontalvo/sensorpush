# SensorPush Pro v4

Dashboard avanzado para monitoreo de temperatura y humedad.

## Incluye
- carga de CSV o Excel
- detección automática de columnas
- gráficas interactivas con Plotly
- selector: ambas variables / solo temperatura / solo humedad
- sombreado visual del rango aceptable
- KPI de cumplimiento por variable
- filtro por fechas
- agrupación por intervalo
- eventos fuera de rango
- exportación de PNG, CSV y PDF

## Ejecutar localmente
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Publicación gratis
Sube `app.py`, `requirements.txt` y `README.md` a GitHub y publícala en Streamlit Community Cloud.


## Ajuste adicional
- resumen ejecutivo con Δ temperatura (criterio ≤ 2 °C)
- resumen ejecutivo con Δ humedad relativa (criterio ≤ 5 % HR)
