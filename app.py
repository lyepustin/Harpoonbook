from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import streamlit as st

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from harpoonbook.bg_capture import (
    is_capture_all_running,
    is_capture_running,
    start_background_capture,
    start_background_capture_all,
    take_finished_capture,
    take_finished_capture_all,
)
from harpoonbook.config import (
    MAX_CAPTURES_PER_DAY,
    MIN_CAPTURE_INTERVAL_HOURS,
    make_db_paths,
    now_local,
)
from harpoonbook.conditions import (
    load_conditions_for_view,
    today_needs_refresh,
)
from harpoonbook.db import (
    count_capture_rounds_on_day,
    fishing_spot_label,
    init_db,
    list_fishing_spots,
    list_snapshot_dates,
)
from harpoonbook.scrapers.windy import WINDY_LOOKAHEAD_DAYS
from harpoonbook.ui.dashboard import available_view_hours, render_dashboard, snap_view_hour
from harpoonbook.ui.spots_admin import render_spots_admin
from harpoonbook.ui.species_admin import render_species_admin
from harpoonbook.ui.trip_log import render_trip_log

st.set_page_config(page_title="Harpoonbook", layout="wide")
st.title("Harpoonbook")

db_paths = make_db_paths(BASE_DIR)
init_db(db_paths.db_path)
now = now_local()
today = now.date()


def _format_view_date(d: date) -> str:
    if d == today:
        return f"Hoy ({d.isoformat()})"
    if d == today + timedelta(days=1):
        return f"Mañana ({d.isoformat()})"
    if d == today + timedelta(days=2):
        return f"Pasado mañana ({d.isoformat()})"
    if d == today - timedelta(days=1):
        return f"Ayer ({d.isoformat()})"
    if d == today - timedelta(days=2):
        return f"Antes de ayer ({d.isoformat()})"
    if d > today:
        return f"Previsión {d.strftime('%d/%m/%Y (%A)')}"
    return d.strftime("%d/%m/%Y (%A)")


def _available_view_dates(spot_id: str) -> list[date]:
    """
    Días navegables: previsión (hoy+N) → hoy → atrás hasta el snapshot más antiguo.
    Orden: más reciente primero (para ◀ atrás / ▶ adelante).
    """
    stored = list_snapshot_dates(db_paths.db_path, spot_id=spot_id)
    oldest = min(stored) if stored else today
    if oldest > today:
        oldest = today
    newest = today + timedelta(days=WINDY_LOOKAHEAD_DAYS)
    days: list[date] = []
    d = newest
    while d >= oldest:
        days.append(d)
        d -= timedelta(days=1)
    return days


def _shift_view_hour(delta: int, hours: list[int]) -> None:
    """Mueve view_hour a la franja anterior/siguiente y fuerza rerun."""
    current = st.session_state.get("view_hour")
    if current not in hours:
        return
    idx = hours.index(current) + delta
    if idx < 0 or idx >= len(hours):
        return
    st.session_state["view_hour"] = hours[idx]
    st.rerun()


def _shift_view_date(delta_days: int, available: list[date]) -> None:
    """Mueve view_date ±1 día dentro del rango disponible y fuerza rerun."""
    current = st.session_state.get("view_date", today)
    if not isinstance(current, date):
        current = today
    target = current + timedelta(days=delta_days)
    if target not in available:
        return
    st.session_state["view_date"] = target
    st.rerun()


def _active_spots() -> list[dict]:
    return list_fishing_spots(db_paths.db_path, active_only=True)


with st.sidebar:
    page = st.radio(
        "Vista",
        ["Dashboard", "Registro de salida", "Sitios de pesca", "Especies"],
        index=0,
    )

if page == "Sitios de pesca":
    render_spots_admin(db_paths.db_path)
    st.stop()

if page == "Especies":
    render_species_admin(db_paths.db_path)
    st.stop()

