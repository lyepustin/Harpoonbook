from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup

from harpoonbook.config import CAPTURE_MAX_WORKERS, now_local
from harpoonbook.scrapers.solunar import parse_solunar_from_soup
from harpoonbook.scrapers.windy import WINDY_LOOKAHEAD_DAYS


TABLADEMAREAS_CABO_ROIG_URL = "https://tablademareas.com/es/alicante/cabo-roig"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; pesca-predictor/0.1)"}
# Mismo horizonte que Windy (+3 días de previsión).
SOLUNAR_LOOKAHEAD_DAYS = WINDY_LOOKAHEAD_DAYS


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_HEADERS)
    return s


def _fetch_soup_for_date(
    session: requests.Session,
    target: date,
    *,
    today: date,
    seeded: bool = False,
) -> BeautifulSoup | None:
    """
    Hoy: GET. Día futuro: POST con campo oculto `fecha=YYYY-MM-DD`
    (mismo formulario que usa el calendario de tablademareas).
    """
    try:
        if target == today:
            resp = session.get(TABLADEMAREAS_CABO_ROIG_URL, timeout=20)
        else:
            if not seeded and not session.cookies:
                session.get(TABLADEMAREAS_CABO_ROIG_URL, timeout=20)
            resp = session.post(
                TABLADEMAREAS_CABO_ROIG_URL,
                data={"fecha": target.isoformat()},
                headers={**_HEADERS, "Referer": TABLADEMAREAS_CABO_ROIG_URL},
                timeout=20,
            )
        resp.raise_for_status()
    except Exception:
        return None
    return BeautifulSoup(resp.text, "html.parser")


def _legacy_period_pairs(periods: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(p["start"], p["end"]) for p in periods]


def _parse_tide_events_for_day(soup: BeautifulSoup, day: date) -> list[dict[str, Any]]:
    """Lista de pleamar/bajamar del día (hora local del gráfico o tabla mensual)."""
    text = soup.get_text("\n", strip=True)
    events: list[dict[str, Any]] = []
    for m in re.finditer(
        r"(pleamar|bajamar)\s*\n\s*(\d{1,2}):(\d{2})\s*\n\s*h",
        text,
        flags=re.IGNORECASE,
    ):
        kind = m.group(1).lower()
        hh, mm = int(m.group(2)), int(m.group(3))
        evt = datetime(day.year, day.month, day.day, hh, mm)
        events.append(
            {
                "kind": kind,
                "time": evt.isoformat(),
                "local_hour": hh,
                "local_minute": mm,
            }
        )
    if not events:
        # Días futuros: el gráfico live no trae horas; la tabla mensual sí.
        by_day = _parse_monthly_tide_table(soup, year=day.year, month=day.month)
        events = list(by_day.get(day.day) or [])
    events.sort(key=lambda e: e["time"])
    return events


def _parse_monthly_tide_table(
    soup: BeautifulSoup,
    *,
    year: int,
    month: int,
) -> dict[int, list[dict[str, Any]]]:
    """
    Parsea `#tabla_mareas` (mes en curso) → {día: [eventos]}.
    Pleamar = `.tabla_mareas_marea_hora` sin clase bajamar; bajamar = `…_hora_bajamar`.
    """
    tabla = soup.select_one("#tabla_mareas")
    if not tabla:
        return {}

    by_day: dict[int, list[dict[str, Any]]] = {}
    for row in tabla.select("tr.tabla_mareas_fila"):
        num_el = row.select_one(".tabla_mareas_dia_numero")
        if not num_el:
            continue
        try:
            day_num = int(num_el.get_text(strip=True))
        except ValueError:
            continue
        day_events: list[dict[str, Any]] = []
        for cell in row.select("td.tabla_mareas_marea"):
            hora_el = cell.select_one(".tabla_mareas_marea_hora")
            if not hora_el:
                continue
            m = re.search(r"(\d{1,2}):(\d{2})", hora_el.get_text(" ", strip=True))
            if not m:
                continue
            hh, mm = int(m.group(1)), int(m.group(2))
            classes = " ".join(hora_el.get("class") or [])
            kind = "bajamar" if "bajamar" in classes else "pleamar"
            try:
                evt = datetime(year, month, day_num, hh, mm)
            except ValueError:
                continue
            day_events.append(
                {
                    "kind": kind,
                    "time": evt.isoformat(),
                    "local_hour": hh,
                    "local_minute": mm,
                }
            )
        if day_events:
            day_events.sort(key=lambda e: e["time"])
            by_day[day_num] = day_events
    return by_day


