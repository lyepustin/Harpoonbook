from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


LOCAL_TZ = ZoneInfo("Europe/Madrid")

# Solunar y marea: siempre tablademareas Cabo Roig (independiente del spot Windy).
SOLUNAR_LOCATION = {"label": "Cabo Roig (tablademareas)", "lat": 37.913, "lon": -0.717}

# Hilos para captura (fuentes por spot y spots en paralelo).
CAPTURE_MAX_WORKERS = 5
# Auto-captura al abrir hoy: pocas rondas, espaciadas (los botones fuerzan).
MAX_CAPTURES_PER_DAY = 3
MIN_CAPTURE_INTERVAL_HOURS = 6.0

# Valores iniciales si la BD de spots está vacía.
SEED_SPOTS: dict[str, dict[str, float | str]] = {
    "cabo_roig_calas": {"label": "Cabo Roig (calas)", "lat": 37.907, "lon": -0.717},
    "cabo_roig_espigon_puerto": {"label": "Espigón puerto Cabo Roig", "lat": 37.905, "lon": -0.720},
    "punta_glea": {"label": "Punta de la Glea", "lat": 37.892, "lon": -0.708},
    "campoamor_espigon_puerto": {"label": "Espigón puerto Campoamor", "lat": 37.878, "lon": -0.738},
}

DEFAULT_SPOTS = SEED_SPOTS

DEFAULT_METHODS = {
    "espera": "Espera",
    "acecho": "Acecho",
    "agujero": "Agujero",
    "cerca_rompientes": "Cerca de rompientes",
    "mar_adentro": "Mar adentro",
}


def parse_method_ids(raw: str | None) -> list[str]:
    """Acepta un id o varios separados por coma (legacy + multi-método)."""
    if not raw:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def join_method_ids(ids: list[str]) -> str:
    return ",".join(ids)


def format_methods(raw: str | None) -> str:
    ids = parse_method_ids(raw)
    if not ids:
        return "—"
    return " · ".join(DEFAULT_METHODS.get(mid, mid) for mid in ids)

BOTTOM_TYPES = {
    "arena": "Arena (más turbidez)",
    "roca": "Roca (protege del remolino)",
    "mixto": "Mixto",
}

EXPOSURE_TYPES = {
    "abierta": "Abierta",
    "resguardada": "Resguardada",
    "mixto": "Mixto",
}

# Escorrentía / barrancos → turbidez de orilla tras lluvia.
RUNOFF_RISK_TYPES = {
    "alto": "Alto (barrancos / drenaje a calas)",
    "medio": "Medio",
    "bajo": "Bajo (poca escorrentía)",
}

COMPASS_DIRECTIONS = {
    "N": "Norte",
    "NE": "Noreste",
    "E": "Este",
    "SE": "Sureste",
    "S": "Sur",
    "SO": "Suroeste",
    "O": "Oeste",
    "NO": "Noroeste",
}

# Rosa de 16 rumbos en español (W→O, SW→SO, NW→NO).
COMPASS_16_ES = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO",
]

_EN_TO_ES_COMPASS = {
    "SSW": "SSO",
    "SW": "SO",
    "WSW": "OSO",
    "W": "O",
    "WNW": "ONO",
    "NW": "NO",
    "NNW": "NNO",
}


def compass_es(label: str | None) -> str | None:
    """Normaliza abreviatura cardinal a español (W→O, SW→SO, …).

    Si el valor es un rumbo puro, lo normaliza. Texto libre no se altera.
    """
    if label is None:
        return None
    text = str(label).strip()
    if not text or text == "—":
        return label
    upper = text.upper()
    if upper in _EN_TO_ES_COMPASS:
        return _EN_TO_ES_COMPASS[upper]
    if upper in COMPASS_16_ES or upper in COMPASS_DIRECTIONS:
        return upper
    return text


def rewrite_compass_es(text: str | None) -> str | None:
    """Sustituye rumbos ingleses embebidos en texto libre (W→O, SW→SO, …)."""
    if text is None:
        return None
    out = str(text)
    for en, es in sorted(_EN_TO_ES_COMPASS.items(), key=lambda kv: len(kv[0]), reverse=True):
        out = re.sub(rf"\b{re.escape(en)}\b", es, out, flags=re.IGNORECASE)
    return out

SPECIES_CATEGORIES = {
    "pez": "Pez",
    "crustaceo": "Crustáceo",
    "cefalopodo": "Cefalópodo",
    "medusa": "Medusa",
    "otro": "Otro",
}

SEED_SPECIES: list[dict[str, str]] = [
    {"id": "pulpo", "label": "Pulpo", "category": "cefalopodo"},
    {"id": "mero", "label": "Mero", "category": "pez"},
    {"id": "serviola", "label": "Serviola", "category": "pez"},
    {"id": "denton", "label": "Dentón", "category": "pez"},
    {"id": "lubina", "label": "Lubina", "category": "pez"},
    {"id": "sargo", "label": "Sargo", "category": "pez"},
    {"id": "lisa", "label": "Lisa", "category": "pez"},
    {"id": "espeton", "label": "Espetón", "category": "pez"},
    {"id": "anjova", "label": "Anjova", "category": "pez"},
    {"id": "medusa", "label": "Medusa", "category": "medusa"},
    {"id": "otro", "label": "Otro", "category": "otro"},
]

