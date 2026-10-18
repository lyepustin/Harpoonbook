from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from threading import Lock
from typing import Any

from harpoonbook.config import (
    CAPTURE_MAX_WORKERS,
    MAX_CAPTURES_PER_DAY,
    MIN_CAPTURE_INTERVAL_HOURS,
    now_local,
)
from harpoonbook.db import (
    count_capture_rounds_on_day,
    get_fishing_spot,
    get_latest_snapshot,
    list_fishing_spots,
    list_snapshot_dates,
    list_snapshots_for_day,
    save_condition_snapshot,
)
from harpoonbook.features.build_features import get_features_for_spot, get_shared_features
from harpoonbook.merge_snapshots import merge_snapshots_for_day
from harpoonbook.scrapers.windy import WINDY_LOOKAHEAD_DAYS

# Serializa escrituras SQLite cuando varios spots capturan a la vez.
_DB_WRITE_LOCK = Lock()


def _source_status(features: dict[str, Any]) -> dict[str, str]:
    status: dict[str, str] = {}
    status["solunar"] = "ok" if features.get("solunar") else "missing"
    status["windy"] = "ok" if features.get("windy") else "missing"
    status["tide"] = (
        "ok"
        if features.get("tide_direction")
        or features.get("next_tide_event")
        or features.get("tide_state")
        else "missing"
    )
    currents = features.get("currents") or {}
    om = currents.get("open_meteo") or {}
    status["open_meteo"] = om.get("status") or ("ok" if om.get("hourly") else "missing")
    optics = features.get("water_optics") or {}
    status["water_optics"] = optics.get("status") or ("ok" if optics.get("hourly") else "missing")
    precip = features.get("precipitation") or {}
    status["precipitation"] = precip.get("status") or (
        "ok" if precip.get("daily") else "missing"
    )
    return status


def _tide_payload(features: dict[str, Any]) -> dict[str, Any]:
    return {
        "tide_state": features.get("tide_state"),
        "tide_direction": features.get("tide_direction"),
        "next_tide_event": features.get("next_tide_event"),
        "hours_to_next_tide_event": features.get("hours_to_next_tide_event"),
        "sunrise_time": features.get("sunrise_time"),
        "sunset_time": features.get("sunset_time"),
        "hours_to_sunrise": features.get("hours_to_sunrise"),
        "hours_to_sunset": features.get("hours_to_sunset"),
    }


def _resolve_spot_coords(
    db_path: str,
    spot_id: str,
    lat: float | None = None,
    lon: float | None = None,
) -> tuple[float, float]:
    if lat is not None and lon is not None:
        return float(lat), float(lon)
    spot = get_fishing_spot(db_path, spot_id)
    if not spot:
        raise ValueError(f"Spot desconocido: {spot_id}")
    return float(spot["lat"]), float(spot["lon"])


def _load_merged_for_day(
    db_path: str,
    spot_id: str,
    snapshot_date: date,
) -> tuple[dict[str, Any], int | None]:
    snapshots = list_snapshots_for_day(db_path, snapshot_date=snapshot_date, spot_id=spot_id)
    merged = merge_snapshots_for_day(snapshots)
    if not merged:
        return {}, None
    return merged, merged.get("condition_snapshot_id")


def capture_conditions(
    db_path: str,
    spot_id: str,
    lat: float,
    lon: float,
    now: datetime | None = None,
    shared: dict[str, Any] | None = None,
    coast_facing: str | None = None,
) -> dict[str, Any]:
    """Scrapea condiciones: solunar compartido (Cabo Roig) + Windy en coords del spot."""
    now = now or now_local()
    if coast_facing is None:
        spot = get_fishing_spot(db_path, spot_id)
        coast_facing = (spot or {}).get("coast_facing")
    features = get_features_for_spot(
        spot_lat=lat,
        spot_lon=lon,
        now=now,
        shared=shared,
        coast_facing=coast_facing,
    )
    features["spot_id"] = spot_id
    features["lat"] = lat
    features["lon"] = lon
    return features