def _parse_tide_block(soup: BeautifulSoup, now: datetime, *, day: date | None = None) -> dict[str, Any]:
    """
    Marea del día.

    Para hoy: estado actual + próximo evento relativo a `now`.
    Para días futuros: calendario de pleamar/bajamar del día (sin estado live).
    """
    day = day or now.date()
    text = soup.get_text("\n", strip=True)
    is_today = day == now.date()

    tide_state: str | None = None
    tide_direction: str | None = None
    next_event: str | None = None
    hours_to_next: float | None = None
    events = _parse_tide_events_for_day(soup, day)

    if is_today:
        el_estado = soup.select_one(".grafico_estado_actual_texto2")
        if el_estado:
            m_el = re.search(
                r"Estado actual:\s*(pleamar|bajamar|subiendo|bajando)",
                el_estado.get_text(" ", strip=True),
                flags=re.IGNORECASE,
            )
            if m_el:
                tide_state = m_el.group(1).lower()
        if not tide_state:
            m_state = re.search(
                r"Estado actual:\s*(pleamar|bajamar|subiendo|bajando)",
                text,
                flags=re.IGNORECASE,
            )
            if m_state:
                tide_state = m_state.group(1).lower()

        if tide_state in ("subiendo",):
            tide_direction = "rising"
        elif tide_state in ("bajando",):
            tide_direction = "falling"
        elif re.search(r"está subiendo en estos momentos", text, flags=re.IGNORECASE):
            tide_direction = "rising"
            tide_state = tide_state or "subiendo"
        elif re.search(r"está bajando en estos momentos", text, flags=re.IGNORECASE):
            tide_direction = "falling"
            tide_state = tide_state or "bajando"

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
        else:
            parsed: list[tuple[datetime, str]] = []
            for ev in events:
                try:
                    evt = datetime.fromisoformat(ev["time"]).replace(tzinfo=now.tzinfo)
                except ValueError:
                    continue
                if evt < now - timedelta(minutes=5):
                    evt = evt + timedelta(days=1)
                parsed.append((evt, ev["kind"]))
            future = sorted((e for e in parsed if e[0] >= now), key=lambda x: x[0])
            if future:
                evt_dt, kind = future[0]
                next_event = kind
                hours_to_next = max(0.0, (evt_dt - now).total_seconds() / 3600.0)
    elif events:
        # Día futuro: primer evento del día como referencia.
        next_event = events[0]["kind"]
        try:
            first = datetime.fromisoformat(events[0]["time"]).replace(tzinfo=now.tzinfo)
            noon = datetime(day.year, day.month, day.day, 12, 0, tzinfo=now.tzinfo)
            hours_to_next = max(0.0, (first - noon).total_seconds() / 3600.0)
        except ValueError:
            hours_to_next = None

    return {
        "tide_state": tide_state,
        "tide_direction": tide_direction,
        "next_tide_event": next_event,
        "hours_to_next_tide_event": round(hours_to_next, 2) if hours_to_next is not None else None,
        "tide_events": events,
    }


def _lookahead_day_entry(
    day: date,
    *,
    today: date,
    now: datetime,
    monthly_tides: dict[int, list[dict[str, Any]]],
    cookie_dict: dict[str, str],
) -> dict[str, Any] | None:
    """POST de un día futuro en sesión propia (thread-safe)."""
    session = _session()
    for name, value in cookie_dict.items():
        session.cookies.set(name, value)
    soup_day = _fetch_soup_for_date(session, day, today=today, seeded=True)
    if soup_day is None:
        return None
    solunar_day = parse_solunar_from_soup(soup_day, now=now, base_date=day)
    tide_day = _parse_tide_block(soup_day, now, day=day)
    if not tide_day.get("tide_events") and day.month == today.month:
        events = monthly_tides.get(day.day) or []
        if events:
            tide_day = {
                "tide_state": None,
                "tide_direction": None,
                "next_tide_event": events[0]["kind"],
                "hours_to_next_tide_event": None,
                "tide_events": events,
            }
    if not (solunar_day.get("periods") or solunar_day.get("best_windows")):
        return None
    return {
        "date": day.isoformat(),
        "solunar": solunar_day,
        "tide": tide_day,
    }


def fetch_tablademeareas_cabo_roig_features(now: datetime | None = None) -> dict[str, Any]:
    """
    Features tablademareas Cabo Roig: hoy + solunar/marea de los N días siguientes.
    Los días futuros se piden en paralelo (sesiones independientes).
    """
    now = now or now_local()
    today = now.date()
    session = _session()

    soup = _fetch_soup_for_date(session, today, today=today)
    if soup is None:
        return {}

    solunar = parse_solunar_from_soup(soup, now=now, base_date=today)
    tide = _parse_tide_block(soup, now, day=today)
    monthly_tides = _parse_monthly_tide_table(soup, year=today.year, month=today.month)
    cookie_dict = {c.name: c.value for c in session.cookies}

    future_days = [today + timedelta(days=i) for i in range(1, SOLUNAR_LOOKAHEAD_DAYS + 1)]
    lookahead: list[dict[str, Any]] = []
    workers = min(CAPTURE_MAX_WORKERS, max(1, len(future_days)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _lookahead_day_entry,
                day,
                today=today,
                now=now,
                monthly_tides=monthly_tides,
                cookie_dict=cookie_dict,
            ): day
            for day in future_days
        }
        by_date: dict[str, dict[str, Any]] = {}
        for fut in as_completed(futures):
            entry = fut.result()
            if entry:
                by_date[entry["date"]] = entry
        lookahead = [by_date[d.isoformat()] for d in future_days if d.isoformat() in by_date]

    return {
        "solunar": solunar,
        "solunar_activity_day": solunar.get("activity_day"),
        "solunar_best_hour": solunar.get("best_hour"),
        "solunar_best_windows": solunar.get("best_windows"),
        "solunar_high_periods": _legacy_period_pairs(solunar.get("major_periods", [])),
        "solunar_medium_periods": _legacy_period_pairs(solunar.get("minor_periods", [])),
        "solunar_lookahead": lookahead,
        "sunrise_time": solunar.get("sunrise_time"),
        "sunset_time": solunar.get("sunset_time"),
        **{k: v for k, v in tide.items() if k != "tide_events"},
        "tide_events": tide.get("tide_events") or [],
    }
