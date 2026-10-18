from __future__ import annotations

import math
from typing import Any

from harpoonbook.config import COMPASS_16_ES, compass_es

# SMOC (Open-Meteo corrientes) ~0.08°; un paso mar adentro evita celdas costeras en calma.
SEA_OFFSET_DEG = 0.08


def deg_to_compass(deg: float | int | None) -> str | None:
    if deg is None:
        return None
    try:
        value = float(deg)
    except (TypeError, ValueError):
        return None
    if math.isnan(value):
        return None
    idx = int((value + 11.25) // 22.5) % 16
    return COMPASS_16_ES[idx]


def uv_to_speed_dir(u_ms: float, v_ms: float) -> tuple[float, float]:
    """u/v en m/s (este/norte). Velocidad m/s y direccion hacia donde fluye."""
    speed = math.hypot(u_ms, v_ms)
    direction = (math.degrees(math.atan2(u_ms, v_ms)) + 360.0) % 360.0
    return speed, direction


def seaward_query_point(
    lat: float,
    lon: float,
    coast_facing: str | None,
    *,
    offset_deg: float = SEA_OFFSET_DEG,
) -> tuple[float, float]:
    """Desplaza el punto de consulta ~1 celda mar adentro según coast_facing."""
    facing = compass_es(coast_facing)
    if not facing or facing not in COMPASS_16_ES:
        return lat, lon
    bearing_deg = COMPASS_16_ES.index(facing) * 22.5
    rad = math.radians(bearing_deg)
    cos_lat = max(math.cos(math.radians(lat)), 0.2)
    return (
        lat + offset_deg * math.cos(rad),
        lon + offset_deg * math.sin(rad) / cos_lat,
    )


def current_slot(
    *,
    time_iso: str,
    local_hour: int,
    speed_kmh: float,
    direction_deg: float,
    u_ms: float | None = None,
    v_ms: float | None = None,
) -> dict[str, Any]:
    speed_ms = speed_kmh / 3.6
    return {
        "time": time_iso,
        "local_hour": local_hour,
        "current_speed_kmh": round(speed_kmh, 2),
        "current_speed_kn": round(speed_kmh / 1.852, 2),
        "current_speed_ms": round(speed_ms, 3),
        "current_direction_deg": round(direction_deg, 0),
        "current_direction": deg_to_compass(direction_deg),
        "u_ms": round(u_ms, 4) if u_ms is not None else None,
        "v_ms": round(v_ms, 4) if v_ms is not None else None,
    }
