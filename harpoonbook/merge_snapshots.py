from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


_WAVE_FIELD_KEYS = (
    "wave_height_m",
    "wave_direction_deg",
    "wave_direction",
    "wave_period_s",
    "swell1_height_m",
    "swell1_direction_deg",
    "swell1_direction",
    "swell1_period_s",
    "wave_power_kw_m",
)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _slot_window_end(slot: dict[str, Any], step_hours: int) -> datetime:
    start = _parse_dt(slot["time"])
    return start + timedelta(hours=step_hours)


def _slot_has_waves(slot: dict[str, Any]) -> bool:
    return any(isinstance(slot.get(k), (int, float)) for k in ("wave_height_m", "swell1_height_m", "wave_power_kw_m"))


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _snapshot_water_temp_c(snap: dict[str, Any]) -> float | None:
    windy = snap.get("windy") or (snap.get("raw") or {}).get("windy") or {}
    temp = _as_float(windy.get("water_temp_c"))
    if temp is not None:
        return temp
    return _as_float((snap.get("raw") or {}).get("water_temp_c"))


def latest_good_water_temp(
    snapshots: list[dict[str, Any]],
) -> tuple[float | None, dict[str, Any] | None]:
    """Última temp. de agua numérica del día (captura más reciente que la tenga)."""
    ordered = sorted(snapshots, key=lambda s: s["captured_at"], reverse=True)
    for snap in ordered:
        temp = _snapshot_water_temp_c(snap)
        if temp is None:
            continue
        return temp, {
            "snapshot_id": int(snap["id"]),
            "captured_at": snap["captured_at"],
        }
    return None, None


