from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import requests

from harpoonbook.config import LOCAL_TZ, now_local
from harpoonbook.scrapers.marine_common import current_slot, seaward_query_point

OPEN_METEO_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"


def _empty_day(
    *,
    date_key: str,
    lat: float,
    lon: float,
    query_lat: float,
    query_lon: float,
    status: str,
    error: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "source": "open_meteo",
        "status": status,
        "forecast_date": date_key,
        "lat": lat,
        "lon": lon,
        "query_lat": query_lat,
        "query_lon": query_lon,
        "hourly": [],
        "disclaimer": "Modelo ~8 km; precision costera limitada.",
    }
    if error:
        out["error"] = error
    return out


def _slots_from_payload(
    payload: dict[str, Any],
    *,
    day_filter: date | None = None,
) -> list[dict[str, Any]]:
    hourly_raw = payload.get("hourly") or {}
    times = hourly_raw.get("time") or []
    velocities = hourly_raw.get("ocean_current_velocity") or []
    directions = hourly_raw.get("ocean_current_direction") or []

    hourly: list[dict[str, Any]] = []
    for t, v_kmh, d_deg in zip(times, velocities, directions):
        if v_kmh is None or d_deg is None:
            continue
        try:
            speed = float(v_kmh)
            direction = float(d_deg)
        except (TypeError, ValueError):
            continue
        # 0 km/h = calma / sin señal útil; la dirección suele ser un placeholder (p.ej. 90°)
        if speed <= 0:
            continue
        dt = datetime.fromisoformat(t).replace(tzinfo=LOCAL_TZ)
        if day_filter is not None and dt.date() != day_filter:
            continue
        hourly.append(
            current_slot(
                time_iso=dt.isoformat(),
                local_hour=dt.hour,
                speed_kmh=speed,
                direction_deg=direction,
            )
        )
    return hourly


def fetch_open_meteo_currents_range(
    lat: float,
    lon: float,
    *,
    start_date: date | None = None,
    num_days: int = 1,
    coast_facing: str | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Corrientes horarias para start_date .. start_date+num_days-1 (una petición).
    Devuelve { "YYYY-MM-DD": payload_open_meteo }.
    """
    start_date = start_date or now_local().date()
    num_days = max(1, num_days)
    end_date = start_date + timedelta(days=num_days - 1)
    query_lat, query_lon = seaward_query_point(lat, lon, coast_facing)

    try:
        resp = requests.get(
            OPEN_METEO_MARINE_URL,
            params={
                "latitude": query_lat,
                "longitude": query_lon,
                "hourly": "ocean_current_velocity,ocean_current_direction",
                "timezone": "Europe/Madrid",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "cell_selection": "sea",
            },
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; harpoonbook/0.1)"},
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        err = str(exc)
        return {
            (start_date + timedelta(days=i)).isoformat(): _empty_day(
                date_key=(start_date + timedelta(days=i)).isoformat(),
                lat=lat,
                lon=lon,
                query_lat=query_lat,
                query_lon=query_lon,
                status="error",
                error=err,
            )
            for i in range(num_days)
        }

    out: dict[str, dict[str, Any]] = {}
    for i in range(num_days):
        day = start_date + timedelta(days=i)
        date_key = day.isoformat()
        hourly = _slots_from_payload(payload, day_filter=day)
        out[date_key] = {
            "source": "open_meteo",
            "status": "ok" if hourly else "missing",
            "forecast_date": date_key,
            "lat": lat,
            "lon": lon,
            "query_lat": query_lat,
            "query_lon": query_lon,
            "grid_lat": payload.get("latitude"),
            "grid_lon": payload.get("longitude"),
            "hourly": hourly,
            "disclaimer": "Modelo ~8 km; precision costera limitada.",
        }
    return out


def fetch_open_meteo_currents(
    lat: float,
    lon: float,
    forecast_date: date | None = None,
    coast_facing: str | None = None,
) -> dict[str, Any]:
    """
    Corrientes horarias desde Open-Meteo Marine (gratis, sin cuenta).
    Precision costera limitada (~8 km).

    Omite horas con velocidad 0 (en calma el modelo pone dirección basura).
    Si hay coast_facing, consulta ~1 celda mar adentro para evitar landmask costero.
    """
    forecast_date = forecast_date or now_local().date()
    by_day = fetch_open_meteo_currents_range(
        lat,
        lon,
        start_date=forecast_date,
        num_days=1,
        coast_facing=coast_facing,
    )
    return by_day.get(forecast_date.isoformat()) or _empty_day(
        date_key=forecast_date.isoformat(),
        lat=lat,
        lon=lon,
        query_lat=lat,
        query_lon=lon,
        status="missing",
    )
