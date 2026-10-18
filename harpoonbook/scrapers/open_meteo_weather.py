from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import requests

from harpoonbook.config import LOCAL_TZ, now_local

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PRECIP_LOOKBACK_DAYS = 3


def _empty_precip(
    *,
    lat: float,
    lon: float,
    status: str,
    as_of: str,
    error: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "source": "open_meteo_forecast",
        "status": status,
        "lat": lat,
        "lon": lon,
        "as_of": as_of,
        "lookback_days": PRECIP_LOOKBACK_DAYS,
        "daily": [],
        "disclaimer": "Precipitación diaria (mm = l/m²). Modelo ~9 km.",
    }
    if error:
        out["error"] = error
    return out


def fetch_open_meteo_precipitation(
    lat: float,
    lon: float,
    *,
    as_of: date | None = None,
    lookback_days: int = PRECIP_LOOKBACK_DAYS,
    forecast_days: int = 4,
) -> dict[str, Any]:
    """
    Precipitación diaria acumulada (past lookback + hoy + previsión).
    Usada para penalizar turbidez de orilla por escorrentía / barrancos.
    """
    as_of = as_of or now_local().date()
    as_of_key = as_of.isoformat()
    start = as_of - timedelta(days=lookback_days)
    end = as_of + timedelta(days=max(0, forecast_days - 1))

    try:
        resp = requests.get(
            OPEN_METEO_FORECAST_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "precipitation_sum",
                "timezone": str(LOCAL_TZ),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
            timeout=25,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — degradar a missing
        return _empty_precip(
            lat=lat,
            lon=lon,
            status="error",
            as_of=as_of_key,
            error=str(exc),
        )

    daily_raw = payload.get("daily") or {}
    times = daily_raw.get("time") or []
    precip = daily_raw.get("precipitation_sum") or []

    daily: list[dict[str, Any]] = []
    by_date: dict[str, dict[str, Any]] = {}
    for day_s, mm in zip(times, precip):
        if mm is None:
            continue
        try:
            value = float(mm)
        except (TypeError, ValueError):
            continue
        row = {
            "date": day_s,
            "precip_mm": round(value, 1),
        }
        daily.append(row)
        by_date[day_s] = {
            "source": "open_meteo_forecast",
            "status": "ok",
            "forecast_date": day_s,
            "lat": lat,
            "lon": lon,
            "precip_mm": round(value, 1),
            "daily": daily,
            "lookback_days": lookback_days,
            "disclaimer": "Precipitación diaria (mm = l/m²). Modelo ~9 km.",
        }

    if not daily:
        return _empty_precip(lat=lat, lon=lon, status="missing", as_of=as_of_key)

    today_mm = None
    for row in daily:
        if row["date"] == as_of_key:
            today_mm = row["precip_mm"]
            break

    return {
        "source": "open_meteo_forecast",
        "status": "ok",
        "lat": lat,
        "lon": lon,
        "as_of": as_of_key,
        "lookback_days": lookback_days,
        "precip_mm": today_mm,
        "daily": daily,
        "by_date": by_date,
        "fetched_at": datetime.now(tz=LOCAL_TZ).isoformat(),
        "disclaimer": "Precipitación diaria (mm = l/m²). Modelo ~9 km.",
    }
