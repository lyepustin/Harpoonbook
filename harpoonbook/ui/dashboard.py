from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import pandas as pd
import streamlit as st

from harpoonbook.config import (
    BOTTOM_TYPES,
    COMPASS_DIRECTIONS,
    EXPOSURE_TYPES,
    LOCAL_TZ,
    RUNOFF_RISK_TYPES,
    SOLUNAR_LOCATION,
    compass_es,
    format_methods,
    now_local,
)
from harpoonbook.db import (
    export_trips_csv,
    fishing_spot_label,
    list_condition_snapshots,
    list_trips,
)
from harpoonbook.scrapers.solunar import (
    all_daylight_windows,
    refine_solunar_for_daylight,
    refine_solunar_for_now,
)
from harpoonbook.spot_profile import format_direction_label
from harpoonbook.ui.condition_arrows import overlay_arrows_from_slots
from harpoonbook.ui.spot_image import show_spot_map_image
from harpoonbook.ui.windy_colors import render_windy_legend_html, style_hour_table, style_windy_table
from harpoonbook.visibility import DEFAULT_LOOKBACK_DAYS, forecast_visibility, summarize_recent_days


def _round1(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 1)


def available_view_hours(features: dict[str, Any] | None) -> list[int]:
    """Horas navegables del día: Windy (3 h) o, si falta, corrientes Open-Meteo."""
    payload = features or {}
    windy = payload.get("windy") or {}
    hours = sorted(
        {
            int(slot["local_hour"])
            for slot in (windy.get("hourly") or [])
            if slot.get("local_hour") is not None
        }
    )
    if hours:
        return hours
    om = ((payload.get("currents") or {}).get("open_meteo") or {})
    return sorted(
        {
            int(slot["local_hour"])
            for slot in (om.get("hourly") or [])
            if slot.get("local_hour") is not None
        }
    )


def snap_view_hour(
    hours: list[int],
    stored: int | None,
    *,
    viewing_today: bool,
    now_hour: int,
) -> int:
    """Mantiene la hora entre días; si no existe, la más cercana. Default hoy=ahora, otro día=09:00."""
    if not hours:
        return now_hour if viewing_today else 9
    if stored is not None:
        if stored in hours:
            return stored
        return min(hours, key=lambda hour: (abs(hour - stored), hour))
    target = now_hour if viewing_today else 9
    return min(hours, key=lambda hour: (abs(hour - target), hour))


def _fmt_time(value: str | None) -> str:
    """ISO / datetime-ish → HH:MM."""
    if not value:
        return "—"
    text = str(value).strip()
    if "T" in text:
        try:
            # 2026-07-16T02:49:00+02:00
            part = text.split("T", 1)[1]
            return part[:5]
        except Exception:
            pass
    if len(text) >= 16 and text[10] == " ":
        return text[11:16]
    if len(text) >= 5 and text[2] == ":":
        return text[:5]
    return text


def _fmt_window(start: str | None, end: str | None) -> str:
    return f"{_fmt_time(start)}–{_fmt_time(end)}"


def _window_range(window: dict[str, Any]) -> str:
    """Rango solunar completo si existe; si no, el recorte diurno."""
    return _fmt_window(
        window.get("solunar_start") or window.get("start"),
        window.get("solunar_end") or window.get("end"),
    )


def _franja_metric(window: dict[str, Any] | None) -> tuple[str, str]:
    """Próxima franja útil como valor; detalle con entra/sale."""
    if not window:
        return "—", "sin franja útil"
    main = _window_range(window)
    if window.get("is_tomorrow"):
        main = f"mañana {main}"
    entrada = _fmt_time(window.get("entry_time") or window.get("start"))
    salida = _fmt_time(window.get("exit_time") or window.get("end"))
    return main, f"entra {entrada} · sale {salida}"


def _window_past(window: dict[str, Any], now: datetime) -> bool:
    exit_raw = window.get("exit_time") or window.get("end")
    if not exit_raw:
        return False
    try:
        dt = datetime.fromisoformat(str(exit_raw))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        return dt <= now
    except ValueError:
        return False


