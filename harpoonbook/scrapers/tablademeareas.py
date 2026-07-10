from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

from harpoonbook.config import now_local
from harpoonbook.scrapers.solunar import parse_solunar_from_soup


TABLADEMAREAS_CABO_ROIG_URL = "https://tablademareas.com/es/alicante/cabo-roig"


def _fetch_soup() -> BeautifulSoup | None:
    try:
        resp = requests.get(
            TABLADEMAREAS_CABO_ROIG_URL,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; pesca-predictor/0.1)"},
        )
        resp.raise_for_status()
    except Exception:
        return None
    return BeautifulSoup(resp.text, "html.parser")


def _legacy_period_pairs(periods: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(p["start"], p["end"]) for p in periods]


def fetch_tablademeareas_cabo_roig_features(now: datetime | None = None) -> dict[str, Any]:
    """
    Devuelve features desde tablademareas para Cabo Roig.
    Incluye bloque `solunar` completo y claves legacy para compatibilidad.
    """
    now = now or now_local()
    soup = _fetch_soup()
    if soup is None:
        return {}

    solunar = parse_solunar_from_soup(soup, now=now)
    text = soup.get_text("\n", strip=True)

    tide_direction: str | None = None
    if re.search(r"está subiendo en estos momentos", text, flags=re.IGNORECASE):
        tide_direction = "rising"
    elif re.search(r"está bajando en estos momentos", text, flags=re.IGNORECASE):
        tide_direction = "falling"

    next_event: str | None = None
    hours_to_next: float | None = None

    m_pleamar = re.search(
        r"La pleamar será dentro de\s+(\d+)\s+horas?\s+y\s+(\d+)\s+minutos",
        text,
        flags=re.IGNORECASE,
    )
    m_bajamar = re.search(
        r"La bajamar será dentro de\s+(\d+)\s+horas?\s+y\s+(\d+)\s+minutos",
        text,
        flags=re.IGNORECASE,
    )

    if m_pleamar:
        next_event = "pleamar"
        hours_to_next = float(m_pleamar.group(1)) + (float(m_pleamar.group(2)) / 60.0)
    elif m_bajamar:
        next_event = "bajamar"
        hours_to_next = float(m_bajamar.group(1)) + (float(m_bajamar.group(2)) / 60.0)

    return {
        "solunar": solunar,
        "solunar_activity_day": solunar.get("activity_day"),
        "solunar_best_hour": solunar.get("best_hour"),
        "solunar_best_windows": solunar.get("best_windows"),
        "solunar_high_periods": _legacy_period_pairs(solunar.get("major_periods", [])),
        "solunar_medium_periods": _legacy_period_pairs(solunar.get("minor_periods", [])),
        "sunrise_time": solunar.get("sunrise_time"),
        "sunset_time": solunar.get("sunset_time"),
        "tide_direction": tide_direction,
        "next_tide_event": next_event,
        "hours_to_next_tide_event": hours_to_next,
    }
