from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any

from bs4 import BeautifulSoup, Tag

from harpoonbook.config import LOCAL_TZ


_PERIOD_TYPE_MAP = (
    (re.compile(r"tránsito lunar opuesto", re.I), "lunar_transit_opposite"),
    (re.compile(r"tránsito lunar", re.I), "lunar_transit"),
    (re.compile(r"salida de la luna", re.I), "moonrise"),
    (re.compile(r"puesta de la luna", re.I), "moonset"),
)


def _to_local_dt(d: date, t: time) -> datetime:
    return datetime.combine(d, t).replace(tzinfo=LOCAL_TZ)


def _parse_hhmm(text: str) -> time | None:
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if not m:
        return None
    return time(int(m.group(1)), int(m.group(2)))


def _parse_period_type(label: str) -> str:
    for pattern, period_type in _PERIOD_TYPE_MAP:
        if pattern.search(label):
            return period_type
    return "unknown"


def _parse_sun_times(soup: BeautifulSoup, base_date: date) -> tuple[datetime | None, datetime | None]:
    sunrise_dt: datetime | None = None
    sunset_dt: datetime | None = None

    illumination = soup.select_one(".fondo_iluminacion_grafico")
    if illumination:
        text = illumination.get_text(" ", strip=True)
        m_sunrise = re.search(r"SALIDA DE SOL\s+(\d{1,2}:\d{2})\s*h", text, flags=re.I)
        m_sunset = re.search(r"PUESTA DE SOL\s+(\d{1,2}:\d{2})\s*h", text, flags=re.I)
        if m_sunrise:
            sunrise_dt = _to_local_dt(base_date, _parse_hhmm(m_sunrise.group(1)) or time(0, 0))
        if m_sunset:
            sunset_dt = _to_local_dt(base_date, _parse_hhmm(m_sunset.group(1)) or time(0, 0))

    if sunrise_dt and sunset_dt:
        return sunrise_dt, sunset_dt

    page_text = soup.get_text("\n", strip=True)
    m_sunrise = re.search(r"amaneció.*?a las\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*h", page_text, flags=re.I | re.S)
    m_sunset = re.search(r"puesta de sol será a las\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*h", page_text, flags=re.I | re.S)
    if m_sunrise and not sunrise_dt:
        t = _parse_hhmm(m_sunrise.group(1))
        if t:
            sunrise_dt = _to_local_dt(base_date, t)
    if m_sunset and not sunset_dt:
        t = _parse_hhmm(m_sunset.group(1))
        if t:
            sunset_dt = _to_local_dt(base_date, t)
    return sunrise_dt, sunset_dt


def _parse_moon_times(soup: BeautifulSoup) -> dict[str, Any]:
    page_text = soup.get_text("\n", strip=True)
    moonrise: dict[str, Any] | None = None
    moonset: dict[str, Any] | None = None
    lunar_transit: str | None = None

    m_rise = re.search(
        r"La luna salió por el\s+(\w+)\s*\((\d+)°\)\s+a la\s+(\d{1,2}:\d{2})\s*h",
        page_text,
        flags=re.I,
    )
    if m_rise:
        moonrise = {
            "time": m_rise.group(3),
            "direction": m_rise.group(1),
            "bearing_deg": int(m_rise.group(2)),
        }

    m_set = re.search(
        r"se puso por el\s+(\w+)\s*\((\d+)°\)\s+a las\s+(\d{1,2}:\d{2})\s*h",
        page_text,
        flags=re.I,
    )
    if m_set:
        moonset = {
            "time": m_set.group(3),
            "direction": m_set.group(1),
            "bearing_deg": int(m_set.group(2)),
        }

    m_transit = re.search(
        r"tránsito lunar.*?a las\s+(\d{1,2}:\d{2})\s*h",
        page_text,
        flags=re.I | re.S,
    )
    if m_transit:
        lunar_transit = m_transit.group(1)

    return {
        "moonrise": moonrise,
        "moonset": moonset,
        "lunar_transit": lunar_transit,
    }