if page == "Registro de salida":
    render_trip_log(db_paths.db_path)
    st.stop()

active_spots = _active_spots()
if not active_spots:
    st.error("No hay sitios de pesca activos. Ve a **Sitios de pesca** para crear o reactivar uno.")
    st.stop()

spot_by_id = {s["id"]: s for s in active_spots}
spot_ids = [s["id"] for s in active_spots]

with st.sidebar:
    st.header("Condiciones")
    if st.session_state.get("last_spot_id") not in spot_ids:
        st.session_state["last_spot_id"] = spot_ids[0]

    spot_id = st.selectbox(
        "Sitio",
        options=spot_ids,
        index=spot_ids.index(st.session_state.get("last_spot_id", spot_ids[0])),
        format_func=lambda sid: spot_by_id[sid]["label"],
    )
    if st.session_state.get("last_spot_id") != spot_id:
        st.session_state["view_date"] = today
        st.session_state["last_spot_id"] = spot_id
        st.session_state.pop("view_hour", None)

    available_dates = _available_view_dates(spot_id)
    default_date = st.session_state.get("view_date", today)
    if not isinstance(default_date, date) or default_date not in available_dates:
        default_date = today
        st.session_state["view_date"] = default_date

    idx = available_dates.index(default_date)
    # available_dates: [hoy, ayer, anteayer, ...] — índice mayor = más antiguo
    can_older = idx < len(available_dates) - 1
    can_newer = idx > 0

    nav_prev, nav_mid, nav_next = st.columns([1, 5, 1])
    with nav_prev:
        if st.button(
            "◀",
            key="view_date_prev",
            disabled=not can_older,
            help="Día anterior",
            use_container_width=True,
        ):
            _shift_view_date(-1, available_dates)
    with nav_next:
        if st.button(
            "▶",
            key="view_date_next",
            disabled=not can_newer,
            help="Día siguiente (hasta +3 de previsión Windy)",
            use_container_width=True,
        ):
            _shift_view_date(+1, available_dates)
    with nav_mid:
        view_date = st.selectbox(
            "Día",
            options=available_dates,
            index=idx,
            format_func=_format_view_date,
            key="view_date",
        )
    viewing_today = view_date == today
    viewing_forecast = view_date > today

spot = spot_by_id[spot_id]
db_path = db_paths.db_path

was_refreshed = False
bg_error: str | None = None
fail_key = f"_bg_fail::{spot_id}"
capture_all_fail_key = "_bg_fail_all"

if viewing_today:
    finished_all = take_finished_capture_all(db_path)
    if finished_all:
        if finished_all["status"] == "done":
            st.session_state.pop(capture_all_fail_key, None)
            results = finished_all.get("results") or []
            if results:
                labeled = [
                    (r["spot_id"], r["snapshot_id"], fishing_spot_label(db_path, r["spot_id"]))
                    for r in results
                ]
                st.session_state["capture_all_done"] = labeled
            else:
                st.session_state["capture_all_empty"] = True
            # El spot actual también se actualizó: forzar toast de refresco.
            for r in results:
                if r["spot_id"] == spot_id:
                    was_refreshed = True
                    st.session_state.pop(fail_key, None)
                    st.session_state["_bg_refresh_toast_id"] = r.get("snapshot_id")
                    break
        elif finished_all["status"] == "error":
            bg_error = finished_all.get("error") or "Error desconocido al capturar todo"
            st.session_state[capture_all_fail_key] = True

    finished = take_finished_capture(db_path, spot_id)
    if finished:
        if finished["status"] == "done":
            was_refreshed = True
            st.session_state.pop(fail_key, None)
            st.session_state["_bg_refresh_toast_id"] = finished.get("snapshot_id")
        elif finished["status"] == "error":
            bg_error = finished.get("error") or "Error desconocido al capturar"
            st.session_state[fail_key] = True