def capture_and_save(
    db_path: str,
    spot_id: str,
    lat: float | None = None,
    lon: float | None = None,
    now: datetime | None = None,
    shared: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    """
    Snapshot de hoy + previsiones (Windy, corrientes, óptica, solunar/marea) de los N días siguientes.
    Cada recaptura actualiza esos días futuros con la previsión más reciente.
    """
    lat, lon = _resolve_spot_coords(db_path, spot_id, lat=lat, lon=lon)
    now = now or now_local()
    spot = get_fishing_spot(db_path, spot_id)
    features = capture_conditions(
        db_path=db_path,
        spot_id=spot_id,
        lat=lat,
        lon=lon,
        now=now,
        shared=shared,
        coast_facing=(spot or {}).get("coast_facing"),
    )

    with _DB_WRITE_LOCK:
        snapshot_id = save_condition_snapshot(
            db_path=db_path,
            snapshot_date=now.date(),
            spot_id=spot_id,
            lat=lat,
            lon=lon,
            solunar=features.get("solunar"),
            windy=features.get("windy"),
            tide=_tide_payload(features),
            source_status=_source_status(features),
            raw=features,
            captured_at=now,
        )
        features["condition_snapshot_id"] = snapshot_id

        _save_forecast_lookahead_snapshots(
            db_path,
            spot_id=spot_id,
            lat=lat,
            lon=lon,
            now=now,
            features=features,
        )
    return features, snapshot_id


def _save_forecast_lookahead_snapshots(
    db_path: str,
    *,
    spot_id: str,
    lat: float,
    lon: float,
    now: datetime,
    features: dict[str, Any],
) -> list[int]:
    """Guarda/actualiza snapshots de los días siguientes con todas las fuentes disponibles."""
    windy_by_date = features.get("windy_by_date") or {}
    currents_by_date = features.get("open_meteo_by_date") or {}
    optics_by_date = features.get("water_optics_by_date") or {}
    precip_full = features.get("precipitation") or {}
    precip_by_date = features.get("precipitation_by_date") or {}
    lookahead_rows = {
        entry.get("date"): entry
        for entry in (features.get("solunar_lookahead") or [])
        if entry.get("date")
    }

    if not (
        windy_by_date
        or currents_by_date
        or optics_by_date
        or precip_by_date
        or lookahead_rows
    ):
        return []

    saved: list[int] = []
    today = now.date()
    for offset in range(1, WINDY_LOOKAHEAD_DAYS + 1):
        day = today + timedelta(days=offset)
        day_key = day.isoformat()

        windy = windy_by_date.get(day_key)
        om = currents_by_date.get(day_key)
        optics = optics_by_date.get(day_key)
        precip_day = precip_by_date.get(day_key)
        # Misma serie diaria en cada día de previsión (pesos relativos a as_of=view_date).
        precip = dict(precip_full) if precip_full else precip_day
        if precip and precip_day:
            precip = dict(precip)
            precip["forecast_date"] = day_key
            precip["precip_mm"] = precip_day.get("precip_mm")
        look = lookahead_rows.get(day_key) or {}
        solunar = look.get("solunar")
        tide_block = look.get("tide") or {}

        if not any([windy, om, optics, precip, solunar]):
            continue

        raw: dict[str, Any] = {
            "timestamp": now.isoformat(),
            "spot_id": spot_id,
            "lat": lat,
            "lon": lon,
            "is_forecast": True,
            "forecast_for": day_key,
            "captured_from": today.isoformat(),
            "windy": windy,
            "solunar": solunar,
            "solunar_lookahead": features.get("solunar_lookahead") or [],
            "currents": {"open_meteo": om} if om else {},
            "water_optics": optics,
            "precipitation": precip,
            "water_temp_c": (windy or {}).get("water_temp_c"),
            "tide_state": tide_block.get("tide_state"),
            "tide_direction": tide_block.get("tide_direction"),
            "next_tide_event": tide_block.get("next_tide_event"),
            "hours_to_next_tide_event": tide_block.get("hours_to_next_tide_event"),
            "tide_events": tide_block.get("tide_events") or [],
            "sunrise_time": (solunar or {}).get("sunrise_time"),
            "sunset_time": (solunar or {}).get("sunset_time"),
        }
        if windy and windy.get("hourly"):
            mid = windy["hourly"][len(windy["hourly"]) // 2]
            raw["wind_speed"] = mid.get("wind_speed_kmh")
            raw["gusts"] = mid.get("wind_gusts_kmh")
            raw["wave_height"] = mid.get("wave_height_m")
            raw["wave_period"] = mid.get("wave_period_s")

        status = {
            "solunar": "ok" if solunar else "missing",
            "windy": "ok" if windy else "missing",
            "tide": "ok" if (tide_block.get("tide_events") or tide_block.get("next_tide_event")) else "missing",
            "open_meteo": (om or {}).get("status") or ("ok" if (om or {}).get("hourly") else "missing"),
            "water_optics": (optics or {}).get("status")
            or ("ok" if (optics or {}).get("hourly") else "missing"),
            "precipitation": (precip or {}).get("status")
            or ("ok" if (precip or {}).get("daily") else "missing"),
            "forecast": "lookahead",
        }
        snap_id = save_condition_snapshot(
            db_path=db_path,
            snapshot_date=day,
            spot_id=spot_id,
            lat=lat,
            lon=lon,
            solunar=solunar,
            windy=windy,
            tide={
                "tide_state": tide_block.get("tide_state"),
                "tide_direction": tide_block.get("tide_direction"),
                "next_tide_event": tide_block.get("next_tide_event"),
                "hours_to_next_tide_event": tide_block.get("hours_to_next_tide_event"),
                "tide_events": tide_block.get("tide_events") or [],
                "sunrise_time": (solunar or {}).get("sunrise_time"),
                "sunset_time": (solunar or {}).get("sunset_time"),
            },
            source_status=status,
            raw=raw,
            captured_at=now,
        )
        saved.append(snap_id)
    return saved


def capture_all_spots(
    db_path: str,
    now: datetime | None = None,
) -> list[tuple[str, int, dict[str, Any]]]:
    """
    Captura un snapshot por cada spot activo.
    Solunar/marea una sola vez; spots en paralelo (hasta CAPTURE_MAX_WORKERS).
    """
    now = now or now_local()
    shared = get_shared_features(now=now)
    spots = list_fishing_spots(db_path, active_only=True)
    if not spots:
        return []

    results: list[tuple[str, int, dict[str, Any]]] = []
    workers = min(CAPTURE_MAX_WORKERS, len(spots))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                capture_and_save,
                db_path,
                spot_id=spot["id"],
                lat=float(spot["lat"]),
                lon=float(spot["lon"]),
                now=now,
                shared=shared,
            ): spot["id"]
            for spot in spots
        }
        for fut in as_completed(futures):
            spot_id = futures[fut]
            features, snapshot_id = fut.result()
            results.append((spot_id, snapshot_id, features))

    # Orden estable = orden de spots activos.
    order = {s["id"]: i for i, s in enumerate(spots)}
    results.sort(key=lambda row: order.get(row[0], 999))
    return results


