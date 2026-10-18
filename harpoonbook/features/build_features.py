from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from harpoonbook.config import CAPTURE_MAX_WORKERS, now_local
from harpoonbook.scrapers.open_meteo_marine import (
    fetch_open_meteo_currents,
    fetch_open_meteo_currents_range,
)
from harpoonbook.scrapers.open_meteo_weather import (
    PRECIP_LOOKBACK_DAYS,
    fetch_open_meteo_precipitation,
)
from harpoonbook.scrapers.copernicus_optics import (
    fetch_copernicus_optics,
    fetch_copernicus_optics_range,
)
from harpoonbook.scrapers.tablademeareas import fetch_tablademeareas_cabo_roig_features
from harpoonbook.scrapers.windy import WINDY_LOOKAHEAD_DAYS, fetch_windy_waves_wind_features


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
        "solunar_lookahead": None,
        "tide_state": None,
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
            "solunar_lookahead": td.get("solunar_lookahead") or [],
            "tide_state": td.get("tide_state"),
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
    coast_facing: str | None = None,
) -> dict[str, Any]:
    now = now or now_local()
    features: dict[str, Any] = {
        "wind_speed": None,
        "gusts": None,
        "wave_height": None,
        "wave_period": None,
        "water_temp_c": None,
        "windy": None,
        "windy_by_date": None,
    }

    wind = fetch_windy_waves_wind_features(
        lat=spot_lat,
        lon=spot_lon,
        detail_lat=spot_lat,
        detail_lon=spot_lon,
        forecast_date=now.date(),
        coast_facing=coast_facing,
        lookahead_days=WINDY_LOOKAHEAD_DAYS,
    )
    if wind:
        features.update(wind)
    return features


def get_currents_features(
    spot_lat: float,
    spot_lon: float,
    now: datetime | None = None,
    coast_facing: str | None = None,
) -> dict[str, Any]:
    now = now or now_local()
    by_date = fetch_open_meteo_currents_range(
        spot_lat,
        spot_lon,
        start_date=now.date(),
        num_days=1 + WINDY_LOOKAHEAD_DAYS,
        coast_facing=coast_facing,
    )
    today_key = now.date().isoformat()
    return {
        "open_meteo": by_date.get(today_key)
        or fetch_open_meteo_currents(
            lat=spot_lat,
            lon=spot_lon,
            forecast_date=now.date(),
            coast_facing=coast_facing,
        ),
        "open_meteo_by_date": by_date,
    }


def get_optics_features(
    spot_lat: float,
    spot_lon: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or now_local()
    by_date = fetch_copernicus_optics_range(
        spot_lat,
        spot_lon,
        start_date=now.date(),
        num_days=1 + WINDY_LOOKAHEAD_DAYS,
    )
    today_key = now.date().isoformat()
    return {
        "water_optics": by_date.get(today_key)
        or fetch_copernicus_optics(lat=spot_lat, lon=spot_lon, forecast_date=now.date()),
        "water_optics_by_date": by_date,
    }


def get_precipitation_features(
    spot_lat: float,
    spot_lon: float,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or now_local()
    precip = fetch_open_meteo_precipitation(
        spot_lat,
        spot_lon,
        as_of=now.date(),
        lookback_days=PRECIP_LOOKBACK_DAYS,
        forecast_days=1 + WINDY_LOOKAHEAD_DAYS,
    )
    by_date = dict(precip.pop("by_date", None) or {})
    return {
        "precipitation": precip,
        "precipitation_by_date": by_date,
    }


def get_features_for_spot(
    spot_lat: float,
    spot_lon: float,
    now: datetime | None = None,
    shared: dict[str, Any] | None = None,
    coast_facing: str | None = None,
) -> dict[str, Any]:
    """Windy + corrientes + óptica + precipitación; solunar compartido o bajo demanda."""
    now = now or now_local()
    merged = dict(shared if shared is not None else get_shared_features(now=now))

    with ThreadPoolExecutor(max_workers=min(4, CAPTURE_MAX_WORKERS)) as pool:
        fut_windy = pool.submit(
            get_windy_features,
            spot_lat,
            spot_lon,
            now,
            coast_facing,
        )
        fut_currents = pool.submit(
            get_currents_features,
            spot_lat,
            spot_lon,
            now,
            coast_facing,
        )
        fut_optics = pool.submit(get_optics_features, spot_lat, spot_lon, now)
        fut_precip = pool.submit(get_precipitation_features, spot_lat, spot_lon, now)
        windy = fut_windy.result()
        currents = fut_currents.result()
        optics = fut_optics.result()
        precip = fut_precip.result()

    merged.update(windy)
    merged["currents"] = {"open_meteo": currents.get("open_meteo")}
    merged["open_meteo_by_date"] = currents.get("open_meteo_by_date") or {}
    merged["water_optics"] = optics.get("water_optics")
    merged["water_optics_by_date"] = optics.get("water_optics_by_date") or {}
    merged["precipitation"] = precip.get("precipitation")
    merged["precipitation_by_date"] = precip.get("precipitation_by_date") or {}
    merged["timestamp"] = now.isoformat()
    return merged


def get_features_now(
    spot_lat: float,
    spot_lon: float,
    now: datetime | None = None,
    coast_facing: str | None = None,
) -> dict[str, Any]:
    """Compat: features completas para un spot (solunar compartido + Windy local)."""
    return get_features_for_spot(
        spot_lat=spot_lat,
        spot_lon=spot_lon,
        now=now,
        coast_facing=coast_facing,
    )