def _parse_moon_phase(soup: BeautifulSoup) -> dict[str, Any]:
    phase_el = soup.select_one("#fase_lunar_titulo")
    age_el = soup.select_one("#grafico_edad_lunar_texto1")
    illumination_el = soup.select_one("#grafico_iluminacion_texto1")

    phase = phase_el.get_text(" ", strip=True) if phase_el else None
    age_days = None
    illumination_pct = None

    if age_el:
        try:
            age_days = float(age_el.get_text(strip=True).replace(",", "."))
        except ValueError:
            age_days = None

    if illumination_el:
        try:
            illumination_pct = int(illumination_el.get_text(strip=True))
        except ValueError:
            illumination_pct = None

    return {
        "phase": phase,
        "age_days": age_days,
        "illumination_pct": illumination_pct,
    }


def _period_group(block: Tag) -> str:
    parent = block.find_parent("div", class_=re.compile("salida_puesta_luna_periodo"))
    if not parent:
        return "unknown"
    classes = " ".join(parent.get("class", []))
    if "mayor" in classes:
        return "major"
    if "menor" in classes:
        return "minor"
    return "unknown"


def _block_is_highlighted(block: Tag) -> bool:
    for el in block.find_all(True):
        if any("verde" in cls for cls in el.get("class", [])):
            return True
    return False


def _parse_coincidence(texto2: str | None) -> tuple[str | None, str | None]:
    if not texto2:
        return None, None
    normalized = re.sub(r"\s+", " ", texto2).strip()
    if re.search(r"coincide con la\s+puesta de sol", normalized, flags=re.I):
        return "sunset", normalized
    if re.search(r"coincide con la\s+salida de sol", normalized, flags=re.I):
        return "sunrise", normalized
    return None, normalized or None


def _parse_period_blocks(soup: BeautifulSoup, base_date: date) -> list[dict[str, Any]]:
    blocks = soup.select("div.salida_puesta_luna_periodo_datos")
    blocks = [b for b in blocks if b.find(class_=re.compile(r"periodo_datos_actividad"))]

    periods: list[dict[str, Any]] = []
    for block in blocks:
        activity_el = block.find(class_=re.compile(r"periodo_datos_actividad"))
        hora_el = block.find(class_=re.compile(r"periodo_datos_hora"))
        label_el = block.find(class_=re.compile(r"periodo_datos_texto1"))
        note_el = block.find(class_=re.compile(r"periodo_datos_texto2"))

        activity_raw = activity_el.get_text(" ", strip=True) if activity_el else ""
        activity = "alta" if "ALTA" in activity_raw.upper() else "media" if "MEDIA" in activity_raw.upper() else "unknown"

        hora_text = hora_el.get_text(" ", strip=True) if hora_el else block.get_text(" ", strip=True)
        m_times = re.search(r"de\s+(\d{1,2}:\d{2})\s*h\s+a\s+(\d{1,2}:\d{2})\s*h", hora_text, flags=re.I)
        if not m_times:
            continue

        start_t = _parse_hhmm(m_times.group(1))
        end_t = _parse_hhmm(m_times.group(2))
        if not start_t or not end_t:
            continue

        start_dt = _to_local_dt(base_date, start_t)
        end_dt = _to_local_dt(base_date, end_t)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)

        label = label_el.get_text(" ", strip=True) if label_el else ""
        coincides_with, coincidence_note = _parse_coincidence(note_el.get_text(" ", strip=True) if note_el else None)
        highlighted = _block_is_highlighted(block) or coincides_with is not None

        periods.append(
            {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "activity": activity,
                "period_group": _period_group(block),
                "period_type": _parse_period_type(label),
                "period_label": label,
                "highlighted": highlighted,
                "coincides_with": coincides_with,
                "coincidence_note": coincidence_note,
            }
        )

    return periods


def _entry_time_for_period(period: dict[str, Any]) -> datetime:
    """Hora recomendada para entrar al agua: inicio del periodo solunar."""
    return datetime.fromisoformat(period["start"])


def _rank_period(period: dict[str, Any]) -> tuple[int, int, int]:
    highlighted = 0 if period.get("highlighted") else 1
    activity = 0 if period.get("activity") == "alta" else 1
    group = 0 if period.get("period_group") == "major" else 1
    return (highlighted, activity, group)


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt


def _daylight_overlap(
    start: datetime,
    end: datetime,
    sunrise: datetime,
    sunset: datetime,
) -> tuple[datetime, datetime] | None:
    """Intersección del periodo solunar con [amanecer, ocaso]. None si no hay solape."""
    lo = max(start, sunrise)
    hi = min(end, sunset)
    if lo >= hi:
        return None
    return lo, hi