def _format_window_line(window: dict[str, Any], *, prefix: str = "") -> str:
    label = window.get("period_label", "Periodo")
    extra = ""
    if window.get("coincides_with") == "sunset":
        extra = " (coincide con puesta de sol)"
    elif window.get("coincides_with") == "sunrise":
        extra = " (coincide con salida de sol)"
    return (
        f"{prefix}rango {_fmt_window(window.get('start'), window.get('end'))} · "
        f"entrada {_fmt_time(window.get('entry_time', window.get('start')))} · "
        f"salida {_fmt_time(window.get('exit_time', window.get('end')))}{extra}"
        f" — {label}"
    )


def _metric_plain(label: str, value: str, detail: str | None = None) -> None:
    """Métrica en dos filas sin flecha ni color de delta de Streamlit."""
    detail_html = ""
    if detail:
        detail_html = (
            f'<div style="font-size:0.9rem;line-height:1.3;margin-top:0.15rem;">'
            f"{detail}</div>"
        )
    st.markdown(
        f'<div style="padding:0.25rem 0 0.5rem 0;">'
        f'<div style="font-size:0.875rem;opacity:0.7;">{label}</div>'
        f'<div style="font-size:1.6rem;font-weight:600;line-height:1.25;">{value}</div>'
        f"{detail_html}</div>",
        unsafe_allow_html=True,
    )


def _nearest_windy_slot(hourly: list[dict[str, Any]], hour: int) -> dict[str, Any] | None:
    if not hourly:
        return None
    return min(hourly, key=lambda slot: abs(int(slot.get("local_hour", 0)) - hour))


def _current_speed_kmh(slot: dict[str, Any] | None) -> float | None:
    if not slot:
        return None
    try:
        value = float(slot.get("current_speed_kmh"))
    except (TypeError, ValueError):
        return None
    return value


