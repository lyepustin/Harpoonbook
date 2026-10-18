# Copernicus Marine (optics / claridad)

## Qué es

Modelo biogeoquímico del Mediterráneo. Usamos el coeficiente de atenuación de luz (**Kd490**) para estimar la claridad base del agua.

- **Código:** `harpoonbook/scrapers/copernicus_optics.py`
- **Dataset:** `cmems_mod_med_bgc-optics_anfc_4.2km_P1D-m`
- **Variable:** `kd490` (1/m)

## Requisitos

Misma cuenta Copernicus Marine (`copernicusmarine login`). Solo se usa para claridad del agua; las corrientes vienen de Open-Meteo.

## Horizonte temporal

El dataset BGC es diario (`P1D`). En cada captura se descarga **un** NetCDF de hoy + 3 días y se reparte por fecha en los snapshots de previsión.

## Datos que extraemos

| Campo | Unidad | Descripción |
|-------|--------|-------------|
| `kd490` | 1/m | Atenuación de luz a 490 nm |
| `secchi_est_m` | m | Profundidad Secchi estimada: `1.7 / kd490` |
| `secchi_day_mean_m` | m | Media del día en el punto |

## Ajuste costero

El Kd490 del modelo representa mar abierto (~4 km). Antes de usarlo en visibilidad aplicamos:

```
secchi_costa = min(15 m, secchi_modelo × 0.2)
```

Ver detalle en [logica/prevision-visibilidad.md](../logica/prevision-visibilidad.md).

## Para qué se usa

**Previsión de visibilidad:** aporta la claridad base del medio antes de penalizar por swell, viento y marea (hoy e histórico; también días de previsión +1…+3).

No se muestra como tabla propia; aparece como métrica **Secchi base** en el bloque de visibilidad.

## Limitaciones

- Satélite/modelo de gran escala; no mide turbidez local en cala.
- Valores de mar abierto son muy altos (~50 m Secchi); el ajuste costero es obligatorio.
- Sin credenciales Copernicus, la previsión usa solo la heurística física (sin Secchi base).
