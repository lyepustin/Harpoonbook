from __future__ import annotations

from datetime import datetime
from typing import Any

from harpoonbook.config import compass_es
from harpoonbook.db import list_snapshots_for_day
from harpoonbook.merge_snapshots import merge_snapshots_for_day
from harpoonbook.visibility import forecast_visibility, summarize_recent_days


def _nearest_slot(hourly: list[dict[str, Any]], hour: int) -> dict[str, Any] | None:
    if not hourly:
        return None
    return min(hourly, key=lambda s: abs(int(s.get("local_hour", 0)) - hour))


def _nearest_current_slot(hourly: list[dict[str, Any]], hour: int) -> dict[str, Any] | None:
    active = []
    for slot in hourly:
        try:
            speed = float(slot.get("current_speed_kmh"))
        except (TypeError, ValueError):
            continue
        if speed > 0:
            active.append(slot)
    return _nearest_slot(active, hour)


def suggest_water_for_window(
    db_path: str,
    spot_id: str,
    started_at: datetime,
    ended_at: datetime | None = None,
    *,
    bottom_type: str | None = None,
    coast_facing: str | None = None,
    runoff_risk: str | None = None,
) -> dict[str, Any]:
    """
    Preset de condiciones de agua a partir del snapshot fusionado del dia de started_at.
    Usa la franja horaria mas cercana al inicio de la salida.
    Visibilidad sugerida = orilla (no mar abierto ~4 km).
    """
    day = started_at.date()
    hour = started_at.hour
    snapshots = list_snapshots_for_day(db_path, snapshot_date=day, spot_id=spot_id)
    if not snapshots:
        return {
            "status": "missing",
            "water_temp_c": None,
            "visibility_m": None,
            "current_speed_kmh": None,
            "current_direction": None,
            "condition_snapshot_id": None,
            "features": {},
        }

    features = merge_snapshots_for_day(snapshots) or {}
    snapshot_id = features.get("condition_snapshot_id")
    windy = features.get("windy") or {}
    water_temp = windy.get("water_temp_c")

    currents = (features.get("currents") or {}).get("open_meteo") or {}
    current_slot = _nearest_current_slot(currents.get("hourly") or [], hour)
    current_speed = (current_slot or {}).get("current_speed_kmh")
    current_dir = compass_es((current_slot or {}).get("current_direction"))

    recent = summarize_recent_days(db_path, spot_id=spot_id, end_date=day)
    vis = forecast_visibility(
        features,
        now=started_at,
        recent_context=recent,
        bottom_type=bottom_type,
        coast_facing=coast_facing,
        runoff_risk=runoff_risk,
        as_of_date=day,
    )
    visibility_m = None
    if vis.get("status") == "ok":
        near = vis.get("nearshore") or vis.get("summary") or {}
        visibility_m = near.get("meters_mid")

    return {
        "status": "ok",
        "water_temp_c": float(water_temp) if water_temp is not None else None,
        "visibility_m": float(visibility_m) if visibility_m is not None else None,
        "current_speed_kmh": float(current_speed) if current_speed is not None else None,
        "current_direction": current_dir,
        "condition_snapshot_id": snapshot_id,
        "features": features,
        "ended_at": ended_at.isoformat() if ended_at else None,
    }
