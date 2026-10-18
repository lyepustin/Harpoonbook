from __future__ import annotations

from datetime import date, datetime, time, timedelta

import streamlit as st

from harpoonbook.config import (
    APNEA_EQUALIZE,
    APNEA_FEEL,
    COMPASS_DIRECTIONS,
    DEFAULT_METHODS,
    LOCAL_TZ,
    MAX_SIGHTINGS_PER_TRIP,
    SHOT_QUALITY,
    SHOT_WITH_CATCH,
    TRIP_DURATION_MINUTES,
    TURBIDITY_LEVELS,
    compass_es,
    format_methods,
    join_method_ids,
    now_local,
    parse_method_ids,
    rewrite_compass_es,
)
from harpoonbook.db import (
    FishingTrip,
    TripSighting,
    add_trip,
    export_trips_csv,
    fishing_spot_label,
    get_trip,
    list_fishing_spots,
    list_species,
    list_trips,
    update_trip,
)
from harpoonbook.spot_profile import format_direction_label
from harpoonbook.trip_presets import suggest_water_for_window

_HOUR_OPTIONS = list(range(4, 23))
_MINUTE_OPTIONS = list(range(0, 60, 5))
_END_HOUR_OPTIONS = list(range(0, 24))
_DURATION_LABELS = [label for _, label in TRIP_DURATION_MINUTES]
_DURATION_BY_LABEL = {label: mins for mins, label in TRIP_DURATION_MINUTES}
_CUSTOM_DURATION = "Otra…"
_APNEA_MAX_SEC = 10 * 60


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=LOCAL_TZ)
    return dt


def _seconds_to_mmss(seconds: int | None) -> tuple[int, int]:
    if not seconds:
        return 0, 0
    return int(seconds) // 60, int(seconds) % 60


def _snap_minute(minute: int) -> int:
    return min(_MINUTE_OPTIONS, key=lambda m: abs(m - minute))


def _nearest_duration_label(total_minutes: int) -> str:
    if not TRIP_DURATION_MINUTES:
        return _CUSTOM_DURATION
    best = min(TRIP_DURATION_MINUTES, key=lambda item: abs(item[0] - total_minutes))
    if abs(best[0] - total_minutes) <= 10:
        return best[1]
    return _CUSTOM_DURATION


def _direction_options(spot: dict | None) -> list[tuple[str, str]]:
    """Returns list of (value, label). value is compass or compass|label."""
    if not spot:
        return []
    dirs = spot.get("approach_directions") or []
    opts: list[tuple[str, str]] = []
    for d in dirs:
        compass = d.get("compass") or ""
        value = compass
        if d.get("label"):
            value = f"{compass}|{d['label']}"
        opts.append((value, format_direction_label(d)))
    if not opts:
        for k, name in COMPASS_DIRECTIONS.items():
            opts.append((k, f"{k} ({name})"))
    return opts


def _parse_approach(value: str | None) -> str | None:
    if not value:
        return None
    return value.split("|", 1)[0]


def _nudge_apnea(delta: int) -> None:
    cur = int(st.session_state.get("apnea_total_sec", 0))
    st.session_state["apnea_total_sec"] = max(0, min(_APNEA_MAX_SEC, cur + delta))


