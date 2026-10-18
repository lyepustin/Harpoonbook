from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import requests

from harpoonbook.config import COMPASS_16_ES, LOCAL_TZ, now_local
from harpoonbook.scrapers.marine_common import seaward_query_point

WINDY_FORECAST_URL = "https://node.windy.com/forecast/{model}/{lat}/{lon}"
# Igual que la web con product=ecmwfWaves: viento ECMWF HRES + olas ECMWF-WAM.
WINDY_MODEL_ATMOS = "ecmwf"
WINDY_MODEL_MARINE = "waves"
# En costa el endpoint a veces resuelve celda de tierra (elev>0) con viento/temp inflados.
_MAX_ATMOS_ELEV_M = 5.0
# Hoy + N días siguientes (fiabilidad ~80% en el panel de Windy).
WINDY_LOOKAHEAD_DAYS = 3

_WINDY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; pesca-predictor/0.1)",
    "Origin": "https://www.windy.com",
    "Referer": "https://www.windy.com/",
    "Accept": "application/json",
}

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
    return COMPASS_16_ES[idx]


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


def _header_elevation(payload: dict[str, Any] | None) -> float | None:
    if not payload:
        return None
    elev = (payload.get("header") or {}).get("elevation")
    if elev is None:
        return None
    try:
        return float(elev)
    except (TypeError, ValueError):
        return None


def _day_max_temp_c(payload: dict[str, Any] | None) -> float | None:
    if not payload:
        return None
    temps: list[float] = []
    for day_entries in (payload.get("data") or {}).values():
        for entry in day_entries or []:
            temp = entry.get("temp")
            if temp is None:
                continue
            try:
                temps.append(float(temp) - 273.15)
            except (TypeError, ValueError):
                continue
    return max(temps) if temps else None


def _looks_like_land_atmos(payload: dict[str, Any] | None) -> bool:
    """Celda costera de tierra: elev>0 o picos térmicos irreales sobre el agua."""
    elev = _header_elevation(payload)
    if elev is not None and elev > _MAX_ATMOS_ELEV_M:
        return True
    max_temp = _day_max_temp_c(payload)
    # Elev 0 a veces sigue siendo tierra mal etiquetada (temp diurna >32 °C).
    return max_temp is not None and max_temp >= 32.0


def _fetch_atmos_prefer_sea(
    lat: float,
    lon: float,
    coast_facing: str | None = None,
    sea_temp_c: float | None = None,
) -> tuple[dict[str, Any] | None, float, float]:
    """
    ECMWF atmos preferiendo celda de mar (como el picker costero de Windy).

    Si el spot resuelve tierra (elevación o temperatura diurna alta), prueba
    offsets mar adentro y elige la celda más “marina” (temp cercana al agua).
    """
    facing = coast_facing or "E"
    target_temp = float(sea_temp_c) if sea_temp_c is not None else 27.0

    candidates: list[tuple[tuple[float, float, float], dict[str, Any], float, float]] = []

    def _consider(payload: dict[str, Any] | None, qlat: float, qlon: float, offset: float) -> None:
        if not payload or _looks_like_land_atmos(payload):
            return
        elev = _header_elevation(payload) or 0.0
        max_temp = _day_max_temp_c(payload)
        temp_gap = abs((max_temp if max_temp is not None else target_temp) - target_temp)
        # Menor elev, temp más cercana al agua, offset más corto.
        score = (elev, temp_gap, offset)
        candidates.append((score, payload, qlat, qlon))

    spot = _fetch_model(WINDY_MODEL_ATMOS, lat, lon)
    _consider(spot, lat, lon, 0.0)

    for offset in (0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.16):
        qlat, qlon = seaward_query_point(lat, lon, facing, offset_deg=offset)
        _consider(_fetch_model(WINDY_MODEL_ATMOS, qlat, qlon), qlat, qlon, offset)

    if candidates:
        candidates.sort(key=lambda c: c[0])
        _, payload, qlat, qlon = candidates[0]
        return payload, qlat, qlon

    return spot, lat, lon


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
    return datetime(
        forecast_date.year, forecast_date.month, forecast_date.day, hour, 0, tzinfo=LOCAL_TZ
    ).isoformat()


def _entries_by_ts(payload: dict[str, Any], date_key: str) -> dict[int, dict[str, Any]]:
    by_ts: dict[int, dict[str, Any]] = {}
    for entry in payload.get("data", {}).get(date_key, []):
        ts = entry.get("ts")
        if ts is not None:
            by_ts[int(ts)] = entry
    return by_ts


def _overlay_fields(
    target: dict[str, Any], source: dict[str, Any] | None, fields: tuple[str, ...]
) -> None:
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


