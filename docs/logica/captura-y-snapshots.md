# Captura y snapshots

## Flujo

```
Captura automatica (python -m harpoonbook) o usuario pulsa "Actualizar (N sitios)" / "Solo {sitio}"
    → get_shared_features()         # tablademeareas hoy + solunar/marea +1…+3
    → por cada spot activo:
        → get_features_for_spot()   # Windy/corrientes/óptica/lluvia hoy+3 (coords del spot)
        → save_condition_snapshot(hoy)
        → save previsiones completas (hoy+1 … hoy+3) como snapshots is_forecast
    → merge_snapshots_for_day()     # vista fusionada del dia
    → app.py muestra el resultado
```

**Codigo:** `harpoonbook/conditions.py`, `harpoonbook/features/build_features.py`, `harpoonbook/capture.py`, `harpoonbook/bg_capture.py`

## Compartido vs por spot

| Dato | Alcance | Fuente |
|------|---------|--------|
| Solunar / marea | **Una vez** por captura; hoy + 3 días (POST fecha) | tablademareas Cabo Roig |
| Windy (viento, olas, swell, temp. agua) | Por spot; **hoy + 3 días** en una petición | Windy |
| Corrientes | Por spot; **hoy + 3 días** en una petición | Open-Meteo Marine |
| Precipitación | Por spot; **3 días atrás + hoy + 3** | Open-Meteo Forecast |
| Optica / claridad | Por spot; **hoy + 3 días** en un NetCDF | Copernicus |

En `capture_all_spots` (boton **Actualizar**, auto-refresco al abrir hoy, o `python -m harpoonbook`) se scrapea solunar/marea **una vez** (con lookahead en paralelo) y ese bloque se reutiliza en el snapshot de cada spot. Los spots y las fuentes por spot (Windy ‖ Open-Meteo ‖ óptica) van en paralelo (`CAPTURE_MAX_WORKERS = 5`); las escrituras SQLite se serializan.

Detalle: [fuentes/tablademeareas.md](../fuentes/tablademeareas.md), [fuentes/windy.md](../fuentes/windy.md).

## Que es un snapshot

Cada captura guarda en `condition_snapshots`:

| Bloque | Contenido |
|--------|-----------|
| `solunar_json` | Solunar del dia |
| `windy_json` | Viento y olas |
| `tide_json` | Marea resumida |
| `raw_json` | Todo lo anterior + corrientes + optics + precipitación + metadatos |

Los snapshots **no se borran**. Varias capturas el mismo dia se fusionan.

Los días **futuros** (hoy+1…+3) se guardan como snapshots de previsión (`raw.is_forecast`) con Windy + corrientes + óptica + precipitación + solunar/marea; al volver a capturar “hoy”, se añaden nuevas previsiones que el merge trata como capturas más recientes.

## Fusion de capturas del mismo dia

**Codigo:** `harpoonbook/merge_snapshots.py`

| Dato | Estrategia |
|------|------------|
| Windy (3 h) | Por franja horaria: la captura mas reciente cuya hora sea anterior al fin de esa franja |
| Corrientes Open-Meteo (1 h) | Igual, por hora |
| Optics (1 h) | Igual, por hora |
| Precipitación (diaria) | Del snapshot mas reciente con serie `daily` |
| Solunar / marea | Del snapshot mas reciente |

Asi una captura a las 10:00 mejora las franjas de la manana sin pisar las de la tarde si luego capturas a las 17:00. Las previsiones de mañana se van refinando cada vez que capturas hoy.

## Cuando se captura

| Accion | Comportamiento |
|--------|----------------|
| Tarea programada / `python -m harpoonbook` | Captura todos los spots activos (recomendado 1-2x/dia) |
| Abrir hoy | Auto-captura **de todos** los sitios activos si hay hueco (≥6 h) y quedan rondas (máx. 3/día) |
| "Actualizar (N sitios)" | Fuerza captura de todos (hoy + previsiones +1…+3) |
| "Solo {sitio}" | Fuerza captura solo del sitio abierto (incluye +3 días) |
| Registrar salida | Enlaza el log al snapshot fusionado de hoy |

## Vista historica y previsión

- **Pasado:** snapshots guardados ese día, fusionados. No se vuelve a llamar a las APIs.
- **Futuro (hasta +3):** previsión completa guardada al capturar hoy. Badge “Previsión (+días)”.

Los snapshots de dias previos alimentan el **contexto de N dias** en la prevision de visibilidad.

## Prevision de visibilidad

Se calcula **al mostrar** el dashboard (`forecast_visibility(features, recent_context=...)`), no se guarda en BD. Usa el snapshot fusionado del dia seleccionado mas la memoria de los 3 dias anteriores.