def _apnea_duration_picker(*, state_key: str = "apnea_total_sec") -> int:
    """Selector tipo ruleta: botones ±s alrededor de un reloj mm:ss."""
    total = int(st.session_state.get(state_key, 0))
    mm, ss = total // 60, total % 60

    st.caption("Apnea media — toca ± para ajustar (sin teclear)")
    left, mid, right = st.columns([3, 2, 3])
    with left:
        b1, b2, b3 = st.columns(3)
        with b1:
            st.button("−30s", key="ap_m30", on_click=_nudge_apnea, args=(-30,), use_container_width=True)
        with b2:
            st.button("−10s", key="ap_m10", on_click=_nudge_apnea, args=(-10,), use_container_width=True)
        with b3:
            st.button("−5s", key="ap_m5", on_click=_nudge_apnea, args=(-5,), use_container_width=True)
    with mid:
        st.markdown(
            f"<div style='text-align:center;font-size:2rem;font-weight:700;"
            f"letter-spacing:0.04em;padding:0.15rem 0;font-variant-numeric:tabular-nums;'>"
            f"{mm}:{ss:02d}</div>",
            unsafe_allow_html=True,
        )
    with right:
        b4, b5, b6 = st.columns(3)
        with b4:
            st.button("+5s", key="ap_p5", on_click=_nudge_apnea, args=(5,), use_container_width=True)
        with b5:
            st.button("+10s", key="ap_p10", on_click=_nudge_apnea, args=(10,), use_container_width=True)
        with b6:
            st.button("+30s", key="ap_p30", on_click=_nudge_apnea, args=(30,), use_container_width=True)

    return int(st.session_state.get(state_key, 0))


