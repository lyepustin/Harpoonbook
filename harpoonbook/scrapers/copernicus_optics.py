from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from harpoonbook.config import LOCAL_TZ, now_local


def has_copernicus_credentials() -> bool:
    home = Path.home()
    if (home / ".copernicusmarine" / ".copernicusmarine-credentials").exists():
        return True
    if (home / ".copernicusmarine-credentials").exists():
        return True
    if os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME") and os.environ.get(
        "COPERNICUSMARINE_SERVICE_PASSWORD"
    ):
        return True
    return False

# Prevision BGC Mediterraneo 4.2 km (atenuacion de luz -> proxy Secchi)
CMEMS_OPTICS_DATASET = "cmems_mod_med_bgc-optics_anfc_4.2km_P1D-m"
CMEMS_OPTICS_VARIABLE = "kd490"
# Secchi ~ 1.7 / Kd490 (m). Referencia oceanografica habitual.
SECCHI_KD_FACTOR = 1.7


def kd490_to_secchi_m(kd490: float) -> float | None:
    if kd490 is None or kd490 <= 0:
        return None
    return round(SECCHI_KD_FACTOR / float(kd490), 1)


def _optics_stub(
    *,
    date_key: str,
    status: str,
    lat: float | None = None,
    lon: float | None = None,
    error: str | None = None,
    disclaimer: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "source": "copernicus_bgc",
        "status": status,
        "forecast_date": date_key,
        "dataset": CMEMS_OPTICS_DATASET,
        "hourly": [],
    }
    if lat is not None:
        out["lat"] = lat
    if lon is not None:
        out["lon"] = lon
    if error:
        out["error"] = error
    if disclaimer:
        out["disclaimer"] = disclaimer
    return out


def _payload_from_hourly(
    hourly: list[dict[str, Any]],
    *,
    date_key: str,
    lat: float,
    lon: float,
) -> dict[str, Any]:
    valid_secchi = [h["secchi_est_m"] for h in hourly if h.get("secchi_est_m")]
    return {
        "source": "copernicus_bgc",
        "status": "ok" if hourly else "missing",
        "forecast_date": date_key,
        "dataset": CMEMS_OPTICS_DATASET,
        "lat": lat,
        "lon": lon,
        "hourly": hourly,
        "secchi_day_mean_m": round(sum(valid_secchi) / len(valid_secchi), 1) if valid_secchi else None,
        "disclaimer": "Claridad base del medio (~4 km). No captura turbidez local en calas.",
    }


def fetch_copernicus_optics_range(
    lat: float,
    lon: float,
    *,
    start_date: date | None = None,
    num_days: int = 1,
) -> dict[str, dict[str, Any]]:
    """
    Kd490/Secchi para start_date .. +num_days-1 en **un** download NetCDF.
    Devuelve { "YYYY-MM-DD": optics_payload }.
    """
    start_date = start_date or now_local().date()
    num_days = max(1, num_days)
    end_date = start_date + timedelta(days=num_days - 1)

    stubs = {
        (start_date + timedelta(days=i)).isoformat(): None
        for i in range(num_days)
    }

    if not has_copernicus_credentials():
        return {
            k: _optics_stub(
                date_key=k,
                status="no_credentials",
                lat=lat,
                lon=lon,
                disclaimer="Ejecuta: copernicusmarine login",
            )
            for k in stubs
        }

    try:
        import xarray as xr
        from copernicusmarine import subset
    except ImportError as exc:
        return {
            k: _optics_stub(
                date_key=k,
                status="error",
                lat=lat,
                lon=lon,
                error=f"Dependencias faltantes: {exc}",
            )
            for k in stubs
        }

    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=LOCAL_TZ)
    # end exclusivo + margen: el subset usa end_datetime inclusivo en la práctica.
    end = datetime(end_date.year, end_date.month, end_date.day, 23, 59, tzinfo=LOCAL_TZ)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "optics.nc"
            cm_log = logging.getLogger("copernicusmarine")
            prev_level = cm_log.level
            cm_log.setLevel(logging.WARNING)
            try:
                subset(
                    dataset_id=CMEMS_OPTICS_DATASET,
                    variables=[CMEMS_OPTICS_VARIABLE],
                    minimum_longitude=lon - 0.05,
                    maximum_longitude=lon + 0.05,
                    minimum_latitude=lat - 0.05,
                    maximum_latitude=lat + 0.05,
                    start_datetime=start.isoformat(),
                    end_datetime=end.isoformat(),
                    minimum_depth=0.0,
                    maximum_depth=0.0,
                    output_filename=str(out),
                )
            finally:
                cm_log.setLevel(prev_level)

            by_day_hourly: dict[str, list[dict[str, Any]]] = {k: [] for k in stubs}
            with xr.open_dataset(out) as ds:
                lat_name = "latitude" if "latitude" in ds.coords else "lat"
                lon_name = "longitude" if "longitude" in ds.coords else "lon"
                point = ds.sel({lat_name: lat, lon_name: lon}, method="nearest")

                time_dim = "time" if "time" in point.dims else next(
                    d for d in point.dims if "time" in d.lower()
                )
                kd = point[CMEMS_OPTICS_VARIABLE]

                for t_idx in range(point.sizes[time_dim]):
                    t_val = point[time_dim].values[t_idx]
                    kd_val = float(kd.isel({time_dim: t_idx}).values)
                    t_str = str(t_val)[:19]
                    dt = datetime.fromisoformat(t_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=LOCAL_TZ)
                    else:
                        dt = dt.astimezone(LOCAL_TZ)
                    date_key = dt.date().isoformat()
                    if date_key not in by_day_hourly:
                        continue
                    secchi = kd490_to_secchi_m(kd_val)
                    by_day_hourly[date_key].append(
                        {
                            "time": dt.isoformat(),
                            "local_hour": dt.hour,
                            "kd490": round(kd_val, 4),
                            "secchi_est_m": secchi,
                        }
                    )
    except Exception as exc:
        return {
            k: _optics_stub(
                date_key=k,
                status="error",
                lat=lat,
                lon=lon,
                error=str(exc),
            )
            for k in stubs
        }

    return {
        k: _payload_from_hourly(hourly, date_key=k, lat=lat, lon=lon)
        for k, hourly in by_day_hourly.items()
    }


def fetch_copernicus_optics(
    lat: float,
    lon: float,
    forecast_date: date | None = None,
) -> dict[str, Any]:
    """
    Kd490 (1/m) desde Copernicus Med BGC forecast.
    Convierte a profundidad Secchi estimada (m) como claridad base del medio.
    """
    forecast_date = forecast_date or now_local().date()
    by_day = fetch_copernicus_optics_range(
        lat, lon, start_date=forecast_date, num_days=1
    )
    return by_day.get(forecast_date.isoformat()) or _optics_stub(
        date_key=forecast_date.isoformat(),
        status="missing",
        lat=lat,
        lon=lon,
    )
