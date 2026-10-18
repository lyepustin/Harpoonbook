from __future__ import annotations

import json
import math
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from harpoonbook.config import BOTTOM_TYPES, COMPASS_16_ES, RUNOFF_RISK_TYPES, compass_es, now_local
from harpoonbook.db import list_snapshots_for_day
from harpoonbook.merge_snapshots import merge_snapshots_for_day
from harpoonbook.scrapers.open_meteo_weather import PRECIP_LOOKBACK_DAYS

# #region agent log
_DEBUG_LOG_PATH = Path(__file__).resolve().parents[1] / "debug-2ab5e2.log"


def _agent_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any],
    *,
    run_id: str = "pre-fix",
) -> None:
    try:
        payload = {
            "sessionId": "2ab5e2",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


# #endregion


# Kd490 Copernicus = claridad de masa de agua (~4 km). Orilla usa escala + techo propios.
OFFSHORE_NOTE = "Claridad de masa de agua (~4 km). No refleja turbidez de orilla."
NEARSHORE_SECCHI_SCALE = 0.18
NEARSHORE_SECCHI_CAP_M = 12.0
DEFAULT_LOOKBACK_DAYS = 3

BOTTOM_TYPE_FACTOR = {
    "roca": 1.08,
    "mixto": 1.0,
    "arena": 0.88,
}

# Cómo de fuerte aplica el spot la escorrentía (alto = barrancos a calas).
RUNOFF_RISK_WEIGHT = {
    "alto": 1.0,
    "medio": 0.65,
    "bajo": 0.35,
}

# Peso de lluvia por antigüedad (día 0 = as_of).
RUNOFF_DAY_WEIGHTS = (1.0, 0.7, 0.4, 0.2)


def _nearshore_secchi(secchi_m: float | None) -> float | None:
    if secchi_m is None:
        return None
    return round(min(NEARSHORE_SECCHI_CAP_M, float(secchi_m) * NEARSHORE_SECCHI_SCALE), 1)


def _clamp(value: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, value))


def _score_label(score: float) -> str:
    if score >= 8.0:
        return "muy buena"
    if score >= 6.0:
        return "buena"
    if score >= 4.0:
        return "regular"
    if score >= 2.0:
        return "mala"
    return "muy mala"


def _meters_from_score(score: float) -> tuple[float, float, float]:
    """Rango orientativo de visibilidad (m) cuando no hay Secchi satelital."""
    mid = max(0.5, score * 1.1)
    spread = max(0.8, (10.0 - score) * 0.35 + 0.5)
    return (
        round(max(0.5, mid - spread), 1),
        round(mid, 1),
        round(mid + spread, 1),
    )


def _meters_from_secchi(secchi_m: float, factor: float) -> tuple[float, float, float]:
    """Secchi base ajustada por condiciones fisicas del momento."""
    mid = max(0.5, secchi_m * factor)
    spread = max(0.5, mid * 0.25)
    return (
        round(max(0.5, mid - spread), 1),
        round(mid, 1),
        round(mid + spread, 1),
    )


def _bottom_type_factor(bottom_type: str | None) -> tuple[float, str | None]:
    key = (bottom_type or "mixto").lower()
    if key not in BOTTOM_TYPE_FACTOR:
        key = "mixto"
    factor = BOTTOM_TYPE_FACTOR[key]
    label = BOTTOM_TYPES.get(key)
    reason = f"Fondo {label.split('(')[0].strip().lower()}" if key != "mixto" else None
    return factor, reason


def _normalize_runoff_risk(runoff_risk: str | None) -> str:
    key = (runoff_risk or "medio").strip().lower()
    return key if key in RUNOFF_RISK_WEIGHT else "medio"


