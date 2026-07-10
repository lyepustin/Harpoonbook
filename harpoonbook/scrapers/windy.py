from __future__ import annotations

from datetime import date, datetime
from typing import Any

import requests

from harpoonbook.config import LOCAL_TZ, now_local

WINDY_FORECAST_URL = "https://node.windy.com/forecast/{model}/{lat}/{lon}"
# Igual que la web con product=ecmwfWaves: viento ECMWF HRES + olas ECMWF-WAM.
WINDY_MODEL_ATMOS = "ecmwf"
WINDY_MODEL_MARINE = "waves"

_WINDY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; pesca-predictor/0.1)",
    "Origin": "https://www.windy.com",
    "Referer": "https://www.windy.com/",
    "Accept": "application/json",
}

_COMPASS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]

_ATMOS_FIELDS = ("wind", "gust", "temp", "feelTemp", "windDir", "rh", "pressure")
_MARINE_FIELDS = (
    "waves", "wavesDir", "wavesPeriod", "wavesPower",
    "swell1", "swell1Dir", "swell1Period",
    "swell2", "swell2Dir", "swell2Period",
)


def _deg_to_compass(deg: float | int | None) -> str | None:
    if deg is None:
        return None
    idx = int((float(deg) + 11.25) // 22.5) % 16
    return _COMPASS[idx]


def _ms_to_kmh(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value) * 3.6, 1)


def _k_to_c(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value) - 273.15, 1)