def _compute_best_hour_and_windows(
    periods: list[dict[str, Any]],
    sunrise_dt: datetime | None = None,
    sunset_dt: datetime | None = None,
) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Elige mejores franjas priorizando horario legal de pescasub (amanecer–ocaso).

    Returns:
        best_hour, best_exit, daylight_windows (ordenadas), night_windows (resto)
    """
    highlighted = [p for p in periods if p.get("highlighted")]
    majors = [
        p
        for p in periods
        if p.get("period_group") == "major" and p.get("activity") == "alta"
    ]
    altas = [p for p in periods if p.get("activity") == "alta"]
    # Preferir los dos periodos mayores; highlighted solo prioriza el orden.
    candidates = majors or highlighted or altas or periods[:]

    use_daylight = sunrise_dt is not None and sunset_dt is not None
    daylight_windows: list[dict[str, Any]] = []
    night_windows: list[dict[str, Any]] = []

    for period in sorted(candidates, key=_rank_period):
        start = _parse_iso_dt(period.get("start"))
        end = _parse_iso_dt(period.get("end"))
        if not start or not end:
            continue

        if use_daylight:
            overlap = _daylight_overlap(start, end, sunrise_dt, sunset_dt)
            if overlap:
                day_start, day_end = overlap
                entry = max(_entry_time_for_period(period), day_start)
                if entry >= day_end:
                    entry = day_start
                daylight_windows.append(
                    {
                        **period,
                        "start": day_start.isoformat(),
                        "end": day_end.isoformat(),
                        "entry_time": entry.isoformat(),
                        "exit_time": day_end.isoformat(),
                        "daylight": True,
                        "solunar_start": period.get("start"),
                        "solunar_end": period.get("end"),
                    }
                )
            else:
                night_windows.append(
                    {
                        **period,
                        "entry_time": _entry_time_for_period(period).isoformat(),
                        "exit_time": end.isoformat(),
                        "daylight": False,
                    }
                )
        else:
            entry = _entry_time_for_period(period)
            daylight_windows.append(
                {
                    **period,
                    "entry_time": entry.isoformat(),
                    "exit_time": end.isoformat(),
                    "daylight": True,
                }
            )

    # Si no hay solape diurno entre candidatos, probar todos los periodos del día.
    if use_daylight and not daylight_windows:
        for period in sorted(periods, key=_rank_period):
            start = _parse_iso_dt(period.get("start"))
            end = _parse_iso_dt(period.get("end"))
            if not start or not end:
                continue
            overlap = _daylight_overlap(start, end, sunrise_dt, sunset_dt)
            if not overlap:
                continue
            day_start, day_end = overlap
            entry = max(_entry_time_for_period(period), day_start)
            if entry >= day_end:
                entry = day_start
            daylight_windows.append(
                {
                    **period,
                    "start": day_start.isoformat(),
                    "end": day_end.isoformat(),
                    "entry_time": entry.isoformat(),
                    "exit_time": day_end.isoformat(),
                    "daylight": True,
                    "solunar_start": period.get("start"),
                    "solunar_end": period.get("end"),
                }
            )

    # Con amanecer/ocaso conocidos: no proponer entrada nocturna (pescasub de día).
    if use_daylight:
        best_hour = daylight_windows[0]["entry_time"] if daylight_windows else None
        best_exit = daylight_windows[0]["exit_time"] if daylight_windows else None
    else:
        fallback = daylight_windows or night_windows
        best_hour = fallback[0]["entry_time"] if fallback else None
        best_exit = fallback[0]["exit_time"] if fallback else None
    return best_hour, best_exit, daylight_windows, night_windows


def refine_solunar_for_daylight(solunar: dict[str, Any] | None) -> dict[str, Any]:
    """
    Reaplica filtro amanecer–ocaso sobre un solunar ya guardado (snapshots viejos).

    Actualiza best_hour / best_exit / best_windows a franjas diurnas; deja night_windows aparte.
    """
    if not solunar:
        return {}
    out = dict(solunar)
    sunrise_dt = _parse_iso_dt(out.get("sunrise_time"))
    sunset_dt = _parse_iso_dt(out.get("sunset_time"))
    periods = list(out.get("periods") or out.get("best_windows") or [])
    if not periods:
        return out

    best_hour, best_exit, day_windows, night_windows = _compute_best_hour_and_windows(
        periods, sunrise_dt, sunset_dt
    )
    out["best_hour"] = best_hour
    out["best_exit"] = best_exit
    out["best_windows"] = day_windows
    out["night_windows"] = night_windows
    if sunrise_dt:
        out["sunrise_time"] = sunrise_dt.isoformat()
    if sunset_dt:
        out["sunset_time"] = sunset_dt.isoformat()
    return out


# Margen mínimo para considerar una franja aún útil (entrada futura o resto de ventana).
_MIN_REMAINING = timedelta(minutes=20)


def _window_from_period(
    period: dict[str, Any],
    sunrise_dt: datetime | None,
    sunset_dt: datetime | None,
) -> dict[str, Any] | None:
    """Recorte diurno de un periodo; None si no hay solape con amanecer–ocaso."""
    start = _parse_iso_dt(period.get("start"))
    end = _parse_iso_dt(period.get("end"))
    if not start or not end:
        return None

    if sunrise_dt is not None and sunset_dt is not None:
        overlap = _daylight_overlap(start, end, sunrise_dt, sunset_dt)
        if not overlap:
            return None
        day_start, day_end = overlap
        entry = max(_entry_time_for_period(period), day_start)
        if entry >= day_end:
            entry = day_start
        return {
            **period,
            "start": day_start.isoformat(),
            "end": day_end.isoformat(),
            "entry_time": entry.isoformat(),
            "exit_time": day_end.isoformat(),
            "daylight": True,
            "solunar_start": period.get("start"),
            "solunar_end": period.get("end"),
        }

    entry = _entry_time_for_period(period)
    return {
        **period,
        "entry_time": entry.isoformat(),
        "exit_time": end.isoformat(),
        "daylight": True,
    }


def all_daylight_windows(solunar: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Todas las franjas diurnas del día (majors + minors), ordenadas por calidad."""
    return _all_daylight_windows(solunar or {})