def _build_day_payload(
    *,
    marine: dict[str, Any],
    atmos: dict[str, Any] | None,
    forecast_date: date,
    lat: float,
    lon: float,
    atmos_lat: float,
    atmos_lon: float,
) -> dict[str, Any] | None:
    date_key = forecast_date.isoformat()
    day_entries = list(marine.get("data", {}).get(date_key, []))
    if not day_entries:
        return None

    header = marine.get("header", {})
    step_hours = int(header.get("step", 3))
    atmos_by_ts = _entries_by_ts(atmos, date_key) if atmos else {}

    hourly: list[dict[str, Any]] = []
    for entry in day_entries:
        merged = dict(entry)
        ts = merged.get("ts")
        if ts is not None:
            _overlay_fields(merged, atmos_by_ts.get(int(ts)), _ATMOS_FIELDS)
        hourly.append(_slot_from_entry(merged, forecast_date=forecast_date))

    hourly.sort(key=lambda slot: slot["local_hour"])

    atmos_header = (atmos or {}).get("header", {})
    atmos_model = atmos_header.get("model", "ECMWF")
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
        "atmos_lat": atmos_lat,
        "atmos_lon": atmos_lon,
        "atmos_elevation_m": _header_elevation(atmos),
        "marine_elevation_m": _header_elevation(marine),
        "hourly": hourly,
    }


def fetch_windy_forecast_range(
    lat: float,
    lon: float,
    *,
    start_date: date | None = None,
    num_days: int | None = None,
    coast_facing: str | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Una sola petición atmos+olas; franjas por día para start_date .. start_date+num_days-1.

    Por defecto: hoy + WINDY_LOOKAHEAD_DAYS (3) = 4 días.
    Devuelve { "YYYY-MM-DD": windy_payload }.
    """
    start_date = start_date or now_local().date()
    if num_days is None:
        num_days = 1 + WINDY_LOOKAHEAD_DAYS

    marine = _fetch_model(WINDY_MODEL_MARINE, lat, lon)
    if not marine:
        return {}

    sea_temp_c = _k_to_c((marine.get("header") or {}).get("sst"))
    atmos, atmos_lat, atmos_lon = _fetch_atmos_prefer_sea(
        lat, lon, coast_facing=coast_facing, sea_temp_c=sea_temp_c
    )

    out: dict[str, dict[str, Any]] = {}
    for offset in range(max(1, num_days)):
        day = start_date + timedelta(days=offset)
        payload = _build_day_payload(
            marine=marine,
            atmos=atmos,
            forecast_date=day,
            lat=lat,
            lon=lon,
            atmos_lat=atmos_lat,
            atmos_lon=atmos_lon,
        )
        if payload:
            out[day.isoformat()] = payload
    return out


def fetch_windy_hourly_forecast(
    lat: float,
    lon: float,
    forecast_date: date | None = None,
    coast_facing: str | None = None,
) -> dict[str, Any]:
    """
    Forecast horario (franjas de 3 h) alineado con el panel de Windy (ecmwfWaves):
    - Viento/temp: ECMWF HRES (`ecmwf`), preferiendo celda de mar en costa
    - Olas/swell: ECMWF-WAM (`waves`)
    """
    forecast_date = forecast_date or now_local().date()
    by_day = fetch_windy_forecast_range(
        lat,
        lon,
        start_date=forecast_date,
        num_days=1,
        coast_facing=coast_facing,
    )
    return by_day.get(forecast_date.isoformat()) or {}


def fetch_windy_waves_wind_features(
    lat: float,
    lon: float,
    detail_lat: float | None = None,
    detail_lon: float | None = None,
    forecast_date: date | None = None,
    coast_facing: str | None = None,
    *,
    lookahead_days: int | None = None,
) -> dict[str, Any]:
    """
    Features Windy del día + opcionalmente `windy_by_date` (hoy y siguientes).

    Con lookahead_days (p.ej. 3) hace **una** petición y rellena hoy + N días.
    """
    _ = detail_lat, detail_lon
    forecast_date = forecast_date or now_local().date()
    if lookahead_days is None:
        windy = fetch_windy_hourly_forecast(
            lat=lat,
            lon=lon,
            forecast_date=forecast_date,
            coast_facing=coast_facing,
        )
        by_day: dict[str, dict[str, Any]] = (
            {forecast_date.isoformat(): windy} if windy else {}
        )
    else:
        by_day = fetch_windy_forecast_range(
            lat,
            lon,
            start_date=forecast_date,
            num_days=1 + max(0, lookahead_days),
            coast_facing=coast_facing,
        )
        windy = by_day.get(forecast_date.isoformat()) or {}

    if not windy and not by_day:
        return {}

    now = now_local()
    slots = (windy or {}).get("hourly", [])
    current: dict[str, Any] | None = None
    if slots:
        current = min(
            slots,
            key=lambda slot: abs(datetime.fromisoformat(slot["time"]).hour - now.hour),
        )

    flat: dict[str, Any] = {"windy": windy or {}, "windy_by_date": by_day}
    if current:
        flat.update(
            {
                "wind_speed": current.get("wind_speed_kmh"),
                "gusts": current.get("wind_gusts_kmh"),
                "wave_height": current.get("wave_height_m"),
                "wave_period": current.get("wave_period_s"),
                "water_temp_c": (windy or {}).get("water_temp_c"),
            }
        )
    elif windy:
        flat["water_temp_c"] = windy.get("water_temp_c")
    return flat