def _precip_by_date(precipitation: dict[str, Any] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    if not precipitation:
        return out
    for row in precipitation.get("daily") or []:
        day_s = row.get("date")
        mm = row.get("precip_mm")
        if not day_s or not isinstance(mm, (int, float)):
            continue
        out[str(day_s)] = float(mm)
    # Compat: un solo día sin serie
    if not out and isinstance(precipitation.get("precip_mm"), (int, float)):
        day_s = precipitation.get("forecast_date") or precipitation.get("as_of")
        if day_s:
            out[str(day_s)] = float(precipitation["precip_mm"])
    return out


def _weighted_precip_mm(
    precip_map: dict[str, float],
    as_of: date,
    *,
    lookback_days: int = PRECIP_LOOKBACK_DAYS,
) -> tuple[float, float, list[dict[str, Any]]]:
    """Precipitación ponderada por antigüedad (lluvia reciente pesa más)."""
    weights = RUNOFF_DAY_WEIGHTS
    breakdown: list[dict[str, Any]] = []
    weighted = 0.0
    peak = 0.0
    max_offset = min(lookback_days, len(weights) - 1)
    for offset in range(0, max_offset + 1):
        day = as_of - timedelta(days=offset)
        mm = precip_map.get(day.isoformat())
        if mm is None:
            continue
        w = weights[offset]
        weighted += mm * w
        peak = max(peak, mm)
        breakdown.append({"date": day.isoformat(), "precip_mm": round(mm, 1), "weight": w})
    return round(weighted, 1), round(peak, 1), breakdown


def _runoff_base_factor(
    weighted_mm: float,
    *,
    runoff_risk: str = "medio",
) -> tuple[float, list[str], dict[str, Any]]:
    """
    Factor de escorrentía (orilla). Umbrales en mm ponderados ≈ l/m² recientes.
    El riesgo del spot atenúa la penalización (bajo ≈ poca desembocadura).
    """
    risk = _normalize_runoff_risk(runoff_risk)
    risk_w = RUNOFF_RISK_WEIGHT[risk]
    meta: dict[str, Any] = {
        "weighted_precip_mm": weighted_mm,
        "runoff_risk": risk,
        "risk_weight": risk_w,
        "threshold": None,
    }
    if weighted_mm < 20:
        return 1.0, [], meta

    if weighted_mm >= 70:
        base = 0.30
        meta["threshold"] = ">=70"
        label = "muy fuerte"
    elif weighted_mm >= 40:
        base = 0.55
        meta["threshold"] = ">=40"
        label = "fuerte"
    else:
        base = 0.85
        meta["threshold"] = ">=20"
        label = "moderada"

    factor = 1.0 - (1.0 - base) * risk_w
    factor = round(_clamp(factor, 0.2, 1.0), 2)
    risk_label = RUNOFF_RISK_TYPES.get(risk, risk).split("(")[0].strip().lower()
    reasons = [
        f"Lluvia {label} reciente (~{weighted_mm:.0f} mm ponderados) — "
        f"escorrentía (riesgo {risk_label})"
    ]
    return factor, reasons, meta


def _runoff_wave_boost(
    base_factor: float,
    wave_height: float | None,
) -> tuple[float, str | None]:
    """Oleaje remueve barro recién depositado en orilla."""
    if base_factor >= 0.98:
        return base_factor, None
    if not isinstance(wave_height, (int, float)) or wave_height < 0.35:
        return base_factor, None
    boosted = round(_clamp(base_factor * 0.85, 0.15, 1.0), 2)
    return boosted, f"Oleaje remueve sedimento de escorrentía ({wave_height:.1f} m)"


def _runoff_from_features(
    features: dict[str, Any],
    *,
    as_of: date | None = None,
    runoff_risk: str | None = None,
) -> dict[str, Any]:
    precip = features.get("precipitation") or {}
    as_of = as_of or now_local().date()
    if precip.get("as_of"):
        try:
            # Si el bloque no cubre as_of (datos viejos), igual usamos fechas absolutas.
            date.fromisoformat(str(precip["as_of"]))
        except ValueError:
            pass
    precip_map = _precip_by_date(precip)
    weighted, peak, breakdown = _weighted_precip_mm(precip_map, as_of)
    factor, reasons, meta = _runoff_base_factor(weighted, runoff_risk=runoff_risk)
    return {
        "runoff_factor": factor,
        "runoff_reasons": reasons,
        "weighted_precip_mm": weighted,
        "peak_precip_mm": peak,
        "breakdown": breakdown,
        "precip_status": precip.get("status"),
        **meta,
    }


def _compass_to_deg(label: str | None) -> float | None:
    c = compass_es(label)
    if not c or c not in COMPASS_16_ES:
        return None
    return COMPASS_16_ES.index(c) * 22.5


def _onshore_alignment(coast_facing: str | None, from_dir: str | None) -> float | None:
    """
    1.0 = viene de cara a la costa (max remocion), 0 = paralelo/offshore.
    """
    coast_deg = _compass_to_deg(coast_facing)
    from_deg = _compass_to_deg(from_dir)
    if coast_deg is None or from_deg is None:
        return None
    diff = abs((from_deg - coast_deg + 180.0) % 360.0 - 180.0)
    if diff > 90.0:
        return 0.0
    return math.cos(math.radians(diff))


def _settling_factor_from_recent(
    max_swell_period: float,
    max_swell_height: float,
    max_wave_height: float,
    *,
    agitated_days: int = 0,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> tuple[float, list[str]]:
    """
    Penaliza si hubo mar de fondo/oleaje en dias previos (agua aun asentandose).
    Incluye agitación moderada repetida típica de orilla mediterránea.
    """
    factor = 1.0
    reasons: list[str] = []

    if max_swell_period >= 10:
        factor *= 0.72
        reasons.append(f"Mar de fondo reciente fuerte (max {max_swell_period:.0f} s)")
    elif max_swell_period >= 8:
        factor *= 0.85
        reasons.append(f"Mar de fondo reciente moderado (max {max_swell_period:.0f} s)")
    elif max_swell_period >= 6:
        factor *= 0.93
        reasons.append(f"Algo de mar de fondo reciente (max {max_swell_period:.0f} s)")

    if max_swell_height >= 1.5:
        factor *= 0.82
        reasons.append(f"Swell reciente alto (max {max_swell_height:.1f} m)")
    elif max_swell_height >= 0.8:
        factor *= 0.92
        reasons.append(f"Swell reciente moderado (max {max_swell_height:.1f} m)")
    elif max_swell_height >= 0.35:
        factor *= 0.94
        reasons.append(f"Swell reciente en orilla (max {max_swell_height:.1f} m)")

    if max_wave_height >= 1.2:
        factor *= 0.88
        reasons.append(f"Oleaje reciente removio fondo (max {max_wave_height:.1f} m)")
    elif max_wave_height >= 0.4:
        factor *= 0.9
        reasons.append(f"Oleaje reciente removio orilla (max {max_wave_height:.1f} m)")

    if agitated_days >= 3:
        factor *= 0.82
        reasons.append(
            f"Orilla removida varios dias ({agitated_days}/{lookback_days})"
        )
    elif agitated_days >= 2:
        factor *= 0.9
        reasons.append(
            f"Orilla removida dias previos ({agitated_days}/{lookback_days})"
        )

    return round(_clamp(factor, 0.45, 1.0), 2), reasons


def _windy_day_peaks(windy: dict[str, Any]) -> dict[str, float]:
    max_swell_period = 0.0
    max_swell_height = 0.0
    max_wave_height = 0.0
    max_wave_power = 0.0
    max_wind = 0.0

    for slot in windy.get("hourly") or []:
        sp = slot.get("swell1_period_s")
        sh = slot.get("swell1_height_m")
        wh = slot.get("wave_height_m")
        wp = slot.get("wave_power_kw_m")
        ws = slot.get("wind_speed_kmh")
        if isinstance(sp, (int, float)):
            max_swell_period = max(max_swell_period, float(sp))
        if isinstance(sh, (int, float)):
            max_swell_height = max(max_swell_height, float(sh))
        if isinstance(wh, (int, float)):
            max_wave_height = max(max_wave_height, float(wh))
        if isinstance(wp, (int, float)):
            max_wave_power = max(max_wave_power, float(wp))
        if isinstance(ws, (int, float)):
            max_wind = max(max_wind, float(ws))

    return {
        "max_swell_period_s": round(max_swell_period, 1),
        "max_swell_height_m": round(max_swell_height, 2),
        "max_wave_height_m": round(max_wave_height, 2),
        "max_wave_power_kw_m": round(max_wave_power, 2),
        "max_wind_kmh": round(max_wind, 1),
    }


def _day_is_agitated(peaks: dict[str, float]) -> bool:
    return (
        peaks.get("max_wave_height_m", 0) >= 0.35
        or peaks.get("max_swell_height_m", 0) >= 0.3
        or peaks.get("max_wave_power_kw_m", 0) >= 0.25
    )


def summarize_recent_days(
    db_path: str,
    spot_id: str,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    end_date: date | None = None,
) -> dict[str, Any]:
    """
    Resume swell/oleaje/viento de los ultimos N dias guardados (snapshots).
    Excluye el dia end_date (normalmente hoy): mira dias anteriores.
    """
    end_date = end_date or now_local().date()
    daily: list[dict[str, Any]] = []
    peaks = {
        "max_swell_period_s": 0.0,
        "max_swell_height_m": 0.0,
        "max_wave_height_m": 0.0,
        "max_wave_power_kw_m": 0.0,
        "max_wind_kmh": 0.0,
    }

    for offset in range(1, lookback_days + 1):
        day = end_date - timedelta(days=offset)
        snapshots = list_snapshots_for_day(db_path, snapshot_date=day, spot_id=spot_id)
        if not snapshots:
            continue
        merged = merge_snapshots_for_day(snapshots) or {}
        windy = merged.get("windy") or {}
        day_peaks = _windy_day_peaks(windy)
        hourly = windy.get("hourly") or []
        max_wave_slot = max(
            (s for s in hourly if isinstance(s.get("wave_height_m"), (int, float))),
            key=lambda s: float(s.get("wave_height_m") or 0),
            default=None,
        )
        daily.append({
            "date": day.isoformat(),
            **day_peaks,
            "max_wave_hour": (max_wave_slot or {}).get("local_hour"),
            "max_wave_dir": (max_wave_slot or {}).get("wave_direction"),
            "max_wave_from_snap": (max_wave_slot or {}).get("_from_snapshot_id"),
            "max_wave_captured_at": (max_wave_slot or {}).get("_from_captured_at"),
            "n_hourly": len(hourly),
        })
        for key in peaks:
            peaks[key] = max(peaks[key], day_peaks[key])

    agitated_days = sum(1 for d in daily if _day_is_agitated(d))
    settling_factor, settling_reasons = _settling_factor_from_recent(
        peaks["max_swell_period_s"],
        peaks["max_swell_height_m"],
        peaks["max_wave_height_m"],
        agitated_days=agitated_days,
        lookback_days=lookback_days,
    )

    # #region agent log
    _agent_log(
        "B",
        "visibility.py:summarize_recent_days",
        "settling memory lookback peaks",
        {
            "end_date": end_date.isoformat(),
            "lookback_days": lookback_days,
            "days_with_data": len(daily),
            "agitated_days": agitated_days,
            "daily": daily,
            "peaks": peaks,
            "settling_factor": settling_factor,
            "settling_reasons": settling_reasons,
        },
    )
    # #endregion

    return {
        "lookback_days": lookback_days,
        "days_with_data": len(daily),
        "agitated_days": agitated_days,
        "daily": daily,
        **peaks,
        "settling_factor": settling_factor,
        "settling_reasons": settling_reasons,
    }


def _nearshore_slot_factor(
    slot: dict[str, Any],
    tide: dict[str, Any],
    *,
    coast_facing: str | None = None,
) -> tuple[float, list[str]]:
    """
    Factor orilla (pesca submarina costera).
    Umbrales mas bajos que mar abierto: 0.3–0.5 m ya remueven arena en 2–10 m.
    """
    factor = 1.0
    reasons: list[str] = []
    penalties_hit: list[str] = []

    swell_period = slot.get("swell1_period_s")
    swell_height = slot.get("swell1_height_m")
    wave_height = slot.get("wave_height_m")
    wave_power = slot.get("wave_power_kw_m")
    wind_speed = slot.get("wind_speed_kmh")
    wind_dir = slot.get("wind_direction")
    swell_dir = slot.get("swell1_direction")
    wave_dir = slot.get("wave_direction")

    # 1) Periodo swell (energia a profundidad)
    if isinstance(swell_period, (int, float)):
        if swell_period >= 10:
            factor *= 0.35
            reasons.append(f"Mar de fondo fuerte (periodo {swell_period:.0f} s)")
            penalties_hit.append("period>=10")
        elif swell_period >= 8:
            factor *= 0.55
            reasons.append(f"Mar de fondo moderado (periodo {swell_period:.0f} s)")
            penalties_hit.append("period>=8")
        elif swell_period >= 6:
            factor *= 0.75
            reasons.append(f"Algo de mar de fondo (periodo {swell_period:.0f} s)")
            penalties_hit.append("period>=6")
        elif swell_period <= 5 and (swell_height or 0) < 0.25 and (wave_height or 0) < 0.25:
            factor *= 1.05
            reasons.append("Sin mar de fondo relevante")
            penalties_hit.append("period_bonus")

    # 2) Altura swell — umbrales orilla
    if isinstance(swell_height, (int, float)):
        if swell_height >= 1.5:
            factor *= 0.55
            reasons.append(f"Swell alto ({swell_height:.1f} m)")
            penalties_hit.append("swell>=1.5")
        elif swell_height >= 0.8:
            factor *= 0.7
            reasons.append(f"Swell moderado ({swell_height:.1f} m)")
            penalties_hit.append("swell>=0.8")
        elif swell_height >= 0.45:
            factor *= 0.82
            reasons.append(f"Swell remueve orilla ({swell_height:.1f} m)")
            penalties_hit.append("swell>=0.45")
        elif swell_height >= 0.3:
            factor *= 0.9
            reasons.append(f"Swell bajo pero activo ({swell_height:.1f} m)")
            penalties_hit.append("swell>=0.3")

    # 3) Oleaje total — critico en calas poco profundas
    if isinstance(wave_height, (int, float)):
        if wave_height >= 1.0:
            factor *= 0.7
            reasons.append(f"Oleaje fuerte remueve fondo ({wave_height:.1f} m)")
            penalties_hit.append("wave>=1.0")
        elif wave_height >= 0.55:
            factor *= 0.78
            reasons.append(f"Oleaje remueve orilla ({wave_height:.1f} m)")
            penalties_hit.append("wave>=0.55")
        elif wave_height >= 0.35:
            factor *= 0.85
            reasons.append(f"Oleaje remueve arena cerca ({wave_height:.1f} m)")
            penalties_hit.append("wave>=0.35")

    # 4) Potencia de ola (fuerza)
    if isinstance(wave_power, (int, float)):
        if wave_power >= 1.5:
            factor *= 0.7
            reasons.append(f"Potencia de ola alta ({wave_power:.1f} kW/m)")
            penalties_hit.append("power>=1.5")
        elif wave_power >= 0.5:
            factor *= 0.82
            reasons.append(f"Potencia de ola moderada ({wave_power:.1f} kW/m)")
            penalties_hit.append("power>=0.5")
        elif wave_power >= 0.2:
            factor *= 0.92
            reasons.append(f"Potencia de ola activa ({wave_power:.1f} kW/m)")
            penalties_hit.append("power>=0.2")

    # 5) Viento + componente de tierra (una sola penalizacion direccional)
    onshore_wind = _onshore_alignment(coast_facing, wind_dir)
    applied_onshore_dir = False
    if isinstance(wind_speed, (int, float)):
        if wind_speed >= 45:
            factor *= 0.7
            reasons.append(f"Viento fuerte ({wind_speed:.0f} km/h)")
            penalties_hit.append("wind>=45")
        elif wind_speed >= 30:
            factor *= 0.85
            reasons.append(f"Viento moderado ({wind_speed:.0f} km/h)")
            penalties_hit.append("wind>=30")
        elif (
            coast_facing
            and onshore_wind is not None
            and onshore_wind >= 0.5
            and wind_speed >= 14
        ):
            if onshore_wind >= 0.7 and wind_speed >= 16:
                factor *= 0.82
                reasons.append(
                    f"Viento de tierra ({wind_dir} sobre costa {compass_es(coast_facing)}, "
                    f"{wind_speed:.0f} km/h)"
                )
                penalties_hit.append("onshore_wind_strong")
            else:
                factor *= 0.9
                reasons.append(
                    f"Componente de tierra ({wind_dir}→{compass_es(coast_facing)})"
                )
                penalties_hit.append("onshore_wind")
            applied_onshore_dir = True
        elif wind_speed <= 10 and (wave_height or 0) < 0.25:
            factor *= 1.03
            penalties_hit.append("wind_bonus")

    if not applied_onshore_dir:
        for label, direction in (("swell", swell_dir), ("olas", wave_dir)):
            align = _onshore_alignment(coast_facing, direction)
            height = swell_height if label == "swell" else wave_height
            if (
                coast_facing
                and align is not None
                and align >= 0.65
                and isinstance(height, (int, float))
                and height >= 0.3
            ):
                factor *= 0.9
                reasons.append(
                    f"{label.capitalize()} de cara a la costa "
                    f"({direction}→{compass_es(coast_facing)})"
                )
                penalties_hit.append(f"onshore_{label}")
                break

    # 6) Marea
    hours_to_tide = tide.get("hours_to_next_tide_event")
    if isinstance(hours_to_tide, (int, float)) and hours_to_tide <= 2.0:
        factor *= 0.9
        event = tide.get("next_tide_event") or "marea"
        reasons.append(f"Cambio de {event} proximo ({hours_to_tide:.1f} h)")
        penalties_hit.append("tide_change")

    tide_dir = tide.get("tide_direction")
    if tide_dir in ("rising", "falling"):
        factor *= 0.95
        label = "subiendo" if tide_dir == "rising" else "bajando"
        if not any(
            "marea" in r.lower() or "pleamar" in r.lower() or "bajamar" in r.lower()
            for r in reasons
        ):
            reasons.append(f"Marea {label}")
        penalties_hit.append("tide_dir")

    factor_out = _clamp(factor, 0.15, 1.05)

    # #region agent log
    _agent_log(
        "A",
        "visibility.py:_nearshore_slot_factor",
        "nearshore slot factor",
        {
            "local_hour": slot.get("local_hour"),
            "swell1_period_s": swell_period,
            "swell1_height_m": swell_height,
            "wave_height_m": wave_height,
            "wave_power_kw_m": wave_power,
            "wind_speed_kmh": wind_speed,
            "wind_dir": wind_dir,
            "coast_facing": coast_facing,
            "onshore_wind": onshore_wind,
            "wind_dir": wind_dir,
            "wave_dir": wave_dir,
            "swell_dir": swell_dir,
            "onshore_wave": _onshore_alignment(coast_facing, wave_dir),
            "onshore_swell": _onshore_alignment(coast_facing, swell_dir),
            "penalties_hit": penalties_hit,
            "height_penalty_while_offshore": bool(
                any(p.startswith(("swell>=", "wave>=", "power>=")) for p in penalties_hit)
                and (_onshore_alignment(coast_facing, wave_dir) or 0) < 0.35
                and (_onshore_alignment(coast_facing, swell_dir) or 0) < 0.35
            ),
            "factor_out": round(factor_out, 3),
            "wave_power_used": isinstance(wave_power, (int, float)),
        },
    )
    # #endregion

    return factor_out, reasons


def _offshore_summary(optics: dict[str, Any]) -> dict[str, Any] | None:
    raw = optics.get("secchi_day_mean_m")
    if not isinstance(raw, (int, float)):
        return None
    meters = round(float(raw), 1)
    # Escala abierta: >20 m ya es excelente en Mediterraneo abierto
    if meters >= 30:
        score = 9.5
    elif meters >= 20:
        score = 8.5
    elif meters >= 12:
        score = 7.0
    elif meters >= 8:
        score = 5.5
    else:
        score = _clamp(meters / 2.0)
    return {
        "scope": "offshore",
        "secchi_m": meters,
        "meters_mid": meters,
        "meters_min": meters,
        "meters_max": meters,
        "score": round(score, 1),
        "label": _score_label(score),
        "note": OFFSHORE_NOTE,
        "optics_status": optics.get("status"),
    }


def forecast_visibility(
    features: dict[str, Any],
    *,
    now: datetime | None = None,
    ref_hour: int | None = None,
    recent_context: dict[str, Any] | None = None,
    bottom_type: str | None = None,
    coast_facing: str | None = None,
    runoff_risk: str | None = None,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    """
    Prevision dual:
    - offshore: Secchi Copernicus (~4 km), claridad de masa de agua
    - nearshore (summary): orilla / pesca, con swell, potencia, viento de tierra,
      memoria N dias y escorrentia por lluvia

    `ref_hour` elige la franja del summary (0–23). Si falta, usa `now.hour` o 12.
    `as_of_date` ancla la ventana de lluvia (vista historica / previsión).
    """
    windy = features.get("windy") or {}
    slots = windy.get("hourly") or []
    optics = features.get("water_optics") or {}
    optics_hourly = optics.get("hourly") or []
    optics_by_hour = {int(s["local_hour"]): s for s in optics_hourly}
    facing = compass_es(coast_facing) or coast_facing

    tide = {
        "tide_direction": features.get("tide_direction"),
        "next_tide_event": features.get("next_tide_event"),
        "hours_to_next_tide_event": features.get("hours_to_next_tide_event"),
    }

    if as_of_date is not None:
        as_of = as_of_date
    elif now is not None:
        as_of = now.date()
    else:
        as_of = now_local().date()

    secchi_base = _nearshore_secchi(optics.get("secchi_day_mean_m"))
    bottom_factor, bottom_reason = _bottom_type_factor(bottom_type)
    settling_factor = 1.0
    settling_reasons: list[str] = []
    if recent_context:
        settling_factor = float(recent_context.get("settling_factor") or 1.0)
        settling_reasons = list(recent_context.get("settling_reasons") or [])

    runoff_ctx = _runoff_from_features(
        features,
        as_of=as_of,
        runoff_risk=runoff_risk,
    )
    runoff_base = float(runoff_ctx.get("runoff_factor") or 1.0)
    runoff_reasons_base = list(runoff_ctx.get("runoff_reasons") or [])

    offshore = _offshore_summary(optics)
    hourly_out: list[dict[str, Any]] = []

    for slot in slots:
        local_hour = int(slot.get("local_hour", 0))
        factor, reasons = _nearshore_slot_factor(slot, tide, coast_facing=facing)
        factor *= settling_factor
        factor *= bottom_factor

        wave_h = slot.get("wave_height_m")
        runoff_factor, wave_reason = _runoff_wave_boost(runoff_base, wave_h)
        factor *= runoff_factor
        factor = _clamp(factor, 0.12, 1.0)

        combined_reasons = reasons + settling_reasons + runoff_reasons_base
        if wave_reason:
            combined_reasons.append(wave_reason)
        if bottom_reason:
            combined_reasons.append(bottom_reason)

        optics_slot = optics_by_hour.get(local_hour)
        raw_secchi = (optics_slot or {}).get("secchi_est_m") or optics.get("secchi_day_mean_m")
        secchi_slot = _nearshore_secchi(raw_secchi)

        if secchi_slot:
            meters_min, meters_mid, meters_max = _meters_from_secchi(secchi_slot, factor)
            score = _clamp(meters_mid / 1.2)
        else:
            score = _clamp(factor * 7.0)
            meters_min, meters_mid, meters_max = _meters_from_score(score)

        hourly_out.append(
            {
                "local_hour": local_hour,
                "time": slot.get("time"),
                "scope": "nearshore",
                "score": round(score, 1),
                "label": _score_label(score),
                "meters_min": meters_min,
                "meters_mid": meters_mid,
                "meters_max": meters_max,
                "factor": round(factor, 2),
                "runoff_factor": runoff_factor,
                "secchi_base_m": secchi_slot,
                "reasons": combined_reasons,
            }
        )

    if not hourly_out and not offshore:
        return {
            "status": "missing",
            "summary": None,
            "nearshore": None,
            "offshore": None,
            "hourly": [],
            "recent_context": recent_context,
            "runoff_context": runoff_ctx,
        }

    if ref_hour is not None:
        target_hour = int(ref_hour)
    elif now is not None:
        target_hour = now.hour
    else:
        target_hour = 12
    current = (
        min(hourly_out, key=lambda row: abs(int(row["local_hour"]) - target_hour))
        if hourly_out
        else None
    )

    nearshore = None
    if current:
        nearshore = {
            "scope": "nearshore",
            "score": current["score"],
            "label": current["label"],
            "meters_min": current["meters_min"],
            "meters_mid": current["meters_mid"],
            "meters_max": current["meters_max"],
            "secchi_base_m": secchi_base,
            "optics_status": optics.get("status"),
            "reasons": current["reasons"],
            "local_hour": current["local_hour"],
            "factor": current["factor"],
            "settling_factor": settling_factor,
            "runoff_factor": current.get("runoff_factor", runoff_base),
            "bottom_type": bottom_type or "mixto",
            "runoff_risk": _normalize_runoff_risk(runoff_risk),
            "coast_facing": facing,
        }

    # summary = nearshore (lo que importa para decidir ir a pescar)
    summary = nearshore

    # #region agent log
    _agent_log(
        "E",
        "visibility.py:forecast_visibility",
        "dual visibility summary",
        {
            "raw_secchi_open_ocean_m": optics.get("secchi_day_mean_m"),
            "nearshore_secchi_scale": NEARSHORE_SECCHI_SCALE,
            "secchi_base_nearshore_m": secchi_base,
            "offshore": offshore,
            "nearshore_label": (nearshore or {}).get("label"),
            "nearshore_meters_mid": (nearshore or {}).get("meters_mid"),
            "nearshore_score": (nearshore or {}).get("score"),
            "nearshore_factor": (nearshore or {}).get("factor"),
            "nearshore_reasons": (nearshore or {}).get("reasons"),
            "settling_factor": settling_factor,
            "runoff_factor": (nearshore or {}).get("runoff_factor", runoff_base),
            "runoff_context": {
                "weighted_precip_mm": runoff_ctx.get("weighted_precip_mm"),
                "peak_precip_mm": runoff_ctx.get("peak_precip_mm"),
                "runoff_risk": runoff_ctx.get("runoff_risk"),
                "threshold": runoff_ctx.get("threshold"),
            },
            "bottom_type": bottom_type or "mixto",
            "coast_facing": facing,
            "as_of_date": as_of.isoformat(),
            "windy_lat": windy.get("lat"),
            "windy_lon": windy.get("lon"),
            "atmos_lat": windy.get("atmos_lat"),
            "atmos_lon": windy.get("atmos_lon"),
            "atmos_elev_m": windy.get("atmos_elevation_m"),
            "current_slot": {
                "local_hour": (current or {}).get("local_hour"),
                "factor": (current or {}).get("factor"),
                "label": (current or {}).get("label"),
                "meters_mid": (current or {}).get("meters_mid"),
            } if current else None,
            "merged_hourly_brief": [
                {
                    "h": s.get("local_hour"),
                    "wind": s.get("wind_speed_kmh"),
                    "wdir": s.get("wind_direction"),
                    "wave": s.get("wave_height_m"),
                    "wavedir": s.get("wave_direction"),
                    "swell": s.get("swell1_height_m"),
                    "sdir": s.get("swell1_direction"),
                    "pwr": s.get("wave_power_kw_m"),
                    "snap": s.get("_from_snapshot_id"),
                }
                for s in slots
            ],
        },
    )
    # #endregion

    status = "ok" if (nearshore or offshore) else "missing"
    return {
        "status": status,
        "summary": summary,
        "nearshore": nearshore,
        "offshore": offshore,
        "hourly": hourly_out,
        "recent_context": recent_context,
        "runoff_context": runoff_ctx,
        "disclaimer": (
            "Orilla: swell/olas/potencia/viento de tierra + memoria N dias + "
            "escorrentia (lluvia). Mar abierto: Secchi Copernicus (~4 km), sin turbidez local."
        ),
    }