def _all_daylight_windows(solunar: dict[str, Any]) -> list[dict[str, Any]]:
    """Todas las franjas diurnas del día (majors + minors), ordenadas por calidad."""
    sunrise_dt = _parse_iso_dt(solunar.get("sunrise_time"))
    sunset_dt = _parse_iso_dt(solunar.get("sunset_time"))
    periods = list(solunar.get("periods") or solunar.get("best_windows") or [])
    windows: list[dict[str, Any]] = []
    for period in periods:
        win = _window_from_period(period, sunrise_dt, sunset_dt)
        if win:
            windows.append(win)
    return sorted(windows, key=_rank_period)


def _is_window_still_useful(window: dict[str, Any], now: datetime) -> bool:
    """True si queda al menos ~20 min de franja o la entrada aún no ha llegado."""
    exit_dt = _parse_iso_dt(window.get("exit_time") or window.get("end"))
    entry_dt = _parse_iso_dt(window.get("entry_time") or window.get("start"))
    if not exit_dt:
        return False
    if exit_dt <= now:
        return False
    if entry_dt and entry_dt > now:
        return True
    return (exit_dt - now) >= _MIN_REMAINING


def _rank_upcoming(window: dict[str, Any], now: datetime) -> tuple:
    """Calidad primero; a igualdad, la más cercana en el tiempo."""
    entry_dt = _parse_iso_dt(window.get("entry_time") or window.get("start"))
    # Segundos hasta la entrada (0 si ya estamos dentro).
    if entry_dt is None:
        proximity = 0
    elif entry_dt <= now:
        proximity = 0
    else:
        proximity = int((entry_dt - now).total_seconds())
    return (*_rank_period(window), proximity)


