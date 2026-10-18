# Previsión de visibilidad

**Código:** `harpoonbook/visibility.py`

Hay **dos lecturas** distintas:

| Ámbito | Qué mide | Fuentes |
|--------|----------|---------|
| **Orilla** (principal, UI "Ahora") | Visibilidad de pesca cerca de costa / calas | Secchi costa + swell/olas/potencia + viento de tierra + memoria N días + fondo + **lluvia/escorrentía** |
| **Mar abierto (~4 km)** | Claridad de masa de agua | Copernicus Kd490 → Secchi, **sin** turbidez local |

## Salida

```json
{
  "nearshore": { "label", "score", "meters_*", "reasons", ... },
  "offshore": { "secchi_m", "label", "note" },
  "summary": "<alias de nearshore>",
  "hourly": [ ... orilla por franja Windy ... ]
}
```

| Campo orilla | Ejemplo | Significado |
|--------------|---------|-------------|
| `label` | regular | muy buena / buena / regular / mala / muy mala |
| `score` | 4.2 | 0–10 |
| `meters_min/mid/max` | 3–5 m | Rango orientativo en orilla |
| `reasons` | ["Oleaje remueve arena cerca (0.4 m)"] | Factores |

## Capa 1 — Claridad base

1. Kd490 Copernicus → `secchi_open ≈ 1.7 / kd490` (mar abierto).
2. **Offshore:** se muestra ese Secchi tal cual (~4 km).
3. **Orilla:** `secchi_orilla = min(12, secchi_open × 0.18)`.

Sin Copernicus, la orilla usa solo la heurística física.

## Capa 2 — Factor físico de orilla (≈0.15–1.0)

Prioridad: periodo swell → altura swell → oleaje → **potencia de ola** → viento / de tierra → marea.

Umbrales pensados para costa poco profunda (2–10 m), no para mar abierto:

| Señal | Umbral orilla | Factor típico |
|-------|---------------|---------------|
| Periodo swell | ≥10 / ≥8 / ≥6 s | ×0.35 / 0.55 / 0.75 |
| Swell altura | ≥0.8 / ≥0.45 / ≥0.30 m | ×0.70 / 0.82 / 0.90 |
| Oleaje | ≥1.0 / ≥0.55 / ≥0.35 m | ×0.70 / 0.78 / 0.85 |
| Potencia | ≥1.5 / ≥0.5 / ≥0.2 kW/m | ×0.70 / 0.82 / 0.92 |
| Viento de tierra (alineado a `coast_facing`) | ≥16 km/h de cara | ×0.82–0.90 |
| Marea | cambio ≤2 h / sube-baja | ×0.90 / 0.95 |

## Capa 3 — Memoria de N días

Snapshots de los **3 días previos**. Además de mar de fondo fuerte, cuenta **días agitados** (olas ≥0.35 m, swell ≥0.30 m o potencia ≥0.25 kW/m):

| Condición | Factor |
|-----------|--------|
| ≥3 días agitados | ×0.82 |
| ≥2 días agitados | ×0.90 |
| Oleaje reciente ≥0.4 m | ×0.90 |

## Capa 4 — Tipo de fondo

| Tipo | Factor |
|------|--------|
| roca | ×1.08 |
| mixto | ×1.00 |
| arena | ×0.88 |

## Capa 5 — Escorrentía (lluvia + barrancos)

Solo **orilla**. Fuente: Open-Meteo Forecast (`precipitation`) + `profile.runoff_risk` del spot.

1. Precipitación diaria de los últimos 3 días + hoy, **ponderada** por antigüedad:

| Antigüedad | Peso |
|------------|------|
| Hoy (día 0) | ×1.0 |
| Ayer | ×0.7 |
| −2 días | ×0.4 |
| −3 días | ×0.2 |

2. Umbrales sobre mm ponderados (≈ l/m²):

| Mm ponderados | Factor base | Etiqueta |
|---------------|-------------|----------|
| ≥ 70 | ×0.30 | lluvia muy fuerte |
| ≥ 40 | ×0.55 | lluvia fuerte |
| ≥ 20 | ×0.85 | lluvia moderada |
| < 20 | ×1.00 | sin efecto |

3. El riesgo del spot atenúa la penalización: `factor = 1 − (1 − base) × peso_riesgo`.

| `runoff_risk` | Peso | Uso típico |
|---------------|------|------------|
| alto | 1.00 | Calas con barrancos / drenaje urbano |
| medio | 0.65 | Por defecto |
| bajo | 0.35 | Costa sin desembocaduras cercanas |

4. Si hay oleaje ≥ 0.35 m el mismo slot y ya hay penalización: ×0.85 extra (remueve barro depositado).

Reasons de ejemplo: `Lluvia fuerte reciente (~79 mm ponderados) — escorrentía (riesgo alto)`.

## Etiquetas de score (orilla)

| Score | Etiqueta |
|-------|----------|
| ≥ 8 | muy buena |
| ≥ 6 | buena |
| ≥ 4 | regular |
| ≥ 2 | mala |
| < 2 | muy mala |

## Limitaciones

- Copernicus no ve turbidez de playa/calas (webcam / log post-salida siguen siendo verdad de campo).
- Upwelling por orientación: solo se usa alineación viento/swell vs `coast_facing`, no circulación completa.
- La lluvia es modelo ~9 km; no sustituye observación local tras un chubasco puntual.
- Calibración futura con logs históricos por sitio (turbidez manual vs mm).
