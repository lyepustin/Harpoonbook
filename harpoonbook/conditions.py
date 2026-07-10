from __future__ import annotations

from datetime import date, datetime
from typing import Any

from harpoonbook.config import now_local
from harpoonbook.db import (
    get_fishing_spot,
    get_latest_snapshot,
    list_fishing_spots,
    list_snapshot_dates,
    list_snapshots_for_day,
    save_condition_snapshot,
)
from harpoonbook.features.build_features import get_features_for_spot, get_shared_features
from harpoonbook.merge_snapshots import merge_snapshots_for_day


def _source_status(features: dict[str, Any]) -> dict[str, str]:
    status: dict[str, str] = {}
    status["solunar"] = "ok" if features.get("solunar") else "missing"
    status["windy"] = "ok" if features.get("windy") else "missing"
    status["tide"] = "ok" if features.get("tide_direction") or features.get("next_tide_event") else "missing"
    return status


def _tide_payload(features: dict[str, Any]) -> dict[str, Any]:
    return {
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
) -> dict[str, Any]:
    """Scrapea condiciones: solunar compartido (Cabo Roig) + Windy en coords del spot."""
    now = now or now_local()
    features = get_features_for_spot(spot_lat=lat, spot_lon=lon, now=now, shared=shared)
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
    Añade un snapshot nuevo (no sobrescribe anteriores).
    Devuelve (features de esa captura, snapshot_id).
    """
    lat, lon = _resolve_spot_coords(db_path, spot_id, lat=lat, lon=lon)
    now = now or now_local()
    features = capture_conditions(
        db_path=db_path,
        spot_id=spot_id,
        lat=lat,
        lon=lon,
        now=now,
        shared=shared,
    )

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
    return features, snapshot_id


def capture_all_spots(
    db_path: str,
    now: datetime | None = None,
) -> list[tuple[str, int, dict[str, Any]]]:
    """
    Captura un snapshot por cada spot activo.
    Solunar/marea se scrapean una sola vez; Windy usa las coords de cada spot.
    """
    now = now or now_local()
    shared = get_shared_features(now=now)
    results: list[tuple[str, int, dict[str, Any]]] = []
    for spot in list_fishing_spots(db_path, active_only=True):
        features, snapshot_id = capture_and_save(
            db_path,
            spot_id=spot["id"],
            lat=float(spot["lat"]),
            lon=float(spot["lon"]),
            now=now,
            shared=shared,
        )
        results.append((spot["id"], snapshot_id, features))
    return results


def load_or_capture(
    db_path: str,
    spot_id: str,
    *,
    max_age_hours: float = 1.0,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], int | None, bool]:
    """
    Si hace falta, captura un snapshot nuevo; siempre devuelve vista fusionada del día.
    """
    now = now_local()
    today = now.date()
    was_refreshed = False

    if force_refresh:
        capture_and_save(db_path, spot_id=spot_id)
        was_refreshed = True
    else:
        latest = get_latest_snapshot(db_path, snapshot_date=today, spot_id=spot_id)
        if not latest:
            capture_and_save(db_path, spot_id=spot_id)
            was_refreshed = True
        elif latest.get("raw"):
            captured_at = datetime.fromisoformat(latest["captured_at"])
            age_h = (now - captured_at).total_seconds() / 3600.0
            if age_h > max_age_hours:
                capture_and_save(db_path, spot_id=spot_id)
                was_refreshed = True

    features, snapshot_id = _load_merged_for_day(db_path, spot_id=spot_id, snapshot_date=today)
    return features, snapshot_id, was_refreshed


def load_conditions_for_view(
    db_path: str,
    spot_id: str,
    view_date: date,
    *,
    max_age_hours: float = 1.0,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], int | None, bool, str]:
    """
    Carga condiciones fusionadas para el dashboard.

    Windy: merge de todas las capturas del día (estrategia por franja horaria).
    """
    today = now_local().date()

    if view_date > today:
        return {}, None, False, "missing"

    if view_date == today:
        features, snapshot_id, was_refreshed = load_or_capture(
            db_path,
            spot_id=spot_id,
            max_age_hours=max_age_hours,
            force_refresh=force_refresh,
        )
        if not features:
            return {}, None, False, "missing"
        mode = "live" if was_refreshed else "cached"
        return features, snapshot_id, was_refreshed, mode

    features, snapshot_id = _load_merged_for_day(db_path, spot_id=spot_id, snapshot_date=view_date)
    if features:
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
        max_age_hours=0.25,
        force_refresh=force_refresh,
    )
    return features, snapshot_id