def _round_or_none(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _fetch_model(model: str, lat: float, lon: float) -> dict[str, Any] | None:
    try:
        resp = requests.get(
            WINDY_FORECAST_URL.format(model=model, lat=lat, lon=lon),
            headers=_WINDY_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _parse_orig_date(orig: str) -> datetime | None:
    try:
        if orig.endswith("Z"):
            orig = orig[:-1] + "+00:00"
        return datetime.fromisoformat(orig)
    except ValueError:
        return None


def _local_hour_from_entry(entry: dict[str, Any]) -> int:
    orig = entry.get("origDate")
    if isinstance(orig, str):
        dt = _parse_orig_date(orig)
        if dt is not None and dt.tzinfo is not None:
            return dt.astimezone(LOCAL_TZ).hour
    return int(entry.get("hour", 0))


def _local_time_iso(entry: dict[str, Any], forecast_date: date) -> str:
    orig = entry.get("origDate")
    if isinstance(orig, str):
        dt = _parse_orig_date(orig)
        if dt is not None and dt.tzinfo is not None:
            return dt.astimezone(LOCAL_TZ).isoformat()
    hour = _local_hour_from_entry(entry)
    return datetime(forecast_date.year, forecast_date.month, forecast_date.day, hour, 0, tzinfo=LOCAL_TZ).isoformat()


def _entries_by_ts(payload: dict[str, Any], date_key: str) -> dict[int, dict[str, Any]]:
    by_ts: dict[int, dict[str, Any]] = {}
    for entry in payload.get("data", {}).get(date_key, []):
        ts = entry.get("ts")
        if ts is not None:
            by_ts[int(ts)] = entry
    return by_ts


def _overlay_fields(target: dict[str, Any], source: dict[str, Any] | None, fields: tuple[str, ...]) -> None:
    if not source:
        return
    for key in fields:
        if source.get(key) is not None:
            target[key] = source[key]


def _slot_from_entry(entry: dict[str, Any], forecast_date: date) -> dict[str, Any]:
    wind_dir = entry.get("windDir")
    wave_dir = entry.get("wavesDir")
    swell_dir = entry.get("swell1Dir")
    is_day_raw = entry.get("isDay")

    return {
        "local_hour": _local_hour_from_entry(entry),
        "time": _local_time_iso(entry, forecast_date),
        "is_day": bool(is_day_raw is not None and float(is_day_raw) > 0.5),
        "temp_c": _k_to_c(entry.get("temp")),
        "wind_speed_kmh": _ms_to_kmh(entry.get("wind")),
        "wind_gusts_kmh": _ms_to_kmh(entry.get("gust")),
        "wind_direction_deg": wind_dir,
        "wind_direction": _deg_to_compass(wind_dir),
        "wave_height_m": _round_or_none(entry.get("waves")),
        "wave_direction_deg": wave_dir,
        "wave_direction": _deg_to_compass(wave_dir),
        "wave_period_s": _round_or_none(entry.get("wavesPeriod")),
        "swell1_height_m": _round_or_none(entry.get("swell1")),
        "swell1_direction_deg": swell_dir,
        "swell1_direction": _deg_to_compass(swell_dir),
        "swell1_period_s": _round_or_none(entry.get("swell1Period")),
        "wave_power_kw_m": _round_or_none(entry.get("wavesPower"), digits=2),
    }


def fetch_windy_hourly_forecast(
    lat: float,
    lon: float,
    forecast_date: date | None = None,
) -> dict[str, Any]:
    """
    Forecast horario (franjas de 3 h) alineado con el panel de Windy (ecmwfWaves):
    - Viento/temp: ECMWF HRES (`ecmwf`)
    - Olas/swell: ECMWF-WAM (`waves`)
    """
    forecast_date = forecast_date or now_local().date()
    date_key = forecast_date.isoformat()

    marine = _fetch_model(WINDY_MODEL_MARINE, lat, lon)
    if not marine:
        return {}

    atmos = _fetch_model(WINDY_MODEL_ATMOS, lat, lon)
    header = marine.get("header", {})
    step_hours = int(header.get("step", 3))
    atmos_by_ts = _entries_by_ts(atmos, date_key) if atmos else {}

    day_entries = list(marine.get("data", {}).get(date_key, []))
    if not day_entries:
        return {}

    hourly: list[dict[str, Any]] = []
    for entry in day_entries:
        merged = dict(entry)
        ts = merged.get("ts")
        if ts is not None:
            _overlay_fields(merged, atmos_by_ts.get(int(ts)), _ATMOS_FIELDS)
        hourly.append(_slot_from_entry(merged, forecast_date=forecast_date))

    hourly.sort(key=lambda slot: slot["local_hour"])

    atmos_model = (atmos or {}).get("header", {}).get("model", "ECMWF")
    marine_model = header.get("model", "ECMWF-WAM")

    return {
        "source": "windy",
        "model": f"{atmos_model} + {marine_model}",
        "forecast_date": date_key,
        "step_hours": step_hours,
        "timezone": header.get("tzName", "Europe/Madrid"),
        "water_temp_c": _k_to_c(header.get("sst")),
        "ref_time": header.get("refTime"),
        "update": header.get("update"),
        "lat": lat,
        "lon": lon,
        "hourly": hourly,
    }


def fetch_windy_waves_wind_features(
    lat: float,
    lon: float,
    detail_lat: float | None = None,
    detail_lon: float | None = None,
    forecast_date: date | None = None,
) -> dict[str, Any]:
    _ = detail_lat, detail_lon
    windy = fetch_windy_hourly_forecast(lat=lat, lon=lon, forecast_date=forecast_date)
    if not windy:
        return {}

    now = now_local()
    slots = windy.get("hourly", [])
    current: dict[str, Any] | None = None
    if slots:
        current = min(
            slots,
            key=lambda slot: abs(datetime.fromisoformat(slot["time"]).hour - now.hour),
        )

    flat: dict[str, Any] = {"windy": windy}
    if current:
        flat.update(
            {
                "wind_speed": current.get("wind_speed_kmh"),
                "gusts": current.get("wind_gusts_kmh"),
                "wave_height": current.get("wave_height_m"),
                "wave_period": current.get("wave_period_s"),
                "water_temp_c": windy.get("water_temp_c"),
            }
        )
    return flat