def today_needs_refresh(
    db_path: str,
    spot_id: str,
    *,
    max_age_hours: float | None = None,
    max_per_day: int | None = None,
) -> bool:
    """True si toca auto-captura: hueco mínimo y tope de rondas/día."""
    now = now_local()
    interval = MIN_CAPTURE_INTERVAL_HOURS if max_age_hours is None else max_age_hours
    cap = MAX_CAPTURES_PER_DAY if max_per_day is None else max_per_day
    if cap > 0 and count_capture_rounds_on_day(db_path, now.date()) >= cap:
        return False
    latest = get_latest_snapshot(db_path, snapshot_date=now.date(), spot_id=spot_id)
    if not latest or not latest.get("raw"):
        return True
    captured_at = datetime.fromisoformat(latest["captured_at"])
    age_h = (now - captured_at).total_seconds() / 3600.0
    return age_h > interval


def load_or_capture(
    db_path: str,
    spot_id: str,
    *,
    max_age_hours: float | None = None,
    force_refresh: bool = False,
    capture_if_stale: bool = True,
) -> tuple[dict[str, Any], int | None, bool]:
    """
    Si hace falta, captura un snapshot nuevo; siempre devuelve vista fusionada del día.

    Con capture_if_stale=False solo lee la BD (útil para refrescar en background).
    """
    today = now_local().date()
    was_refreshed = False

    if capture_if_stale and (
        force_refresh or today_needs_refresh(db_path, spot_id, max_age_hours=max_age_hours)
    ):
        capture_and_save(db_path, spot_id=spot_id)
        was_refreshed = True

    features, snapshot_id = _load_merged_for_day(db_path, spot_id=spot_id, snapshot_date=today)
    return features, snapshot_id, was_refreshed


def load_conditions_for_view(
    db_path: str,
    spot_id: str,
    view_date: date,
    *,
    max_age_hours: float | None = None,
    force_refresh: bool = False,
    capture_if_stale: bool = True,
) -> tuple[dict[str, Any], int | None, bool, str]:
    """
    Carga condiciones fusionadas para el dashboard.

    Windy: merge de todas las capturas del día (estrategia por franja horaria).
    Días futuros (hasta WINDY_LOOKAHEAD_DAYS): previsión guardada en capturas previas.
    """
    today = now_local().date()
    max_future = today + timedelta(days=WINDY_LOOKAHEAD_DAYS)

    if view_date > max_future:
        return {}, None, False, "missing"

    if view_date == today:
        features, snapshot_id, was_refreshed = load_or_capture(
            db_path,
            spot_id=spot_id,
            max_age_hours=max_age_hours,
            force_refresh=force_refresh,
            capture_if_stale=capture_if_stale,
        )
        if not features:
            return {}, None, False, "missing"
        mode = "live" if was_refreshed else "cached"
        return features, snapshot_id, was_refreshed, mode

    features, snapshot_id = _load_merged_for_day(db_path, spot_id=spot_id, snapshot_date=view_date)
    if features:
        if view_date > today:
            return features, snapshot_id, False, "forecast"
        return features, snapshot_id, False, "historical"

    return {}, None, False, "missing"


def load_today_conditions_for_log(
    db_path: str,
    spot_id: str,
    *,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], int | None]:
    """Condiciones fusionadas de hoy para enlazar un log nuevo."""
    features, snapshot_id, _ = load_or_capture(
        db_path,
        spot_id=spot_id,
        force_refresh=force_refresh,
    )
    return features, snapshot_id
