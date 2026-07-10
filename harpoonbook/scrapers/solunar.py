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


def _compute_best_hour_and_windows(
    periods: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    highlighted = [p for p in periods if p.get("highlighted")]
    candidates = highlighted if highlighted else [p for p in periods if p.get("activity") == "alta"]
    if not candidates:
        candidates = periods[:]

    ranked = sorted(candidates, key=_rank_period)
    best_windows: list[dict[str, Any]] = []
    for period in ranked:
        entry = _entry_time_for_period(period)
        best_windows.append(
            {
                **period,
                "entry_time": entry.isoformat(),
            }
        )

    best_hour = best_windows[0]["entry_time"] if best_windows else None
    return best_hour, best_windows


def parse_solunar_from_soup(soup: BeautifulSoup, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(tz=LOCAL_TZ)
    base_date = now.date()

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
    best_hour, best_windows = _compute_best_hour_and_windows(periods)
    moon_times = _parse_moon_times(soup)
    moon_phase = _parse_moon_phase(soup)

    major_periods = [p for p in periods if p["period_group"] == "major" and p["activity"] == "alta"]
    minor_periods = [p for p in periods if p["period_group"] == "minor" and p["activity"] == "media"]

    return {
        "activity_day": activity_day,
        "periods": periods,
        "major_periods": major_periods,
        "minor_periods": minor_periods,
        "best_hour": best_hour,
        "best_windows": best_windows,
        "sunrise_time": sunrise_dt.isoformat() if sunrise_dt else None,
        "sunset_time": sunset_dt.isoformat() if sunset_dt else None,
        **moon_times,
        **moon_phase,
    }