def _active_current_slots(hourly: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Solo franjas con corriente > 0 (0 = calma / sin dato útil del modelo)."""
    out: list[dict[str, Any]] = []
    for slot in hourly:
        speed = _current_speed_kmh(slot)
        if speed is not None and speed > 0:
            out.append(slot)
    return out


def _nearest_current_slot(hourly: list[dict[str, Any]], hour: int) -> dict[str, Any] | None:
    return _nearest_windy_slot(_active_current_slots(hourly), hour)


def _tide_metric(features: dict[str, Any]) -> tuple[str, str]:
    """Returns (main, delta): estado de marea + próximo evento."""
    state = (features.get("tide_state") or "").lower()
    direction = features.get("tide_direction")
    event = features.get("next_tide_event")
    hours = features.get("hours_to_next_tide_event")

    if state == "pleamar":
        main = "Pleamar"
    elif state == "bajamar":
        main = "Bajamar"
    elif state == "subiendo" or direction == "rising":
        main = "Subiendo"
    elif state == "bajando" or direction == "falling":
        main = "Bajando"
    else:
        main = "—"

    if event and hours is not None:
        try:
            h = float(hours)
            if h < 1:
                mins = max(1, int(round(h * 60)))
                delta = f"{event} en ~{mins} min"
            else:
                delta = f"{event} en {h:.1f} h"
        except (TypeError, ValueError):
            delta = str(event)
    elif event:
        delta = str(event)
    else:
        delta = ""
    return main, delta


def _agua_now_metric(features: dict[str, Any], windy: dict[str, Any]) -> tuple[str, str]:
    """Temp. agua (Windy/ECMWF) como valor principal; próximo evento de marea como delta."""
    temp = _round1(windy.get("water_temp_c"))
    main = f"{temp}°C" if temp is not None else "—"
    _, tide_delta = _tide_metric(features)
    return main, tide_delta


def _sea_now_line(slot: dict[str, Any] | None) -> tuple[str, str]:
    if not slot:
        return "—", ""
    wind = _round1(slot.get("wind_speed_kmh"))
    waves = _round1(slot.get("wave_height_m"))
    swell = _round1(slot.get("swell1_height_m"))
    period = _round1(slot.get("swell1_period_s"))
    wdir = compass_es(slot.get("wind_direction")) or "—"
    hour = int(slot.get("local_hour", 0))
    main = f"{wind if wind is not None else '—'} km/h {wdir}"
    parts = []
    if waves is not None:
        parts.append(f"olas {waves} m")
    if swell is not None:
        swell_txt = f"swell {swell} m"
        if period is not None:
            swell_txt += f" / {period} s"
        parts.append(swell_txt)
    delta = f"{hour:02d}:00 · " + " · ".join(parts) if parts else f"{hour:02d}:00"
    return main, delta


def _current_strength_label(speed_kmh: float | None) -> str:
    """Etiqueta cualitativa para corriente modelada (orientativa en costa)."""
    if speed_kmh is None:
        return "—"
    # Umbrales en km/h (~ nudos): floja <0.4 kn, moderada <1 kn, fuerte <1.6 kn
    if speed_kmh < 0.8:
        return "Floja"
    if speed_kmh < 1.9:
        return "Moderada"
    if speed_kmh < 3.0:
        return "Fuerte"
    return "Muy fuerte"


def _next_current_change(
    hourly: list[dict[str, Any]],
    current_slot: dict[str, Any],
) -> dict[str, Any] | None:
    """Primera franja posterior con dirección distinta a la actual."""
    current_hour = int(current_slot.get("local_hour", 0))
    current_dir = compass_es(current_slot.get("current_direction"))
    if not current_dir:
        return None
    later = sorted(
        (s for s in _active_current_slots(hourly) if int(s.get("local_hour", 0)) > current_hour),
        key=lambda s: int(s.get("local_hour", 0)),
    )
    for slot in later:
        nxt = compass_es(slot.get("current_direction"))
        if nxt and nxt != current_dir:
            return slot
    return None


def _current_now_line(
    slot: dict[str, Any] | None,
    hourly: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """Returns (main, delta): estado actual / próximo cambio de dirección."""
    if not slot:
        return "—", ""
    speed_f = _current_speed_kmh(slot)
    strength = _current_strength_label(speed_f)
    direction = compass_es(slot.get("current_direction")) or "—"
    speed_r = _round1(speed_f)
    main = f"{strength} {direction}"
    if speed_r is not None:
        main = f"{main} · {speed_r} km/h"

    change = _next_current_change(hourly or [], slot)
    if not change:
        return main, "estable"
    change_dir = compass_es(change.get("current_direction")) or "—"
    change_hour = int(change.get("local_hour", 0))
    return main, f"cambia a {change_dir} · {change_hour:02d}:00"


def _sources_status(features: dict[str, Any]) -> list[str]:
    status: list[str] = []
    if features.get("solunar"):
        status.append("solunar ✓")
    windy = features.get("windy") or {}
    if windy.get("hourly"):
        status.append("windy ✓")
    currents = features.get("currents") or {}
    if (currents.get("open_meteo") or {}).get("hourly"):
        status.append("open-meteo ✓")
    optics = features.get("water_optics") or {}
    if optics.get("hourly"):
        status.append("optics ✓")
    elif optics.get("status") == "no_credentials":
        status.append("optics (sin login)")
    precip = features.get("precipitation") or {}
    if precip.get("daily") or precip.get("status") == "ok":
        status.append("lluvia ✓")
    elif precip.get("status") == "error":
        status.append("lluvia ✗")
    if features.get("tide_direction") or features.get("next_tide_event") or features.get("tide_state"):
        status.append("marea ✓")
    return status


def render_dashboard(
    db_path: str,
    *,
    spot: dict[str, Any],
    spot_id: str,
    view_date: date,
    view_hour: int,
    features: dict[str, Any],
    snapshot_id: int | None,
    view_mode: str,
    viewing_today: bool,
    date_label: str,
    base_dir: str,
) -> None:
    """Vista de decisión: ahora arriba; detalle en expanders."""
    now = now_local()
    # Solunar: mejor entrada solo en horario legal pescasub (amanecer–ocaso).
    solunar_raw = dict(features.get("solunar") or {})
    if not solunar_raw.get("sunrise_time") and features.get("sunrise_time"):
        solunar_raw["sunrise_time"] = features.get("sunrise_time")
        solunar_raw["sunset_time"] = features.get("sunset_time")
    lookahead = features.get("solunar_lookahead") or []
    if viewing_today:
        solunar = refine_solunar_for_now(solunar_raw, now, lookahead=lookahead)
    else:
        solunar = refine_solunar_for_daylight(solunar_raw)
    windy = features.get("windy") or {}
    merge_meta = features.get("merge_meta") or {}
    bottom_type = spot.get("bottom_type") or "mixto"
    profile = spot.get("profile") or {}

    # --- 1. Cabecera ---
    header_cols = st.columns([3, 2])
    with header_cols[0]:
        st.markdown(f"### {date_label} — {spot['label']}")
    with header_cols[1]:
        if view_mode == "live":
            st.success("Datos en vivo")
        elif view_mode == "cached":
            st.info("Caché reciente")
        elif view_mode == "forecast":
            st.info("Previsión (+días)")
        elif view_mode == "historical":
            st.warning("Histórico (snapshot)")
        else:
            st.error("Sin datos para este día")

    ref_hour = int(view_hour)
    sea_slot = _nearest_windy_slot(windy.get("hourly") or [], ref_hour)
    om_hourly = ((features.get("currents") or {}).get("open_meteo") or {}).get("hourly") or []
    current_slot = _nearest_current_slot(om_hourly, ref_hour)
    arrows = overlay_arrows_from_slots(sea_slot, current_slot)
    sea_hour = int(sea_slot["local_hour"]) if sea_slot and sea_slot.get("local_hour") is not None else ref_hour
    current_hour = (
        int(current_slot["local_hour"])
        if current_slot and current_slot.get("local_hour") is not None
        else None
    )
    now_slot = _nearest_windy_slot(windy.get("hourly") or [], now.hour) if viewing_today else None
    is_now_slot = bool(
        viewing_today
        and now_slot
        and sea_slot
        and int(now_slot.get("local_hour", -1)) == int(sea_slot.get("local_hour", -2))
    )

    # --- Foto + ficha corta ---
    map_cols = st.columns([1, 2.4])
    with map_cols[0]:
        shown = show_spot_map_image(
            db_path,
            spot_id,
            max_width=300,
            caption=None,
            arrows=arrows or None,
        )
        if not shown:
            st.caption("Sin mapa. Sube una captura en **Sitios de pesca**.")
        elif arrows:
            st.caption("Flechas: viento · swell · corriente (centro del mapa).")
    with map_cols[1]:
        facing = compass_es(spot.get("coast_facing"))
        facing_txt = f"{facing} ({COMPASS_DIRECTIONS.get(facing, '')})" if facing else "—"
        exposure = profile.get("exposure")
        exposure_txt = EXPOSURE_TYPES.get(exposure, exposure) if exposure else "—"
        runoff = profile.get("runoff_risk") or "medio"
        runoff_txt = RUNOFF_RISK_TYPES.get(runoff, runoff).split("(")[0].strip()

        dmin, dmax = profile.get("depth_min_m"), profile.get("depth_max_m")
        depth_txt = ""
        if dmin is not None and dmax is not None:
            depth_txt = f" · **Prof.:** {dmin:g}–{dmax:g} m"
        elif dmax is not None:
            depth_txt = f" · **Prof.:** hasta ~{dmax:g} m"
        elif dmin is not None:
            depth_txt = f" · **Prof.:** desde ~{dmin:g} m"

        parts: list[str] = []
        parts.append(
            f"**Fondo:** {BOTTOM_TYPES.get(bottom_type, 'Mixto')} · "
            f"**Costa mira:** {facing_txt} · "
            f"**Exposición:** {exposure_txt} · "
            f"**Escorrentía:** {runoff_txt}{depth_txt}"
        )

        entry_notes = (profile.get("entry_notes") or "").strip()
        if entry_notes:
            entry_inline = " · ".join(
                line.strip().lstrip("-• ").strip()
                for line in entry_notes.splitlines()
                if line.strip()
            )
            parts.append(f"**Entradas:** {entry_inline}")

        dirs = spot.get("approach_directions") or []
        if dirs:
            dir_lines = "  \n".join(f"- {format_direction_label(d)}" for d in dirs)
            parts.append(f"**Direcciones de pesca:**  \n{dir_lines}")

        structure = (profile.get("structure_notes") or "").strip()
        if structure:
            structure_txt = " ".join(structure.split())
            parts.append(f"**Estructura:**  \n{structure_txt}")

        summary = (profile.get("summary") or "").strip()
        if summary:
            summary_txt = " ".join(summary.split())
            parts.append(f"**Resumen:**  \n{summary_txt}")

        st.markdown("\n\n".join(parts))

    # --- Contexto N días + visibilidad (cálculo) ---
    recent_context = summarize_recent_days(
        db_path,
        spot_id=spot_id,
        lookback_days=DEFAULT_LOOKBACK_DAYS,
        end_date=view_date,
    )
    visibility = forecast_visibility(
        features,
        now=now if viewing_today else None,
        ref_hour=ref_hour,
        recent_context=recent_context,
        bottom_type=bottom_type,
        coast_facing=spot.get("coast_facing"),
        runoff_risk=(profile.get("runoff_risk") if profile else None),
        as_of_date=view_date,
    )

    # --- 2. Bloque Ahora / franja ---
    st.markdown("#### Ahora" if is_now_slot else f"#### A las {ref_hour:02d}:00")

    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        if view_mode == "missing" or visibility.get("status") != "ok":
            _metric_plain("Visibilidad orilla", "—")
        else:
            summary_v = visibility.get("nearshore") or visibility.get("summary") or {}
            mid = summary_v.get("meters_mid")
            label = (summary_v.get("label") or "—").title()
            detail = f"~{mid} m" if mid is not None else None
            _metric_plain("Visibilidad orilla", label, detail)
            offshore = visibility.get("offshore") or {}
            off_m = offshore.get("secchi_m") or offshore.get("meters_mid")
            if off_m is not None:
                st.caption(f"Mar abierto (~4 km): ~{off_m:g} m")
    with m2:
        agua_main, agua_detail = _agua_now_metric(features, windy)
        _metric_plain("Agua", agua_main, agua_detail or None)
    with m3:
        cur_main, cur_detail = _current_now_line(current_slot, om_hourly)
        _metric_plain("Corriente", cur_main, cur_detail or None)
    with m4:
        if viewing_today:
            next_win = solunar.get("next_window")
        else:
            day_wins = solunar.get("best_windows") or []
            next_win = day_wins[0] if day_wins else None
        franja_main, franja_detail = _franja_metric(next_win)
        _metric_plain("Franja", franja_main, franja_detail or None)
    with m5:
        sea_main, sea_detail = _sea_now_line(sea_slot)
        _metric_plain("Mar cerca", sea_main, sea_detail or None)

    settling = recent_context.get("settling_factor")
    settling_reasons = recent_context.get("settling_reasons") or []
    if settling is not None and float(settling) < 1.0 and settling_reasons:
        st.caption(
            f"Agua aún asentándose (factor {float(settling):.2f}): "
            + " | ".join(settling_reasons)
        )

    # --- 3. Expand: Solunar y marea ---
    with st.expander("Solunar y marea", expanded=False):
        if solunar:
            sun_line = ""
            if solunar.get("sunrise_time") or solunar.get("sunset_time"):
                sun_line = (
                    f"Horario legal aproximado: "
                    f"{_fmt_time(solunar.get('sunrise_time'))}–{_fmt_time(solunar.get('sunset_time'))} "
                    f"(amanecer–ocaso).  \n"
                )
            if sun_line:
                st.caption(sun_line)

            next_win = solunar.get("next_window") if viewing_today else None
            if viewing_today and next_win:
                prefix = "**Próxima (mañana):** " if next_win.get("is_tomorrow") else "**Próxima:** "
                st.markdown(prefix + _format_window_line(next_win))
            elif not viewing_today:
                day_windows_best = solunar.get("best_windows") or []
                if day_windows_best:
                    w0 = day_windows_best[0]
                    st.markdown(
                        f"**Mejor franja:** {_format_window_line(w0)}"
                    )

            day_all = all_daylight_windows(solunar)
            if day_all:
                st.markdown("**Franjas diurnas del día**")
                for i, window in enumerate(day_all, start=1):
                    past = viewing_today and _window_past(window, now)
                    mark = " ~~(pasada)~~" if past else ""
                    st.markdown(
                        f"**#{i}:** {_format_window_line(window)}{mark}"
                    )
            else:
                st.caption("No hay franja solunar útil entre amanecer y ocaso.")

            if viewing_today and lookahead:
                for entry in lookahead:
                    day_label = entry.get("date") or "mañana"
                    day_sol = entry.get("solunar") or {}
                    tomorrow_wins = all_daylight_windows(day_sol)
                    if not tomorrow_wins:
                        continue
                    st.markdown(f"**Mañana ({day_label})**")
                    act = day_sol.get("activity_day")
                    if act:
                        st.caption(f"Actividad prevista: {act}")
                    for i, window in enumerate(tomorrow_wins, start=1):
                        st.markdown(f"**M{i}:** {_format_window_line(window)}")

            night_windows = solunar.get("night_windows") or []
            if night_windows:
                with st.expander("Franjas nocturnas (fuera de horario legal)", expanded=False):
                    for i, window in enumerate(night_windows, start=1):
                        st.markdown(
                            f"**Noche #{i}:** {_format_window_line(window)}"
                        )

            st.caption(
                f"Actividad: {solunar.get('activity_day') or '—'} | "
                f"Fase: {solunar.get('phase') or '—'} | "
                f"Iluminación: {solunar.get('illumination_pct')}%"
            )
        else:
            st.caption("Sin datos solunar para este día.")

        tide_main, tide_delta = _tide_metric(features)
        if tide_main != "—" or tide_delta:
            st.markdown(f"**Marea:** {tide_main}" + (f" — {tide_delta}" if tide_delta else ""))
        st.caption(f"Referencia: {SOLUNAR_LOCATION['label']}")

    # --- 4. Expand: Windy ---
    with st.expander("Viento y olas (Windy)", expanded=False):
        if windy.get("hourly"):
            merge_line = ""
            if merge_meta.get("snapshot_count", 0) > 1:
                capture_times = ", ".join(c[11:16] for c in merge_meta.get("captures", []))
                merge_line = (
                    f" | fusionado ({merge_meta['snapshot_count']} capturas: {capture_times})"
                )
            elev = windy.get("atmos_elevation_m")
            elev_txt = f" | celda atmos {elev:g} m" if elev is not None else ""
            st.caption(
                f"Modelo {windy.get('model')} | agua {windy.get('water_temp_c')}°C | "
                f"actualizado {windy.get('update', '?')}{elev_txt}{merge_line}"
            )
            rows = []
            for slot in windy["hourly"]:
                rows.append(
                    {
                        "Hora": f"{slot['local_hour']:02d}:00",
                        "Temp °C": _round1(slot.get("temp_c")),
                        "Viento km/h": _round1(slot.get("wind_speed_kmh")),
                        "Rachas": _round1(slot.get("wind_gusts_kmh")),
                        "Dir viento": compass_es(slot.get("wind_direction")),
                        "Olas m": _round1(slot.get("wave_height_m")),
                        "Dir olas": compass_es(slot.get("wave_direction")),
                        "Swell1 m": _round1(slot.get("swell1_height_m")),
                        "Periodo s": _round1(slot.get("swell1_period_s")),
                    }
                )
            st.dataframe(
                style_windy_table(pd.DataFrame(rows), highlight_hour=sea_hour),
                width="stretch",
            )
            st.markdown(render_windy_legend_html(), unsafe_allow_html=True)
        else:
            st.caption("Sin datos Windy para este día.")

    # --- 5. Expand: Visibilidad por horas ---
    with st.expander("Visibilidad por horas", expanded=False):
        if view_mode == "missing" or visibility.get("status") != "ok":
            st.caption("Sin datos suficientes para prever visibilidad este día.")
        else:
            summary_v = visibility.get("nearshore") or visibility.get("summary") or {}
            offshore = visibility.get("offshore") or {}
            meters_min = summary_v.get("meters_min")
            meters_max = summary_v.get("meters_max")
            if meters_min is not None and meters_max is not None:
                st.markdown(
                    f"**Orilla (~{summary_v.get('local_hour', '?')}:00):** "
                    f"**{meters_min}–{meters_max} m**"
                    + (
                        f" · **Mar abierto (~4 km):** ~{offshore.get('secchi_m'):g} m"
                        if offshore.get("secchi_m") is not None
                        else ""
                    )
                )
            reasons = summary_v.get("reasons") or []
            if reasons:
                st.caption("Factores orilla: " + " | ".join(reasons))
            runoff_ctx = visibility.get("runoff_context") or {}
            if runoff_ctx.get("weighted_precip_mm"):
                st.caption(
                    f"Lluvia ponderada (orilla): ~{runoff_ctx['weighted_precip_mm']:g} mm · "
                    f"pico {runoff_ctx.get('peak_precip_mm', '—')} mm · "
                    f"riesgo {runoff_ctx.get('runoff_risk', 'medio')} · "
                    f"factor ×{runoff_ctx.get('runoff_factor', 1)}"
                )
            vis_rows = []
            for row in visibility.get("hourly") or []:
                vis_rows.append(
                    {
                        "Hora": f"{row['local_hour']:02d}:00",
                        "Score": row["score"],
                        "Orilla": row["label"],
                        "Metros ~": f"{row['meters_min']}-{row['meters_max']}",
                        "Secchi orilla m": _round1(row.get("secchi_base_m")),
                        "Factor": row.get("factor"),
                    }
                )
            if vis_rows:
                st.dataframe(
                    style_hour_table(pd.DataFrame(vis_rows), sea_hour),
                    width="stretch",
                )
            st.caption(visibility.get("disclaimer", ""))
            if offshore.get("note"):
                st.caption(offshore["note"])
            optics = features.get("water_optics") or {}
            if optics.get("status") == "no_credentials":
                st.caption("Secchi base: ejecuta `copernicusmarine login` para activar claridad satelital.")
            elif optics.get("status") == "error":
                st.caption(f"Optics Copernicus: {optics.get('error')}")

    # --- 6. Expand: Contexto N días ---
    with st.expander(f"Contexto últimos {DEFAULT_LOOKBACK_DAYS} días", expanded=False):
        if recent_context.get("days_with_data", 0) == 0:
            st.caption(
                f"Sin snapshots en los {DEFAULT_LOOKBACK_DAYS} días previos. "
                "Programa la captura automática diaria para acumular historial."
            )
        else:
            ctx_cols = st.columns(4)
            with ctx_cols[0]:
                st.metric(
                    "Días con datos",
                    f"{recent_context['days_with_data']}/{DEFAULT_LOOKBACK_DAYS}",
                )
            with ctx_cols[1]:
                st.metric("Max periodo swell", f"{recent_context['max_swell_period_s']} s")
            with ctx_cols[2]:
                st.metric("Max swell", f"{recent_context['max_swell_height_m']} m")
            with ctx_cols[3]:
                st.metric("Factor asentamiento", f"{float(recent_context['settling_factor']):.2f}")
            if recent_context.get("settling_reasons"):
                st.caption("Memoria reciente: " + " | ".join(recent_context["settling_reasons"]))
            if recent_context.get("daily"):
                ctx_rows = [
                    {
                        "Día": row["date"],
                        "Periodo s": row["max_swell_period_s"],
                        "Swell m": row["max_swell_height_m"],
                        "Olas m": row["max_wave_height_m"],
                        "Viento km/h": row["max_wind_kmh"],
                    }
                    for row in recent_context["daily"]
                ]
                st.dataframe(pd.DataFrame(ctx_rows), width="stretch", hide_index=True)

    # --- 7. Expand: Corrientes ---
    with st.expander("Corrientes (Open-Meteo)", expanded=False):
        currents = features.get("currents") or {}
        open_meteo = currents.get("open_meteo") or {}
        om_hourly = open_meteo.get("hourly") or []
        if om_hourly:
            st.caption("Modelo ~8 km; en calas la corriente real puede ser mayor. Solo horas con corriente > 0.")
            current_rows = [
                {
                    "Hora": f"{int(slot['local_hour']):02d}:00",
                    "km/h": _round1(slot.get("current_speed_kmh")),
                    "kn": _round1(slot.get("current_speed_kn")),
                    "Dir": compass_es(slot.get("current_direction")),
                }
                for slot in _active_current_slots(om_hourly)
            ]
            if current_rows:
                st.dataframe(
                    style_hour_table(pd.DataFrame(current_rows), current_hour),
                    width="stretch",
                )
            else:
                st.caption("Todas las franjas en calma (0 km/h) según el modelo.")
        else:
            error = open_meteo.get("error")
            st.caption("Sin corrientes." + (f" Open-Meteo: {error}" if error else ""))

    # --- 8. Expand: Más / técnico + historial ---
    with st.expander("Más: historial y técnico", expanded=False):
        sources = _sources_status(features)
        merge_n = merge_meta.get("snapshot_count", 0)
        captured = (features.get("timestamp") or "")[:16].replace("T", " ")
        st.caption(
            f"Snapshot #{snapshot_id or '—'} | capturado {captured or '—'} | "
            f"fuentes: {' | '.join(sources) if sources else '—'} | "
            f"coords {spot['lat']:.5f}, {spot['lon']:.5f}"
            + (f" | fusionado ×{merge_n}" if merge_n > 1 else "")
        )

        tab_trips, tab_snapshots, tab_raw = st.tabs(
            ["Salidas", "Snapshots del día", "Raw JSON"]
        )

        with tab_trips:
            col1, col2 = st.columns([2, 1])
            with col1:
                rows = list_trips(db_path, limit=20)
                if rows:
                    st.write(f"Últimas {len(rows)} salidas")
                    for r in rows:
                        spot_label = fishing_spot_label(db_path, r["spot_id"])
                        method_label = format_methods(r.get("method_id"))
                        n_s = len(r.get("sightings") or [])
                        started = (r.get("started_at") or "")[:16].replace("T", " ")
                        vis = r.get("visibility_m")
                        vis_ref = f" | vis {vis} m" if vis is not None else ""
                        st.markdown(
                            f"- #{r['id']} {started} | {spot_label} | {method_label} | "
                            f"{n_s} avistamientos{vis_ref}"
                        )
                        if r.get("notas"):
                            st.caption(r["notas"])
                else:
                    st.caption("Aún no hay salidas. Regístralas en **Registro de salida**.")
            with col2:
                if st.button("Exportar CSV salidas (últimas 200)"):
                    export_path = os.path.join(
                        base_dir, "data", f"export_trips_{now.date().isoformat()}.csv"
                    )
                    export_trips_csv(db_path, export_path, limit=200)
                    st.success(f"CSV exportado: {export_path}")

        with tab_snapshots:
            day_snaps = list_condition_snapshots(
                db_path,
                snapshot_date=view_date,
                spot_id=spot_id,
                limit=50,
            )
            if day_snaps:
                st.caption(
                    f"{len(day_snaps)} capturas este día "
                    "(el dashboard fusiona Windy por franja)."
                )
                for s in day_snaps:
                    windy_slots = len((s.get("windy") or {}).get("hourly", []))
                    st.markdown(
                        f"**#{s['id']}** — {s['captured_at']} | "
                        f"solunar {s.get('source_status', {}).get('solunar', '?')} | "
                        f"windy {s.get('source_status', {}).get('windy', '?')} | "
                        f"{windy_slots} franjas"
                    )
            else:
                st.caption(f"No hay snapshots para {date_label}.")

        with tab_raw:
            st.json(features)
