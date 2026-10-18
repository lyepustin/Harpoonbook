# Documentación Harpoonbook

Cuaderno de pesca submarina: condiciones del mar, previsión de visibilidad e historial de salidas.

## Índice

### Fuentes de datos

| Fuente | Archivo | Para qué sirve |
|--------|---------|----------------|
| Windy | [fuentes/windy.md](fuentes/windy.md) | Viento, olas, swell, temperatura del agua |
| tablademareas | [fuentes/tablademeareas.md](fuentes/tablademeareas.md) | Solunar y marea (única referencia Cabo Roig, compartida por todos los spots) |
| Open-Meteo Marine | [fuentes/open-meteo-corrientes.md](fuentes/open-meteo-corrientes.md) | Corrientes modeladas (global) |
| Open-Meteo Forecast | [fuentes/open-meteo-lluvia.md](fuentes/open-meteo-lluvia.md) | Precipitación diaria (escorrentía / orilla) |
| Copernicus optics | [fuentes/copernicus-optics.md](fuentes/copernicus-optics.md) | Claridad del agua (Kd490 → Secchi) |

### Lógica interna

| Tema | Archivo |
|------|---------|
| Captura y snapshots | [logica/captura-y-snapshots.md](logica/captura-y-snapshots.md) |
| Previsión de visibilidad | [logica/prevision-visibilidad.md](logica/prevision-visibilidad.md) |

### Datos del grupo

| Tema | Archivo |
|------|---------|
| Sitios, especies, salidas y BD | [datos-locales/spots-y-logs.md](datos-locales/spots-y-logs.md) |

## Flujo resumido

```
Captura automatica (1-2x/dia), "Actualizar" o "Capturar todo" en la app
    → tablademareas 1 vez (solunar/marea Cabo Roig, compartido)
    → por spot: Windy + Open-Meteo (corrientes + lluvia) + Copernicus optics
    → snapshot en SQLite (data/harpoonbook.db)
    → dashboard: ficha/foto + bloque "Ahora" (visibilidad/marea/entrada/mar) + detalle en expanders
    → Registro de salida: trip (agua/apneas) + N avistamientos
```

## Vistas de la app

| Vista | Para qué |
|-------|----------|
| Dashboard | Decisión del día: mapa/ficha, resumen Ahora, detalle colapsable e historial |
| Registro de salida | Crear/editar trips post-pesca |
| Sitios de pesca | CRUD spots, mapa BLOB, prompt ChatGPT, direcciones |
| Especies | CRUD del catálogo de especies |

## Captura automática

```bash
python -m harpoonbook
```

Programar en Windows (Tareas programadas) 1-2 veces al día, en el equipo con `copernicusmarine login` para conservar la claridad satelital.

## Código relevante

| Módulo | Rol |
|--------|-----|
| `harpoonbook/scrapers/` | Descarga de cada fuente |
| `harpoonbook/features/build_features.py` | Une todo en un snapshot |
| `harpoonbook/merge_snapshots.py` | Fusiona capturas del mismo día |
| `harpoonbook/visibility.py` | Previsión de visibilidad + memoria N dias |
| `harpoonbook/capture.py` | CLI de captura para todos los spots |
| `harpoonbook/ui/dashboard.py` | Vista principal (decisión + expanders) |
| `app.py` | Shell Streamlit (sidebar + routing) |
