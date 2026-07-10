from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _slot_window_end(slot: dict[str, Any], step_hours: int) -> datetime:
    start = _parse_dt(slot["time"])
    return start + timedelta(hours=step_hours)


def merge_windy_hourly(
    snapshots: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Fusiona franjas Windy de varias capturas del mismo día.

    Por cada franja horaria H, se elige el snapshot más reciente cuya hora de
    captura sea <= fin de ventana de H (inicio + step_hours).

    Ej: capturas 05:00, 10:00 y 17:00
    - Franja 08:00 → mejor captura <= 11:00 → 10:00
    - Franja 17:00 → mejor captura <= 20:00 → 17:00 (más precisa que la de 05:00)
    - Franja 08:00 no usa la de 17:00 (ya pasó esa ventana)
    """
    if not snapshots:
        return [], {"strategy": "empty", "snapshot_count": 0, "snapshot_ids": []}

    ordered = sorted(snapshots, key=lambda s: s["captured_at"])
    best_by_hour: dict[int, tuple[datetime, dict[str, Any], int]] = {}

    for snap in ordered:
        captured_at = _parse_dt(snap["captured_at"])
        windy = snap.get("windy") or (snap.get("raw") or {}).get("windy") or {}
        step_hours = int(windy.get("step_hours") or 3)
        snap_id = int(snap["id"])

        for slot in windy.get("hourly") or []:
            local_hour = int(slot["local_hour"])
            window_end = _slot_window_end(slot, step_hours)
            if captured_at > window_end:
                continue

            prev = best_by_hour.get(local_hour)
            if prev is None or captured_at > prev[0]:
                merged_slot = dict(slot)
                merged_slot["_from_snapshot_id"] = snap_id
                merged_slot["_from_captured_at"] = snap["captured_at"]
                best_by_hour[local_hour] = (captured_at, merged_slot, snap_id)

    hourly = [best_by_hour[h][1] for h in sorted(best_by_hour)]
    meta = {
        "strategy": "latest_capture_before_slot_window_end",
        "step_hours": int((ordered[-1].get("windy") or {}).get("step_hours") or 3),
        "snapshot_count": len(ordered),
        "snapshot_ids": [int(s["id"]) for s in ordered],
        "captures": [s["captured_at"] for s in ordered],
        "slot_sources": {
            str(h): {
                "snapshot_id": best_by_hour[h][2],
                "captured_at": best_by_hour[h][0].isoformat(),
            }
            for h in best_by_hour
        },
    }
    return hourly, meta


def merge_snapshots_for_day(snapshots: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Combina N snapshots del día: solunar del último + Windy fusionado."""
    if not snapshots:
        return None

    ordered = sorted(snapshots, key=lambda s: s["captured_at"])
    latest = ordered[-1]
    base = dict(latest.get("raw") or {})

    merged_hourly, merge_meta = merge_windy_hourly(ordered)
    windy = dict(latest.get("windy") or (base.get("windy") or {}))
    windy["hourly"] = merged_hourly
    windy["merged"] = True
    windy["merge_meta"] = merge_meta

    base["windy"] = windy
    base["merge_meta"] = merge_meta
    base["condition_snapshot_id"] = latest["id"]
    base["snapshot_ids"] = merge_meta["snapshot_ids"]

    if merged_hourly:
        now = datetime.now().astimezone()
        current = min(
            merged_hourly,
            key=lambda slot: abs(_parse_dt(slot["time"]).hour - now.hour),
        )
        base["wind_speed"] = current.get("wind_speed_kmh")
        base["gusts"] = current.get("wind_gusts_kmh")
        base["wave_height"] = current.get("wave_height_m")
        base["wave_period"] = current.get("wave_period_s")
        base["water_temp_c"] = windy.get("water_temp_c")

    return base