# Alias legacy (lista de labels)
DEFAULT_SPECIES = [s["label"].lower() for s in SEED_SPECIES]

TURBIDITY_LEVELS = {
    "clara": "Clara",
    "media": "Media",
    "turbia": "Turbia",
}

APNEA_FEEL = {
    "mal": "Mal",
    "regular": "Regular",
    "bien": "Bien",
    "muy_bien": "Muy bien",
}

APNEA_EQUALIZE = {
    "no": "No",
    "si_otitis": "Sí, otitis",
    "si_otro": "Sí, otro",
}

SHOT_QUALITY = {
    "n_a": "N/A (no disparé)",
    "perfecto_espina": "Perfecto a la espina",
    "justo_tripa": "Justo / tripa",
    "desgarro": "Desgarro",
    "fallido": "Fallido",
}

# Calidad de tiro que implica captura (unidades/peso tienen sentido).
SHOT_WITH_CATCH = frozenset({"perfecto_espina", "justo_tripa", "desgarro"})

# Duraciones típicas de salida (minutos) para el selector rápido.
TRIP_DURATION_MINUTES = [
    (90, "1 h 30"),
    (120, "2 h"),
    (150, "2 h 30"),
    (180, "3 h"),
    (210, "3 h 30"),
    (240, "4 h"),
    (300, "5 h"),
]

MAX_SIGHTINGS_PER_TRIP = 8

EMPTY_SPOT_PROFILE: dict[str, object] = {
    "bottom_type": "mixto",
    "bottom_detail": "",
    "exposure": "mixto",
    "runoff_risk": "medio",
    "depth_min_m": None,
    "depth_max_m": None,
    "landmarks": "",
    "structure_notes": "",
    "entry_notes": "",
    "local_current_notes": "",
    "summary": "",
}

SPOT_ANALYSIS_PROMPT = """Analiza este spot de pesca submarina en la costa de Alicante / Murcia (España).

Te adjunto DOS imágenes del MISMO sitio (mismo encuadre aproximado):
1) SATÉLITE / Google Maps o Earth — orilla, calas, espigones, puertos.
2) BATIMETRÍA / hillshade (Plataforma) — relieve del fondo.

Cómo usarlas:
- Satélite: orientación, exposición, entradas, referencias.
- Hillshade: estructura del fondo. Lo oscuro suele ser SOMBRA de pendiente, NO profundidad.
- Si indico ANCLA_PROFUNDIDAD, úsala solo como escala orientativa; no conviertas gris en metros.

Responde en español, SOLO con estas secciones (cabecera exacta + dos puntos). Sé breve: sin párrafos largos ni repetir el mismo dato en otra sección.

TIPO_DE_FONDO: arena | roca | mixto — media línea de detalle
ESTRUCTURA_FONDO: 1 frase del hillshade (lengüetas / canales / caídas). No repitas entradas ni direcciones.
EXPOSICION: abierta | resguardada | mixto — media línea
ORIENTACION_COSTA: N/NE/E/SE/S/SO/O/NO (hacia el mar desde la orilla)
PROFUNDIDAD_APARENTE: min-max m cerca de costa (con ancla si la di)
ENTRADAS: 1-3 viñetas, solo nombre/lugar de entrada (sin tip de ruta)
DIRECCIONES_PESCA: 2-4 viñetas. Formato: brújula: destino corto — tip en ≤8 palabras. Ej:
  - E: hacia Cala de los Apóstoles — lengüeta y caída
  - S: exterior del espigón — solo mar plano
PELIGROS_Y_REFERENCIAS: media línea (rocas a flor, boyas, tráfico…)
CORRIENTE_LOCAL: media línea
RESUMEN: 1 frase táctica NUEVA (corriente/viento/evitar). No repitas entradas, direcciones ni estructura.

Concreto para entrada a pie/orilla. No inventes profundidades exactas sin ancla.
"""


def personalized_spot_prompt(label: str, lat: float, lon: float) -> str:
    """Prompt listo para ChatGPT incluyendo nombre y coordenadas del spot."""
    return (
        f"Spot de pesca submarina: {label}\n"
        f"Coordenadas: {lat:.6f}, {lon:.6f}\n"
        f"Zona: costa de Alicante / Murcia (España).\n\n"
        f"Adjunta DOS capturas de ESTE punto concreto:\n"
        f"1) Satélite / Google Maps o Earth\n"
        f"2) Batimetría / hillshade (Plataforma) del mismo encuadre\n\n"
        f"Opcional (recomendado): añade una línea con ancla de profundidad, por ejemplo:\n"
        f"ANCLA_PROFUNDIDAD: la zona más profunda visible en el hillshade es ~12 m\n\n"
        f"{SPOT_ANALYSIS_PROMPT}"
    )


@dataclass(frozen=True)
class DbPaths:
    db_path: str


def make_db_paths(base_dir: str) -> DbPaths:
    return DbPaths(db_path=f"{base_dir}/data/harpoonbook.db")


def now_local() -> datetime:
    return datetime.now(tz=LOCAL_TZ)