# Lectura rápida de BD; la captura en vivo va en background.
features, snapshot_id, _, view_mode = load_conditions_for_view(
    db_path,
    spot_id=spot_id,
    view_date=view_date,
    max_age_hours=MIN_CAPTURE_INTERVAL_HOURS,
    force_refresh=False,
    capture_if_stale=False,
)
if was_refreshed and features:
    view_mode = "live"
    toast_id = st.session_state.pop("_bg_refresh_toast_id", snapshot_id)
    st.toast(f"{spot['label']}: snapshot #{toast_id or snapshot_id}")

hours = available_view_hours(features)
if hours:
    stored = st.session_state.get("view_hour")
    if not isinstance(stored, int) or stored not in hours:
        st.session_state["view_hour"] = snap_view_hour(
            hours,
            stored if isinstance(stored, int) else None,
            viewing_today=viewing_today,
            now_hour=now.hour,
        )

force_refresh_all = False
force_refresh_one = False
with st.sidebar:
    if hours:
        if len(hours) == 1:
            view_hour = hours[0]
            st.caption(f"Hora: {view_hour:02d}:00")
        else:
            hour_idx = hours.index(st.session_state["view_hour"]) if st.session_state.get("view_hour") in hours else 0
            nav_h_prev, nav_h_mid, nav_h_next = st.columns([1, 5, 1])
            with nav_h_prev:
                if st.button(
                    "◀",
                    key="view_hour_prev",
                    disabled=hour_idx <= 0,
                    help="Franja anterior",
                    use_container_width=True,
                ):
                    _shift_view_hour(-1, hours)
            with nav_h_next:
                if st.button(
                    "▶",
                    key="view_hour_next",
                    disabled=hour_idx >= len(hours) - 1,
                    help="Franja siguiente",
                    use_container_width=True,
                ):
                    _shift_view_hour(+1, hours)
            with nav_h_mid:
                view_hour = st.selectbox(
                    "Hora",
                    options=hours,
                    format_func=lambda h: f"{h:02d}:00",
                    key="view_hour",
                )
        st.caption("Franjas Windy cada 3 h (modelo ECMWF).")
    else:
        view_hour = now.hour if viewing_today else 12
        st.caption("Sin franjas horarias para este día.")

    if viewing_today:
        n_spots = len(active_spots)
        current_label = spot_by_id[spot_id]["label"]
        force_refresh_all = st.button(
            f"Actualizar ({n_spots} sitios)",
            disabled=is_capture_all_running(db_paths.db_path),
            help="Windy (hoy+3 días), marea, corrientes y óptica para todos los sitios activos.",
        )
        force_refresh_one = st.button(
            f"Solo {current_label}",
            help="Fuerza captura solo del sitio abierto (incluye previsión Windy +3 días).",
        )
        rounds_today = count_capture_rounds_on_day(db_paths.db_path, today)
        st.caption(
            f"Auto {rounds_today}/{MAX_CAPTURES_PER_DAY} hoy · "
            f"mín. {MIN_CAPTURE_INTERVAL_HOURS:g} h entre rondas. "
            "Los botones fuerzan."
        )
    elif viewing_forecast:
        st.caption(
            "Previsión (~80% fiabilidad): Windy, corrientes, óptica y solunar/marea. "
            "Se actualiza al capturar **hoy**."
        )
    else:
        st.caption("Vista histórica: datos del snapshot guardado ese día.")

