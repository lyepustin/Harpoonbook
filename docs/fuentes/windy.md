# Windy

## Qué es

Previsión meteorológica y marina por coordenadas. Usamos el endpoint público de Windy (mismo origen que la web), sin API key.

- **Código:** `harpoonbook/scrapers/windy.py`
- **URL:** `https://node.windy.com/forecast/{model}/{lat}/{lon}`
- **Modelos:** `ecmwf` (viento/temp) + `waves` (olas/swell)

## Alcance geográfico

Se consulta **por sitio de pesca** (lat/lon de cada spot). Todos los spots pueden compartir el mismo día pero con datos distintos si las coordenadas difieren.

## Horizonte temporal

Una sola petición al endpoint ya trae ~6 días. En cada captura guardamos:

| Día | Qué se guarda |
|-----|----------------|
| **Hoy** | Snapshot completo (Windy + solunar/marea + corrientes + óptica) |
| **Hoy+1 … Hoy+3** | Snapshot de **previsión** completo: Windy + corrientes Open-Meteo + óptica Copernicus + solunar/marea tablademareas |

Si al día siguiente vuelves a capturar, esos días futuros se **actualizan** con la previsión más reciente (nuevo snapshot; el merge del día usa la captura más fresca por franja).

Constante: `WINDY_LOOKAHEAD_DAYS = 3` (horizonte compartido por todas las fuentes de previsión).

En el dashboard puedes navegar a **Mañana / Pasado mañana / +3** con las flechas del selector de día, y a cada **franja de 3 h** con el slider de hora (como el timeline de Windy).

## Datos que extraemos

Por franjas de **3 horas** (horario local):

| Campo | Unidad | Uso |
|-------|--------|-----|
| `wind_speed_kmh` | km/h | Viento |
| `wind_gusts_kmh` | km/h | Rachas |
| `wind_direction` | brújula | Dirección del viento (de dónde viene) |
| `wind_direction_deg` | ° | Misma dirección en grados |
| `wave_height_m` | m | Oleaje total |
| `wave_direction` | brújula | Dirección olas |
| `wave_period_s` | s | Periodo olas |
| `swell1_height_m` | m | Altura swell (mar de fondo) |
| `swell1_period_s` | s | Periodo swell — **clave para visibilidad** |
| `swell1_direction` | brújula | De dónde viene el swell |
| `swell1_direction_deg` | ° | Misma dirección en grados |
| `wave_power_kw_m` | kW/m | Potencia de ola |
| `temp_c` | °C | Temperatura del aire |
| `water_temp_c` | °C | Temperatura del agua (cabecera del modelo) |

## Para qué se usa

1. **Dashboard:** selector de **día + hora** (franjas de 3 h). La foto del sitio muestra tres flechas estilo picker de Windy, ancladas al **centro de la imagen** (norte arriba):
   - **Viento** (cian): de dónde viene (el mismo ángulo que la barra del picker).
   - **Swell** (naranja): de dónde viene el mar de fondo (sin invertir).
   - **Corriente** (verde, en lugar de Swell 2): Open-Meteo, hacia dónde fluye.
   
   El bloque resumen, la visibilidad de orilla y las tablas siguen la franja elegida (fila resaltada).
2. **Previsión de visibilidad:** el periodo y altura de swell, el oleaje y el viento alimentan las penalizaciones en `visibility.py`.
3. **Historial:** cada snapshot guarda la previsión del día de captura; los días futuros se van refinando en capturas posteriores.

## Limitaciones

- No incluye corrientes ni turbidez (Windy las muestra en la web pero no en este endpoint gratuito). La flecha de corriente del mapa sale de Open-Meteo.
- Resolución de modelo (~km); en calas estrechas la realidad puede diferir.
- Franjas de 3 h, no horarias.
- En costa, ECMWF a veces resuelve una **celda de tierra** (elevación > 0) con viento/temp más altos que el picker de la web. Pedimos celda de mar (y si hace falta desplazamos según `coast_facing`).
- Los snapshots de previsión (+1…+3) incluyen Windy, corrientes, óptica y solunar/marea; se refrescan cada vez que capturas “hoy”.