def _copy_wave_fields(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key in _WAVE_FIELD_KEYS:
        if dst.get(key) is None and src.get(key) is not None:
            dst[key] = src[key]
    if src.get("_from_snapshot_id") is not None and not _slot_has_waves(dst):
        return
    if not _slot_has_waves(dst) and _slot_has_waves(src):
        dst["_waves_from_snapshot_id"] = src.get("_from_snapshot_id")
        dst["_waves_from_captured_at"] = src.get("_from_captured_at")


def merge_windy_hourly(
    snapshots: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Fusiona franjas Windy de varias capturas del mismo día.

    Por cada franja horaria H, se elige el snapshot más reciente cuya hora de
    captura sea <= fin de ventana de H (inicio + step_hours).

    Si hay empate de frescura, preferimos capturas con celda atmos de mar
    (atmos_elevation_m baja) frente a tierra (viento/temp inflados).

    Si la captura elegida no trae olas/swell (fallo parcial del endpoint),
    rellena esos campos desde la captura más reciente que sí los tenga.
    """
    if not snapshots:
        return [], {"strategy": "empty", "snapshot_count": 0, "snapshot_ids": []}

    ordered = sorted(snapshots, key=lambda s: s["captured_at"])
    best_by_hour: dict[int, tuple[datetime, dict[str, Any], int, float]] = {}
    wave_by_hour: dict[int, tuple[datetime, dict[str, Any]]] = {}

    for snap in ordered:
        captured_at = _parse_dt(snap["captured_at"])
        windy = snap.get("windy") or (snap.get("raw") or {}).get("windy") or {}
        step_hours = int(windy.get("step_hours") or 3)
        snap_id = int(snap["id"])
        elev = windy.get("atmos_elevation_m")
        try:
            elev_score = float(elev) if elev is not None else 50.0
        except (TypeError, ValueError):
            elev_score = 50.0

        for slot in windy.get("hourly") or []:
            local_hour = int(slot["local_hour"])
            window_end = _slot_window_end(slot, step_hours)
            if captured_at > window_end:
                continue

            tagged = dict(slot)
            tagged["_from_snapshot_id"] = snap_id
            tagged["_from_captured_at"] = snap["captured_at"]

            if _slot_has_waves(tagged):
                prev_wave = wave_by_hour.get(local_hour)
                if prev_wave is None or captured_at >= prev_wave[0]:
                    wave_by_hour[local_hour] = (captured_at, tagged)

            prev = best_by_hour.get(local_hour)
            better = False
            if prev is None:
                better = True
            else:
                prev_cap, prev_slot, _, prev_elev = prev
                new_sea = elev_score <= 5.0
                prev_sea = prev_elev <= 5.0
                new_waves = _slot_has_waves(tagged)
                prev_waves = _slot_has_waves(prev_slot)
                # Celda de mar gana a tierra aunque sea una captura un poco más antigua.
                if new_sea and not prev_sea:
                    better = True
                elif prev_sea and not new_sea:
                    better = False
                elif new_waves and not prev_waves:
                    better = True
                elif prev_waves and not new_waves and captured_at <= prev_cap:
                    better = False
                elif captured_at > prev_cap:
                    # Preferir captura nueva, aunque falten olas (se rellenan despues).
                    better = True
                elif captured_at == prev_cap and elev_score < prev_elev:
                    better = True
            if better:
                best_by_hour[local_hour] = (captured_at, tagged, snap_id, elev_score)

    hourly: list[dict[str, Any]] = []
    for hour in sorted(best_by_hour):
        slot = dict(best_by_hour[hour][1])
        if not _slot_has_waves(slot) and hour in wave_by_hour:
            wave_src = wave_by_hour[hour][1]
            _copy_wave_fields(slot, wave_src)
            slot["_waves_from_snapshot_id"] = wave_src.get("_from_snapshot_id")
            slot["_waves_from_captured_at"] = wave_src.get("_from_captured_at")
        hourly.append(slot)

    meta = {
        "strategy": "latest_capture_before_slot_window_end+wave_backfill",
        "step_hours": int((ordered[-1].get("windy") or {}).get("step_hours") or 3),
        "snapshot_count": len(ordered),
        "snapshot_ids": [int(s["id"]) for s in ordered],
        "captures": [s["captured_at"] for s in ordered],
        "slot_sources": {},
    }
    for row in hourly:
        h = int(row["local_hour"])
        meta["slot_sources"][str(h)] = {
            "snapshot_id": row.get("_from_snapshot_id"),
            "captured_at": row.get("_from_captured_at"),
            "waves_from_snapshot_id": row.get("_waves_from_snapshot_id"),
        }
    return hourly, meta


def merge_currents_hourly(
    snapshots: list[dict[str, Any]],
    source_key: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fusiona franjas horarias (step 1 h) de un bloque dentro de raw.currents."""
    if not snapshots:
        return [], {"strategy": "empty", "snapshot_count": 0, "snapshot_ids": []}

    ordered = sorted(snapshots, key=lambda s: s["captured_at"])
    best_by_hour: dict[int, tuple[datetime, dict[str, Any], int]] = {}

    for snap in ordered:
        captured_at = _parse_dt(snap["captured_at"])
        currents = (snap.get("raw") or {}).get("currents") or {}
        source = currents.get(source_key) or {}
        snap_id = int(snap["id"])

        for slot in source.get("hourly") or []:
            local_hour = int(slot["local_hour"])
            window_end = _parse_dt(slot["time"]) + timedelta(hours=1)
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
        "step_hours": 1,
        "snapshot_count": len(ordered),
        "snapshot_ids": [int(s["id"]) for s in ordered],
        "captures": [s["captured_at"] for s in ordered],
    }
    return hourly, meta


def _merge_generic_hourly(
    snapshots: list[dict[str, Any]],
    payload_key: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fusiona hourly[] de un bloque top-level en raw (p.ej. water_optics)."""
    if not snapshots:
        return [], {"strategy": "empty", "snapshot_count": 0, "snapshot_ids": []}

    ordered = sorted(snapshots, key=lambda s: s["captured_at"])
    best_by_hour: dict[int, tuple[datetime, dict[str, Any], int]] = {}

    for snap in ordered:
        captured_at = _parse_dt(snap["captured_at"])
        payload = (snap.get("raw") or {}).get(payload_key) or {}
        snap_id = int(snap["id"])

        for slot in payload.get("hourly") or []:
            local_hour = int(slot["local_hour"])
            window_end = _parse_dt(slot["time"]) + timedelta(hours=1)
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
        "step_hours": 1,
        "snapshot_count": len(ordered),
        "snapshot_ids": [int(s["id"]) for s in ordered],
        "captures": [s["captured_at"] for s in ordered],
    }
    return hourly, meta


def merge_snapshots_for_day(snapshots: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Combina N snapshots del día: solunar del último + Windy fusionado."""
    if not snapshots:
        return None

    ordered = sorted(snapshots, key=lambda s: s["captured_at"])
    latest = ordered[-1]
    base = dict(latest.get("raw") or {})

    merged_windy_hourly, merge_meta = merge_windy_hourly(ordered)
    windy = dict(latest.get("windy") or (base.get("windy") or {}))
    windy["hourly"] = merged_windy_hourly
    windy["merged"] = True
    water_temp = _as_float(windy.get("water_temp_c"))
    if water_temp is None:
        water_temp, water_src = latest_good_water_temp(ordered)
        if water_src:
            merge_meta["water_temp_backfill"] = water_src
            windy["water_temp_from_snapshot_id"] = water_src["snapshot_id"]
    if water_temp is not None:
        windy["water_temp_c"] = water_temp
        base["water_temp_c"] = water_temp
    windy["merge_meta"] = merge_meta

    base["windy"] = windy
    base["merge_meta"] = merge_meta
    base["condition_snapshot_id"] = latest["id"]
    base["snapshot_ids"] = merge_meta["snapshot_ids"]

    latest_currents = dict((latest.get("raw") or {}).get("currents") or {})
    merged_currents: dict[str, Any] = {}
    for source_key in ("open_meteo",):
        source = dict(latest_currents.get(source_key) or {})
        merged_source_hourly, currents_meta = merge_currents_hourly(ordered, source_key=source_key)
        if merged_source_hourly:
            source["hourly"] = merged_source_hourly
            source["merged"] = True
            source["merge_meta"] = currents_meta
        merged_currents[source_key] = source
    base["currents"] = merged_currents

    latest_optics = dict((latest.get("raw") or {}).get("water_optics") or {})
    optics_merged, optics_meta = _merge_generic_hourly(ordered, payload_key="water_optics")
    if optics_merged:
        latest_optics["hourly"] = optics_merged
        latest_optics["merged"] = True
        latest_optics["merge_meta"] = optics_meta
        valid_secchi = [h["secchi_est_m"] for h in optics_merged if h.get("secchi_est_m")]
        if valid_secchi:
            latest_optics["secchi_day_mean_m"] = round(sum(valid_secchi) / len(valid_secchi), 1)
    base["water_optics"] = latest_optics

    # Precipitación: última captura con serie diaria (no es horaria).
    latest_precip = None
    for snap in reversed(ordered):
        precip = (snap.get("raw") or {}).get("precipitation")
        if precip and (precip.get("daily") or precip.get("precip_mm") is not None):
            latest_precip = precip
            break
    if latest_precip is not None:
        base["precipitation"] = latest_precip

    if merged_windy_hourly:
        now = datetime.now().astimezone()
        current = min(
            merged_windy_hourly,
            key=lambda slot: abs(_parse_dt(slot["time"]).hour - now.hour),
        )
        base["wind_speed"] = current.get("wind_speed_kmh")
        base["gusts"] = current.get("wind_gusts_kmh")
        base["wave_height"] = current.get("wave_height_m")
        base["wave_period"] = current.get("wave_period_s")

    return base