def render_trip_log(db_path: str) -> None:
    st.subheader("Registro de salida al mar")
    st.caption(
        "Formulario post-pesca: dirección, agua (con preset), apneas y avistamientos. "
        "Puedes editar salidas anteriores."
    )

    spots = list_fishing_spots(db_path, active_only=True)
    species_list = list_species(db_path, active_only=True)
    if not spots:
        st.error("No hay sitios activos. Ve a **Sitios de pesca**.")
        return
    if not species_list:
        st.error("No hay especies. Ve a **Especies**.")
        return

    spot_by_id = {s["id"]: s for s in spots}
    species_by_id = {s["id"]: s for s in species_list}
    species_ids = [s["id"] for s in species_list]

    mode = st.radio("Modo", ["Nueva salida", "Editar salida"], horizontal=True, key="trip_mode")
    edit_trip_id: int | None = None
    existing: dict | None = None

    if mode == "Editar salida":
        trips = list_trips(db_path, limit=40)
        if not trips:
            st.info("Aún no hay salidas registradas.")
            return
        options = {t["id"]: t for t in trips}

        def _trip_label(tid: int) -> str:
            t = options[tid]
            label = fishing_spot_label(db_path, t["spot_id"])
            started = (t.get("started_at") or "")[:16].replace("T", " ")
            methods = format_methods(t.get("method_id"))
            return f"#{tid} — {started} — {label} ({methods})"

        edit_trip_id = st.selectbox(
            "Salida",
            options=list(options.keys()),
            format_func=_trip_label,
            key="edit_trip_select",
        )
        existing = get_trip(db_path, int(edit_trip_id)) if edit_trip_id else None
        if not existing:
            st.error("No se pudo cargar la salida.")
            return

    now = now_local()
    default_start = now
    default_end = now + timedelta(hours=3)

    if existing:
        try:
            default_start = datetime.fromisoformat(existing["started_at"])
            default_end = datetime.fromisoformat(existing["ended_at"])
        except (TypeError, ValueError):
            pass

    default_start = _ensure_aware(default_start)
    default_end = _ensure_aware(default_end)

    # --- Cabecera ---
    st.markdown("#### 1. Cabecera")
    spot_ids = [s["id"] for s in spots]
    default_spot = existing["spot_id"] if existing and existing["spot_id"] in spot_ids else spot_ids[0]
    spot_id = st.selectbox(
        "Sitio",
        options=spot_ids,
        index=spot_ids.index(default_spot),
        format_func=lambda sid: spot_by_id[sid]["label"],
        key="trip_spot",
    )
    spot = spot_by_id[spot_id]

    method_ids = list(DEFAULT_METHODS.keys())
    default_methods = parse_method_ids(existing.get("method_id") if existing else None)
    default_methods = [m for m in default_methods if m in method_ids]
    if not default_methods:
        default_methods = [m for m in ("espera", "acecho") if m in method_ids] or [method_ids[0]]

    times_sig = ("edit", edit_trip_id) if existing else ("new", None)
    if st.session_state.get("_trip_times_sig") != times_sig:
        st.session_state["trip_methods"] = default_methods
        st.session_state["trip_date"] = default_start.date()
        start_hour = min(_HOUR_OPTIONS, key=lambda h: abs(h - default_start.hour))
        st.session_state["trip_start_h"] = start_hour
        st.session_state["trip_start_m"] = _snap_minute(default_start.minute)
        span_min = max(1, int((default_end - default_start).total_seconds() // 60))
        dur_label = _nearest_duration_label(span_min)
        st.session_state["trip_duration"] = dur_label
        st.session_state["trip_end_h"] = default_end.hour
        st.session_state["trip_end_m"] = _snap_minute(default_end.minute)
        if existing and existing.get("apnea_avg_seconds"):
            st.session_state["apnea_total_sec"] = int(existing["apnea_avg_seconds"])
        else:
            st.session_state["apnea_total_sec"] = 90  # 1:30 por defecto
        st.session_state["trip_apnea_feel"] = (
            existing.get("apnea_feel") if existing and existing.get("apnea_feel") in APNEA_FEEL else "bien"
        )
        eq_default = "no"
        if existing:
            raw_eq = existing.get("apnea_equalize")
            if raw_eq in APNEA_EQUALIZE:
                eq_default = raw_eq
            elif existing.get("apnea_hard_to_equalize"):
                eq_default = "si_otro"
        st.session_state["ap_eq"] = eq_default
        st.session_state["_trip_times_sig"] = times_sig

    # Prof. media: por defecto la máxima del spot (ficha).
    spot_profile = spot.get("profile") or {}
    spot_max_depth = spot_profile.get("depth_max_m")
    depth_sig = (times_sig, spot_id)
    if st.session_state.get("_ap_depth_sig") != depth_sig:
        if (
            existing
            and existing.get("spot_id") == spot_id
            and existing.get("apnea_avg_depth_m") is not None
        ):
            st.session_state["ap_depth"] = float(existing["apnea_avg_depth_m"])
        elif spot_max_depth is not None:
            st.session_state["ap_depth"] = float(spot_max_depth)
        else:
            st.session_state["ap_depth"] = 0.0
        st.session_state["_ap_depth_sig"] = depth_sig

    selected_methods = st.multiselect(
        "Método",
        options=method_ids,
        format_func=lambda mid: DEFAULT_METHODS[mid],
        key="trip_methods",
        help="Puedes marcar varios (espera + acecho, etc.).",
    )

    d1, d2, d3 = st.columns([2, 1, 1])
    with d1:
        trip_day: date = st.date_input("Fecha", key="trip_date")
    with d2:
        start_h = st.selectbox("Hora inicio", options=_HOUR_OPTIONS, key="trip_start_h")
    with d3:
        start_m = st.selectbox(
            "Minutos",
            options=_MINUTE_OPTIONS,
            key="trip_start_m",
            format_func=lambda m: f"{m:02d}",
        )

    dur_options = _DURATION_LABELS + [_CUSTOM_DURATION]
    duration_label = st.radio(
        "Duración",
        options=dur_options,
        horizontal=True,
        key="trip_duration",
    )

    started_at = datetime.combine(trip_day, time(int(start_h), int(start_m)), tzinfo=LOCAL_TZ)

    if duration_label == _CUSTOM_DURATION:
        e1, e2 = st.columns(2)
        with e1:
            end_h = st.selectbox("Hora fin", options=_END_HOUR_OPTIONS, key="trip_end_h")
        with e2:
            end_m = st.selectbox(
                "Minutos fin",
                options=_MINUTE_OPTIONS,
                key="trip_end_m",
                format_func=lambda m: f"{m:02d}",
            )
        ended_at = datetime.combine(trip_day, time(int(end_h), int(end_m)), tzinfo=LOCAL_TZ)
        if ended_at <= started_at:
            ended_at = ended_at + timedelta(days=1)
    else:
        ended_at = started_at + timedelta(minutes=_DURATION_BY_LABEL[duration_label])

    st.caption(f"Ventana: **{started_at:%H:%M}** → **{ended_at:%H:%M}** ({trip_day:%d/%m/%Y})")

    dir_opts = _direction_options(spot)
    dir_values = [v for v, _ in dir_opts]
    default_dir = None
    if existing and existing.get("approach_direction"):
        for v in dir_values:
            if v == existing["approach_direction"] or v.startswith(existing["approach_direction"] + "|"):
                default_dir = v
                break
    approach_value = st.selectbox(
        "Dirección de pesca",
        options=dir_values,
        index=dir_values.index(default_dir) if default_dir in dir_values else 0,
        format_func=lambda v: next(lbl for val, lbl in dir_opts if val == v),
        key="trip_approach",
    )
    if spot.get("coast_facing"):
        facing = compass_es(spot["coast_facing"]) or spot["coast_facing"]
        st.caption(
            f"Costa mira hacia **{facing}** "
            f"({COMPASS_DIRECTIONS.get(facing, '')}). "
            "Compara con la dirección de la corriente al decidir si te dejas llevar."
        )

    # --- Agua ---
    st.markdown("#### 2. Agua")
    started_at = _ensure_aware(started_at)
    ended_at = _ensure_aware(ended_at)

    # Auto-preset al cambiar sitio o fecha/hora de inicio (sin botón).
    preset_sig = f"{spot_id}|{started_at.strftime('%Y-%m-%dT%H')}"
    preset_key = f"water_preset_{preset_sig}"

    def _apply_water_suggestion(suggestion: dict, *, overwrite_fields: bool) -> None:
        st.session_state[preset_key] = suggestion
        if not overwrite_fields or suggestion.get("status") != "ok":
            return
        if suggestion.get("water_temp_c") is not None:
            st.session_state["trip_temp"] = float(suggestion["water_temp_c"])
        if suggestion.get("visibility_m") is not None:
            st.session_state["trip_vis"] = float(suggestion["visibility_m"])
        if suggestion.get("current_speed_kmh") is not None:
            st.session_state["trip_cur_speed"] = float(suggestion["current_speed_kmh"])
        if suggestion.get("current_direction"):
            st.session_state["trip_cur_dir"] = (
                rewrite_compass_es(str(suggestion["current_direction"])) or ""
            )

    need_auto = st.session_state.get("_water_auto_sig") != preset_sig
    if need_auto:
        # En edición, no pisar valores guardados la primera vez que abres esa salida.
        overwrite = True
        if existing:
            try:
                orig_start = datetime.fromisoformat(existing["started_at"])
                if orig_start.tzinfo is None:
                    orig_start = orig_start.replace(tzinfo=LOCAL_TZ)
                same_window = (
                    orig_start.strftime("%Y-%m-%dT%H") == started_at.strftime("%Y-%m-%dT%H")
                    and existing.get("spot_id") == spot_id
                )
                overwrite = not same_window
            except (TypeError, ValueError):
                overwrite = False
            if not overwrite:
                # Restaura valores guardados en los widgets (keys de session_state).
                if existing.get("water_temp_c") is not None:
                    st.session_state["trip_temp"] = float(existing["water_temp_c"])
                if existing.get("visibility_m") is not None:
                    st.session_state["trip_vis"] = float(existing["visibility_m"])
                if existing.get("current_speed_kmh") is not None:
                    st.session_state["trip_cur_speed"] = float(existing["current_speed_kmh"])
                if existing.get("current_direction"):
                    st.session_state["trip_cur_dir"] = (
                        rewrite_compass_es(str(existing["current_direction"])) or ""
                    )
        suggestion = suggest_water_for_window(
            db_path,
            spot_id,
            started_at,
            ended_at,
            bottom_type=spot.get("bottom_type"),
            coast_facing=spot.get("coast_facing"),
            runoff_risk=(spot.get("profile") or {}).get("runoff_risk"),
        )
        _apply_water_suggestion(suggestion, overwrite_fields=overwrite)
        st.session_state["_water_auto_sig"] = preset_sig
        if suggestion.get("status") == "ok" and overwrite:
            st.session_state["_water_preset_flash"] = True
            st.rerun()

    if st.button("Recargar preset", key="load_water_preset", help="Vuelve a leer snapshots del día"):
        suggestion = suggest_water_for_window(
            db_path,
            spot_id,
            started_at,
            ended_at,
            bottom_type=spot.get("bottom_type"),
            coast_facing=spot.get("coast_facing"),
            runoff_risk=(spot.get("profile") or {}).get("runoff_risk"),
        )
        _apply_water_suggestion(suggestion, overwrite_fields=True)
        st.session_state["_water_auto_sig"] = preset_sig
        if suggestion.get("status") == "ok":
            st.session_state["_water_preset_flash"] = True
            st.rerun()
        else:
            st.warning("No hay snapshot ese día para este sitio. Rellena a mano.")

    if st.session_state.pop("_water_preset_flash", False):
        st.caption("Preset desde snapshots. Ajusta lo que no coincida con la realidad.")

    suggestion = st.session_state.get(preset_key) or {}
    if existing and not suggestion:
        suggestion = {
            "water_temp_c": existing.get("water_temp_c_suggested"),
            "visibility_m": existing.get("visibility_m_suggested"),
            "current_speed_kmh": existing.get("current_speed_kmh_suggested"),
            "current_direction": existing.get("current_direction_suggested"),
            "condition_snapshot_id": existing.get("condition_snapshot_id"),
            "features": existing.get("features_snapshot") or {},
            "status": "ok",
        }

    def _def_num(key: str, suggested_key: str, fallback: float | None = None) -> float:
        if existing and existing.get(key) is not None:
            return float(existing[key])
        if suggestion.get(suggested_key) is not None:
            return float(suggestion[suggested_key])
        return float(fallback or 0.0)

    default_cur_dir = (
        (existing.get("current_direction") if existing else None)
        or suggestion.get("current_direction")
        or ""
    )
    default_cur_dir = rewrite_compass_es(str(default_cur_dir)) or ""

    # Defaults solo si aún no hay valor (el preset escribe en session_state; sin value=).
    if "trip_temp" not in st.session_state:
        st.session_state["trip_temp"] = _def_num("water_temp_c", "water_temp_c", 22.0)
    if "trip_vis" not in st.session_state:
        st.session_state["trip_vis"] = _def_num("visibility_m", "visibility_m", 5.0)
    if "trip_cur_speed" not in st.session_state:
        st.session_state["trip_cur_speed"] = _def_num(
            "current_speed_kmh", "current_speed_kmh", 0.0
        )
    if "trip_cur_dir" not in st.session_state:
        st.session_state["trip_cur_dir"] = default_cur_dir

    w1, w2, w3, w4 = st.columns(4)
    with w1:
        water_temp = st.number_input(
            "Temp. agua °C",
            step=0.1,
            format="%.1f",
            key="trip_temp",
        )
    with w2:
        visibility_m = st.number_input(
            "Visibilidad (m)",
            min_value=0.0,
            step=0.5,
            format="%.1f",
            key="trip_vis",
        )
    with w3:
        current_speed = st.number_input(
            "Corriente km/h",
            min_value=0.0,
            step=0.1,
            format="%.1f",
            key="trip_cur_speed",
        )
    with w4:
        turb_keys = list(TURBIDITY_LEVELS.keys())
        cur_turb = (existing.get("turbidity") if existing else None) or "media"
        turbidity = st.selectbox(
            "Turbidez",
            options=turb_keys,
            index=turb_keys.index(cur_turb) if cur_turb in turb_keys else 1,
            format_func=lambda k: TURBIDITY_LEVELS[k],
            key="trip_turb",
        )

    current_direction = st.text_input(
        "Dirección corriente",
        key="trip_cur_dir",
        help="Ej. NE, hacia el espigón…",
    )

    # --- Apneas ---
    st.markdown("#### 3. Apneas")
    feel_keys = [""] + list(APNEA_FEEL.keys())
    if "trip_apnea_feel" not in st.session_state:
        st.session_state["trip_apnea_feel"] = "bien"
    apnea_feel = st.selectbox(
        "Cómo te has sentido",
        options=feel_keys,
        format_func=lambda k: "—" if not k else APNEA_FEEL[k],
        key="trip_apnea_feel",
    )

    apnea_total = _apnea_duration_picker()
    depth_help = (
        f"Máx. del spot: {float(spot_max_depth):g} m"
        if spot_max_depth is not None
        else "Sin profundidad máx. en la ficha del spot"
    )
    apnea_depth = st.number_input(
        "Prof. media (m)",
        min_value=0.0,
        step=0.5,
        key="ap_depth",
        help=depth_help,
    )
    eq_keys = list(APNEA_EQUALIZE.keys())
    if "ap_eq" not in st.session_state:
        st.session_state["ap_eq"] = "no"
    apnea_equalize = st.selectbox(
        "¿Te ha costado compensar?",
        options=eq_keys,
        format_func=lambda k: APNEA_EQUALIZE[k],
        key="ap_eq",
    )

    # --- Avistamientos ---
    st.markdown("#### 4. Avistamientos")
    existing_sightings = list(existing.get("sightings") or []) if existing else []
    n_sight = st.number_input(
        "Número de avistamientos",
        min_value=0,
        max_value=MAX_SIGHTINGS_PER_TRIP,
        value=min(max(len(existing_sightings), 0), MAX_SIGHTINGS_PER_TRIP),
        key="n_sightings",
    )

    # Si intentaste pescar, no tiene sentido "N/A (no disparé)".
    shot_when_tried = [k for k in SHOT_QUALITY if k != "n_a"]

    sightings: list[TripSighting] = []
    for i in range(int(n_sight)):
        base = existing_sightings[i] if i < len(existing_sightings) else {}
        with st.expander(f"Avistamiento #{i + 1}", expanded=i < 2):
            sid_default = base.get("species_id") if base.get("species_id") in species_ids else species_ids[0]
            sid = st.selectbox(
                "Especie",
                options=species_ids,
                index=species_ids.index(sid_default),
                format_func=lambda x: species_by_id[x]["label"],
                key=f"sight_sp_{i}",
            )
            if base.get("undersized"):
                action_default = "undersized"
            elif base.get("tried_to_fish"):
                action_default = "tried"
            else:
                action_default = "solo"
            action_keys = ["solo", "tried", "undersized"]
            action_labels = {
                "solo": "Solo visto",
                "tried": "Intenté pescar",
                "undersized": "No da la talla",
            }
            action = st.radio(
                "Acción",
                options=action_keys,
                index=action_keys.index(action_default),
                format_func=lambda k: action_labels[k],
                horizontal=True,
                key=f"sight_action_{i}",
                help="No da la talla e Intenté pescar son excluyentes.",
            )
            tried = action == "tried"
            undersized = action == "undersized"
            caught = 0
            weight = 0.0
            shot = "n_a"
            if undersized:
                st.caption("Avistado sin disparar (talla).")
            elif tried:
                cur_shot = base.get("shot_quality") or "fallido"
                if cur_shot not in shot_when_tried:
                    cur_shot = "fallido"
                shot = st.selectbox(
                    "Calidad del tiro",
                    options=shot_when_tried,
                    index=shot_when_tried.index(cur_shot),
                    format_func=lambda k: SHOT_QUALITY[k],
                    key=f"sight_shot_{i}",
                )
                if shot in SHOT_WITH_CATCH:
                    c1, c2 = st.columns(2)
                    with c1:
                        caught = st.number_input(
                            "Unidades pescadas",
                            min_value=0,
                            value=int(base.get("caught_count") or 0),
                            key=f"sight_c_{i}",
                        )
                    with c2:
                        weight = st.number_input(
                            "Peso medio (g)",
                            min_value=0.0,
                            value=float(base.get("avg_weight_g") or 0),
                            step=50.0,
                            key=f"sight_w_{i}",
                        )
                else:
                    st.caption("Tiro fallido: no hay unidades ni peso.")
            sightings.append(
                TripSighting(
                    species_id=sid,
                    count_seen=1,
                    times_seen=1,
                    tried_to_fish=bool(tried),
                    undersized=bool(undersized),
                    caught_count=int(caught),
                    avg_weight_g=weight if weight > 0 else None,
                    shot_quality=shot if tried else "n_a",
                )
            )

    notas = st.text_area(
        "Notas",
        value=(existing.get("notas") or "") if existing else "",
        height=80,
        key="trip_notas",
    )

    # --- Guardar ---
    save_label = "Actualizar salida" if existing else "Guardar salida"
    if st.button(save_label, type="primary", key="save_trip"):
        if not selected_methods:
            st.error("Elige al menos un método.")
            return
        if ended_at < started_at:
            st.error("La hora de fin no puede ser anterior al inicio.")
            return

        # Score legacy: se conserva al editar; en salidas nuevas queda en 0.
        score = int(existing["score"]) if existing and existing.get("score") is not None else 0

        trip = FishingTrip(
            spot_id=spot_id,
            method_id=join_method_ids(selected_methods),
            started_at=started_at,
            ended_at=ended_at,
            score=score,
            approach_direction=_parse_approach(approach_value),
            notas=notas,
            water_temp_c=water_temp if water_temp else None,
            visibility_m=visibility_m if visibility_m else None,
            current_speed_kmh=current_speed if current_speed else None,
            current_direction=(rewrite_compass_es(current_direction.strip()) or None),
            turbidity=turbidity,
            water_temp_c_suggested=suggestion.get("water_temp_c"),
            visibility_m_suggested=suggestion.get("visibility_m"),
            current_speed_kmh_suggested=suggestion.get("current_speed_kmh"),
            current_direction_suggested=compass_es(suggestion.get("current_direction")),
            apnea_feel=apnea_feel or None,
            apnea_avg_seconds=apnea_total if apnea_total > 0 else None,
            apnea_avg_depth_m=apnea_depth if apnea_depth > 0 else None,
            apnea_equalize=apnea_equalize,
            features_snapshot=suggestion.get("features") or (existing.get("features_snapshot") if existing else {}) or {},
            condition_snapshot_id=suggestion.get("condition_snapshot_id")
            or (existing.get("condition_snapshot_id") if existing else None),
            sightings=sightings,
        )

        if existing and edit_trip_id is not None:
            ok = update_trip(db_path, int(edit_trip_id), trip)
            if ok:
                st.success(f"Salida #{edit_trip_id} actualizada.")
                st.rerun()
            else:
                st.error("No se pudo actualizar.")
        else:
            trip_id = add_trip(db_path, trip)
            st.success(f"Salida #{trip_id} guardada.")
            st.rerun()

    st.divider()
    st.markdown("#### Últimas salidas")
    recent = list_trips(db_path, limit=15)
    if not recent:
        st.caption("Sin salidas todavía.")
    else:
        for t in recent:
            label = fishing_spot_label(db_path, t["spot_id"])
            method = format_methods(t.get("method_id"))
            started = (t.get("started_at") or "")[:16].replace("T", " ")
            n_s = len(t.get("sightings") or [])
            vis = t.get("visibility_m")
            vis_txt = f" | vis {vis} m" if vis is not None else ""
            st.markdown(
                f"- **#{t['id']}** {started} | {label} | {method} | "
                f"{n_s} avistamientos{vis_txt}"
            )
            if t.get("notas"):
                st.caption(t["notas"][:120])

    if st.button("Exportar CSV (últimas 200)"):
        import os

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(root, "data", f"export_trips_{now_local().date().isoformat()}.csv")
        export_trips_csv(db_path, path, limit=200)
        st.success(f"Exportado: {path}")
