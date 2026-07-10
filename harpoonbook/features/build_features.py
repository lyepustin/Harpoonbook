from __future__ import annotations

from datetime import datetime
from typing import Any

from harpoonbook.config import now_local
from harpoonbook.scrapers.tablademeareas import fetch_tablademeareas_cabo_roig_features
from harpoonbook.scrapers.windy_embed import fetch_windy_waves_wind_features


def _hours_to(from_dt: datetime, to_dt: datetime) -> float:
    return (to_dt - from_dt).total_seconds() / 3600.0


def get_shared_features(now: datetime | None = None) -> dict[str, Any]:
    """
    Solunar y marea desde tablademareas (Cabo Roig).
    Compartido por todos los spots el mismo día.
    """
    now = now or now_local()
    features: dict[str, Any] = {
        "timestamp": now.isoformat(),
        "solunar_activity_day": None,
        "solunar_best_hour": None,
        "solunar_best_windows": None,
        "solunar": None,
        "tide_direction": None,
        "next_tide_event": None,
        "hours_to_next_tide_event": None,
        "hours_to_sunrise": None,
        "hours_to_sunset": None,
        "sunrise_time": None,
        "sunset_time": None,
        "solunar_high_periods": None,
        "solunar_medium_periods": None,
    }

    td = fetch_tablademeareas_cabo_roig_features(now=now)
    if not td:
        return features

    features.update(
        {
            "solunar": td.get("solunar"),
            "solunar_activity_day": td.get("solunar_activity_day"),
            "solunar_best_hour": td.get("solunar_best_hour"),
            "solunar_best_windows": td.get("solunar_best_windows"),
            "tide_direction": td.get("tide_direction"),
            "next_tide_event": td.get("next_tide_event"),
            "hours_to_next_tide_event": td.get("hours_to_next_tide_event"),
            "sunrise_time": td.get("sunrise_time"),
            "sunset_time": td.get("sunset_time"),
            "solunar_high_periods": td.get("solunar_high_periods"),
            "solunar_medium_periods": td.get("solunar_medium_periods"),
        }
    )

    if td.get("sunrise_time"):
        sunrise_dt = datetime.fromisoformat(td["sunrise_time"])
        features["hours_to_sunrise"] = _hours_to(now, sunrise_dt)
    if td.get("sunset_time"):
        sunset_dt = datetime.fromisoformat(td["sunset_time"])
        features["hours_to_sunset"] = _hours_to(now, sunset_dt)

    return features


def get_windy_features(
    spot_lat: float,
    spot_lon: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or now_local()
    features: dict[str, Any] = {
        "wind_speed": None,
        "gusts": None,
        "wave_height": None,
        "wave_period": None,
        "water_temp_c": None,
        "windy": None,
    }

    wind = fetch_windy_waves_wind_features(
        lat=spot_lat,
        lon=spot_lon,
        detail_lat=spot_lat,
        detail_lon=spot_lon,
        forecast_date=now.date(),
    )
    if wind:
        features.update(wind)
    return features


def get_features_for_spot(
    spot_lat: float,
    spot_lon: float,
    now: datetime | None = None,
    shared: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or now_local()
    merged = dict(shared if shared is not None else get_shared_features(now=now))
    merged.update(get_windy_features(spot_lat=spot_lat, spot_lon=spot_lon, now=now))
    merged["timestamp"] = now.isoformat()
    return merged


def get_features_now(
    spot_lat: float,
    spot_lon: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compat: features completas para un spot (solunar compartido + Windy local)."""
    return get_features_for_spot(spot_lat=spot_lat, spot_lon=spot_lon, now=now)