def select_next_daylight_window(
    solunar: dict[str, Any] | None,
    now: datetime | None = None,
    *,
    lookahead: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """
    Próxima franja diurna útil respecto a `now`.

    1) Periodos diurnos de hoy (incl. media/menores) aún útiles.
    2) Si no hay, mejor franja diurna de mañana (`lookahead`).
    """
    now = now or datetime.now(tz=LOCAL_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=LOCAL_TZ)

    today_windows = _all_daylight_windows(solunar or {})
    upcoming_today = [w for w in today_windows if _is_window_still_useful(w, now)]
    if upcoming_today:
        best = min(upcoming_today, key=lambda w: _rank_upcoming(w, now))
        out = dict(best)
        out["for_date"] = (solunar or {}).get("for_date") or now.date().isoformat()
        out["is_tomorrow"] = False
        return out

    for entry in lookahead or []:
        day = entry.get("date")
        day_solunar = entry.get("solunar") or {}
        if not day_solunar:
            continue
        day_windows = _all_daylight_windows(day_solunar)
        if not day_windows:
            # Sin filtro por now: es un día futuro; coger la mejor del día.
            continue
        # Día futuro: todas las franjas son "próximas"; elegir por calidad.
        best = min(day_windows, key=_rank_period)
        out = dict(best)
        out["for_date"] = day or day_solunar.get("for_date")
        out["is_tomorrow"] = True
        return out

    return None


def list_upcoming_daylight_windows(
    solunar: dict[str, Any] | None,
    now: datetime | None = None,
    *,
    lookahead: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Franjas diurnas aún útiles hoy, más las de mañana (lookahead)."""
    now = now or datetime.now(tz=LOCAL_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=LOCAL_TZ)

    result: list[dict[str, Any]] = []
    today_iso = (solunar or {}).get("for_date") or now.date().isoformat()
    for w in _all_daylight_windows(solunar or {}):
        if not _is_window_still_useful(w, now):
            continue
        item = dict(w)
        item["for_date"] = today_iso
        item["is_tomorrow"] = False
        result.append(item)
    result.sort(key=lambda w: _rank_upcoming(w, now))

    for entry in lookahead or []:
        day = entry.get("date")
        day_solunar = entry.get("solunar") or {}
        day_windows = sorted(_all_daylight_windows(day_solunar), key=_rank_period)
        for w in day_windows:
            item = dict(w)
            item["for_date"] = day or day_solunar.get("for_date")
            item["is_tomorrow"] = True
            result.append(item)

    return result


def refine_solunar_for_now(
    solunar: dict[str, Any] | None,
    now: datetime | None = None,
    *,
    lookahead: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Como refine_solunar_for_daylight, más next_window / upcoming_windows
    filtrados por la hora actual (y opcionalmente mañana).
    """
    out = refine_solunar_for_daylight(solunar)
    if not out and not lookahead:
        return {}
    now = now or datetime.now(tz=LOCAL_TZ)
    next_win = select_next_daylight_window(out, now, lookahead=lookahead)
    upcoming = list_upcoming_daylight_windows(out, now, lookahead=lookahead)
    out["next_window"] = next_win
    out["upcoming_windows"] = upcoming
    if next_win:
        out["best_hour"] = next_win.get("entry_time")
        out["best_exit"] = next_win.get("exit_time")
    return out


def parse_solunar_from_soup(
    soup: BeautifulSoup,
    now: datetime | None = None,
    *,
    base_date: date | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(tz=LOCAL_TZ)
    base_date = base_date or now.date()

    activity_day: str | None = None
    activity_el = soup.select_one(".salida_puesta_luna_actividad_dia_titulo1")
    if activity_el:
        m_day = re.search(r"ACTIVIDAD DEL DÍA:\s*([A-ZÁÉÍÓÚÑ]+)", activity_el.get_text(" ", strip=True), flags=re.I)
        if m_day:
            activity_day = m_day.group(1).strip().upper()
    if not activity_day:
        m_day = re.search(r"ACTIVIDAD DEL DÍA:\s*([A-ZÁÉÍÓÚÑ]+)", soup.get_text("\n", strip=True), flags=re.I)
        if m_day:
            activity_day = m_day.group(1).strip().upper()

    sunrise_dt, sunset_dt = _parse_sun_times(soup, base_date)
    periods = _parse_period_blocks(soup, base_date)
    best_hour, best_exit, best_windows, night_windows = _compute_best_hour_and_windows(
        periods, sunrise_dt, sunset_dt
    )
    moon_times = _parse_moon_times(soup)
    moon_phase = _parse_moon_phase(soup)

    major_periods = [p for p in periods if p["period_group"] == "major" and p["activity"] == "alta"]
    minor_periods = [p for p in periods if p["period_group"] == "minor" and p["activity"] == "media"]

    return {
        "for_date": base_date.isoformat(),
        "activity_day": activity_day,
        "periods": periods,
        "major_periods": major_periods,
        "minor_periods": minor_periods,
        "best_hour": best_hour,
        "best_exit": best_exit,
        "best_windows": best_windows,
        "night_windows": night_windows,
        "sunrise_time": sunrise_dt.isoformat() if sunrise_dt else None,
        "sunset_time": sunset_dt.isoformat() if sunset_dt else None,
        **moon_times,
        **moon_phase,
    }