if viewing_today:
    capture_all_running = is_capture_all_running(db_path)

    if force_refresh_all:
        st.session_state.pop(capture_all_fail_key, None)
        st.session_state.pop(fail_key, None)
        started = start_background_capture_all(db_path)
        if not started:
            st.session_state["capture_all_busy"] = True
        capture_all_running = is_capture_all_running(db_path)

    force_one = bool(force_refresh_one)
    stale = today_needs_refresh(db_path, spot_id)
    skip_auto = bool(st.session_state.get(fail_key)) or bool(
        st.session_state.get(capture_all_fail_key)
    )
    if not capture_all_running:
        if force_one:
            st.session_state.pop(fail_key, None)
            started = start_background_capture(db_path, spot_id)
            if not started:
                st.session_state["capture_all_busy"] = True
        elif stale and not skip_auto:
            # Por defecto: refrescar todos los sitios activos (no solo el abierto).
            started = start_background_capture_all(db_path)
            if not started:
                st.session_state["capture_all_busy"] = True
            capture_all_running = is_capture_all_running(db_path)

    refreshing = is_capture_running(db_path, spot_id) or capture_all_running
    if not refreshing:
        # Captura muy rápida: recoger resultado en este mismo run.
        finished_now = take_finished_capture(db_path, spot_id)
        if finished_now and finished_now["status"] == "done":
            st.session_state.pop(fail_key, None)
            st.session_state["_bg_refresh_toast_id"] = finished_now.get("snapshot_id")
            st.rerun()
        if finished_now and finished_now["status"] == "error":
            bg_error = finished_now.get("error") or "Error desconocido al capturar"
            st.session_state[fail_key] = True

        finished_all_now = take_finished_capture_all(db_path)
        if finished_all_now and finished_all_now["status"] == "done":
            st.session_state.pop(capture_all_fail_key, None)
            results = finished_all_now.get("results") or []
            if results:
                st.session_state["capture_all_done"] = [
                    (r["spot_id"], r["snapshot_id"], fishing_spot_label(db_path, r["spot_id"]))
                    for r in results
                ]
            else:
                st.session_state["capture_all_empty"] = True
            st.rerun()
        if finished_all_now and finished_all_now["status"] == "error":
            bg_error = finished_all_now.get("error") or "Error desconocido al capturar todo"
            st.session_state[capture_all_fail_key] = True

    if capture_all_running:
        st.info(
            f"Capturando **todas** las fuentes para **{len(active_spots)} sitios** "
            "en segundo plano (Windy, marea, corrientes, óptica)…"
        )

        @st.fragment(run_every=timedelta(seconds=2))
        def _poll_background_capture_all() -> None:
            if not is_capture_all_running(db_path):
                st.rerun(scope="app")
            st.caption("Sigue en otro proceso. Puedes cambiar de día y de hora.")

        _poll_background_capture_all()
    elif refreshing:
        st.info(f"Actualizando datos en vivo de **{spot['label']}** en segundo plano…")

        @st.fragment(run_every=timedelta(seconds=2))
        def _poll_background_capture() -> None:
            if not is_capture_running(db_path, spot_id):
                st.rerun(scope="app")
            st.caption("Sigue en otro proceso. Puedes cambiar de día y de hora.")

        _poll_background_capture()
    elif bg_error:
        st.warning(
            f"No se pudieron actualizar los datos en vivo: {bg_error}  \n"
            "Puedes reintentar con **Actualizar** o **Solo este sitio**."
        )

if st.session_state.pop("capture_all_busy", False):
    st.warning("Ya hay una captura en curso. Espera a que termine.")

if done := st.session_state.pop("capture_all_done", None):
    parts = [f"{label} #{snap_id}" for _, snap_id, label in done]
    st.toast(f"Captura completa ({len(done)} sitios): " + ", ".join(parts))
    st.success(
        f"**Captura completa ({len(done)} sitios):**  \n"
        + "  \n".join(f"- {label} → snapshot #{snap_id}" for _, snap_id, label in done)
    )
elif st.session_state.pop("capture_all_empty", False):
    st.warning("No hay sitios activos para capturar.")

render_dashboard(
    db_path,
    spot=spot,
    spot_id=spot_id,
    view_date=view_date,
    view_hour=view_hour,
    features=features,
    snapshot_id=snapshot_id,
    view_mode=view_mode,
    viewing_today=viewing_today,
    date_label=_format_view_date(view_date),
    base_dir=BASE_DIR,
)
