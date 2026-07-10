from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


LOCAL_TZ = ZoneInfo("Europe/Madrid")

# Solunar y marea: siempre tablademareas Cabo Roig (independiente del spot Windy).
SOLUNAR_LOCATION = {"label": "Cabo Roig (tablademareas)", "lat": 37.913, "lon": -0.717}

# Valores iniciales si la BD de spots está vacía.
SEED_SPOTS: dict[str, dict[str, float | str]] = {
    "cabo_roig_calas": {"label": "Cabo Roig (calas)", "lat": 37.907, "lon": -0.717},
    "cabo_roig_espigon_puerto": {"label": "Espigón puerto Cabo Roig", "lat": 37.905, "lon": -0.720},
    "punta_glea": {"label": "Punta de la Glea", "lat": 37.892, "lon": -0.708},
    "campoamor_espigon_puerto": {"label": "Espigón puerto Campoamor", "lat": 37.878, "lon": -0.738},
}

# Alias legacy
DEFAULT_SPOTS = SEED_SPOTS


DEFAULT_METHODS = {
    "espera": "Espera",
    "acecho": "Acecho",
    "agujero": "Agujero",
    "cerca_rompientes": "Cerca de rompientes",
    "mar_adentro": "Mar adentro",
}


@dataclass(frozen=True)
class DbPaths:
    db_path: str


def make_db_paths(base_dir: str) -> DbPaths:
    # SQLite en disco local; nombre estable para que el historial crezca.
    return DbPaths(db_path=f"{base_dir}/data/harpoonbook.db")


def now_local() -> datetime:
    return datetime.now(tz=LOCAL_TZ)

