# Open-Meteo Forecast (precipitación)

## Qué es

API gratuita de previsión atmosférica (no marina).

- **Código:** `harpoonbook/scrapers/open_meteo_weather.py`
- **URL:** `https://api.open-meteo.com/v1/forecast`

## Alcance geográfico

Por coordenadas de **cada sitio de pesca** (misma lat/lon que Windy).

## Horizonte temporal

En cada captura pedimos **3 días atrás + hoy + 3 de previsión** (`start_date` / `end_date`) y guardamos la serie diaria en `raw.precipitation`.

## Datos que extraemos

| Campo | Unidad | Descripción |
|-------|--------|-------------|
| `precip_mm` | mm (= l/m²) | Acumulado diario `precipitation_sum` |
| `daily[]` | — | Serie `{date, precip_mm}` para la ventana |

## Para qué se usa

**Previsión de visibilidad de orilla:** lluvia reciente + riesgo de escorrentía del spot (`runoff_risk`) penalizan turbidez por barrancos / drenaje urbano.

No afecta al Secchi de mar abierto (~4 km).

Ver [logica/prevision-visibilidad.md](../logica/prevision-visibilidad.md).

## Limitaciones

- Modelo ~9 km; no ve chubascos locales de un solo barranco.
- No distingue suelo saturado vs seco (solo acumula mm).
- Hace falta una captura nueva para tener precipitación en snapshots antiguos.
