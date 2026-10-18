# tablademeareas (Cabo Roig)

## Qué es

Scraping de la página de mareas y solunar de [tablademareas.com](https://tablademareas.com/es/alicante/cabo-roig).

- **Código:** `harpoonbook/scrapers/tablademeareas.py`, `harpoonbook/scrapers/solunar.py`
- **Referencia fija:** Cabo Roig (`config.SOLUNAR_LOCATION`)

## Alcance geográfico

**Una sola referencia costera** para todo el grupo. Solunar y marea se capturan una vez por actualización y se comparten entre todos los spots.

Los spots de Campoamor o Punta Glea usan la misma marea/solunar de Cabo Roig. Si en el futuro hace falta otra referencia, habría que parametrizarla por zona.

## Datos que extraemos

### Solunar (hoy)

| Campo | Uso |
|-------|-----|
| `activity_day` | Actividad del día (ALTA / MEDIA / BAJA) |
| `best_hour` | Mejor hora de entrada del día (dentro de amanecer–ocaso) |
| `best_exit` | Mejor hora de salida (fin de la franja diurna útil) |
| `best_windows` | Franjas diurnas prioritarias (majors) con `start`/`end`, `entry_time`, `exit_time` |
| `night_windows` | Franjas solunares nocturnas (informativas; no se proponen para entrar) |
| `periods` | Todos los periodos (majors + minors) |
| `phase`, `illumination_pct` | Fase lunar e iluminación |
| `sunrise_time`, `sunset_time` | Salida y puesta de sol |

### Lookahead (hoy+1 … hoy+3)

En cada captura se hace POST `fecha=YYYY-MM-DD` por cada día siguiente (mismo horizonte que Windy) y se guarda:

```json
"solunar_lookahead": [
  {"date": "2026-08-08", "solunar": { "..."}, "tide": { "tide_events": [...], "next_tide_event": "..." }},
  {"date": "2026-08-09", "solunar": { "..."}, "tide": { "..."}},
  {"date": "2026-08-10", "solunar": { "..."}, "tide": { "..."}}
]
```

Persiste en `raw_json` y alimenta los snapshots de previsión de esos días. En días futuros no hay “estado actual” de marea live (el gráfico se carga por JS).
Las horas de pleamar/bajamar salen de la **tabla mensual** (`#tabla_mareas`) del GET de hoy.
Si el horizonte cruza de mes, esos días pueden quedar sin calendario de marea hasta el mes siguiente.

### Próxima franja útil (dashboard)

En vista **Hoy** no se muestra “la mejor del día” si ya pasó. Se calcula en lectura:

1. Todas las franjas **diurnas** de hoy (majors **y** minors).
2. Se descartan las que ya terminaron o les quedan menos de ~20 min.
3. Entre las restantes: calidad (major/alta antes que media) y, a igualdad, la más cercana.
4. Si no queda ninguna hoy → mejor franja diurna de **mañana** (`solunar_lookahead`).

Campos derivados en UI: `next_window`, `upcoming_windows` (`refine_solunar_for_now`).

En vista **histórica** se mantiene “mejor del día” sin filtrar por hora actual.

### Marea

| Campo | Uso |
|-------|-----|
| `tide_direction` | `rising` (subiendo) o `falling` (bajando) |
| `next_tide_event` | `pleamar` o `bajamar` |
| `hours_to_next_tide_event` | Horas hasta el próximo cambio |

## Para qué se usa

1. **Dashboard:** bloque Solunar con **próxima** franja (hoy o mañana) y actividad del día.
2. **Previsión de visibilidad:** marea subiendo/bajando y proximidad al cambio de marea penalizan la visibilidad estimada.
3. **Historial:** guardado en cada snapshot (hoy + lookahead).

## Cómo se obtiene el día siguiente

tablademareas no acepta `?fecha=` en GET. El calendario envía un **POST** al mismo path con el campo oculto `fecha=YYYY-MM-DD`. El scraper reutiliza esa petición tras un GET inicial (cookies/`PHPSESSID`).

## Limitaciones

- Scraping HTML: si cambia la web, puede romperse.
- No da amplitud de marea en metros ni tabla horaria completa.
- Referencia única (Cabo Roig), no por spot.
- Lookahead de **1 día** (mañana); no más adelante.
