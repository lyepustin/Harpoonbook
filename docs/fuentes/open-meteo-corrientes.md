# Open-Meteo Marine (corrientes)

## Qué es

API gratuita de condiciones marinas, sin cuenta.

- **Código:** `harpoonbook/scrapers/open_meteo_marine.py`
- **URL:** `https://marine-api.open-meteo.com/v1/marine`

## Alcance geográfico

Por coordenadas de **cada sitio de pesca**.

## Horizonte temporal

Open-Meteo acepta `start_date` / `end_date`. En cada captura pedimos **hoy + 3 días** en una sola petición y guardamos un bloque por día (`open_meteo_by_date` → snapshots de previsión).

## Datos que extraemos

Por franjas **horarias**:

| Campo | Unidad | Descripción |
|-------|--------|-------------|
| `current_speed_kmh` | km/h | Velocidad de corriente |
| `current_speed_kn` | nudos | Misma velocidad en nudos |
| `current_direction_deg` | ° | Hacia dónde va la corriente |
| `current_direction` | brújula | Dirección en texto |

## Para qué se usa

1. **Dashboard:** métrica de corriente (hoy e histórico; también previsión +1…+3).
2. **Comparación** con lo que notáis en el agua.
3. **Historial:** guardado en `raw.currents.open_meteo` del snapshot.

No alimenta directamente la previsión de visibilidad. Es la **única fuente de corrientes** del PoC (Copernicus corrientes fue eliminado por duplicar datos y añadir fricción).

## Limitaciones

- Resolución ~8 km. Open-Meteo avisa de **precisión limitada en costa**.
- No captura corrientes locales entre rocas (muy habituales en Cabo Roig).
- Modelo global; suele ser más optimista que la corriente real en cala.
- Horas con velocidad `0` se omiten (calma o celda costera sin señal útil).
- Si el spot tiene `coast_facing`, la consulta se desplaza ~1 celda mar adentro.
