# Sitios de pesca, especies y salidas

## Base de datos

- **Archivo:** `data/harpoonbook.db` (SQLite)
- **Código:** `harpoonbook/db.py`

## Sitios de pesca (`fishing_spots`)

| Campo | Descripción |
|-------|-------------|
| `id` | Identificador interno (ej. `cabo_roig_calas`) |
| `label` | Nombre visible |
| `lat`, `lon` | Coordenadas para Windy, corrientes y optics |
| `bottom_type` | arena / roca / mixto |
| `map_image_blob` + `map_image_mime` | Captura Google Maps/Earth (BLOB) |
| `profile_json` | Ficha del spot (fondo, exposición, `runoff_risk`, profundidad, resumen…) |
| `approach_directions_json` | Direcciones posibles de pesca (brújula + etiqueta) |
| `coast_facing` | Hacia dónde mira la costa (para leer corriente) |
| `active` | Si aparece en el dashboard |
| `notes` | Notas libres |

### Flujo de ficha con ChatGPT

1. Sube una captura del mapa (satélite) en **Sitios de pesca** (para el Dashboard).
2. Copia el prompt personalizado del sitio.
3. En ChatGPT adjunta **dos** imágenes del mismo encuadre: satélite + batimetría/hillshade (Plataforma).
4. Opcional: añade `ANCLA_PROFUNDIDAD: la zona más profunda visible ~12 m` (escala; no gris=metros).
5. Pega la respuesta → **Aplicar a ficha** y revisa direcciones / costa.

El prompt pide secciones extra (`ESTRUCTURA_FONDO`, `ENTRADAS`) que van a `profile_json.structure_notes` y `entry_notes`.

Prompt: `config.SPOT_ANALYSIS_PROMPT`. Parser: `harpoonbook/spot_profile.py`.

## Especies (`species`)

CRUD en la vista **Especies**.

| Campo | Descripción |
|-------|-------------|
| `id` | slug interno |
| `label` | Nombre visible |
| `category` | pez / crustáceo / cefalópodo / medusa / otro |
| `active` | Si aparece en el registro de salida |

Seed inicial: pulpo, mero, serviola, dentón, lubina, sargo, espetón, anjova, medusa, otro.

## Salidas (`fishing_trips` + `trip_sightings`)

Vista **Registro de salida**. Sustituye el antiguo formulario de `fishing_logs`.

### Cabecera del trip

Spot, método, inicio/fin, score, **dirección de pesca**, notas.

### Agua (editable + preset)

Al pulsar **Cargar preset desde snapshots** (o al guardar) se sugiere:

- temperatura del agua (Windy)
- visibilidad estimada (m)
- corriente velocidad/dirección (Open-Meteo)

Todo se puede corregir. Se guardan valores efectivos y `*_suggested` para calibrar después. Turbidez (clara/media/turbia) es manual.

Código: `harpoonbook/trip_presets.py`.

### Apneas

Sensación, promedio apnea (mm:ss), profundidad media, si costó compensar.

### Avistamientos (hasta 8)

Por cada uno: especie, nº vistas, veces vistas, si intentaste pescar, unidades, peso medio (g), calidad del tiro.

### Edición

En la misma vista: modo **Editar salida** → elige un trip → actualiza cabecera, agua, apneas y reemplaza avistamientos.

## Migración

Los `fishing_logs` antiguos se convierten a `fishing_trips` al arrancar (`migrate_legacy_logs_to_trips`), enlazados por `legacy_log_id`. La tabla `fishing_logs` se conserva; la UI ya no la usa.

## Métodos de pesca

| ID | Nombre |
|----|--------|
| `espera` | Espera |
| `acecho` | Acecho |
| `agujero` | Agujero |
| `cerca_rompientes` | Cerca de rompientes |
| `mar_adentro` | Mar adentro |

Definidos en `config.DEFAULT_METHODS`.
