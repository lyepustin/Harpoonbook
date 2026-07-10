from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from harpoonbook.config import DEFAULT_METHODS, SOLUNAR_LOCATION, make_db_paths, now_local
from harpoonbook.conditions import capture_all_spots, load_conditions_for_view, load_today_conditions_for_log
from harpoonbook.db import (
    FishingLog,
    add_log,
    export_logs_csv,
    fishing_spot_label,
    init_db,
    list_condition_snapshots,
    list_fishing_spots,
    list_logs,
    list_snapshot_dates,
)
from harpoonbook.recommender import recommend
from harpoonbook.ui.spots_admin import render_spots_admin
from harpoonbook.ui.windy_colors import render_windy_legend_html, style_windy_table


st.set_page_config(page_title="Harpoonbook", layout="wide")
st.title("Harpoonbook")

db_paths = make_db_paths(BASE_DIR)
init_db(db_paths.db_path)
now = now_local()
today = now.date()


def _format_view_date(d: date) -> str:
    if d == today:
        return f"Hoy ({d.isoformat()})"
    if d == today - timedelta(days=1):
        return f"Ayer ({d.isoformat()})"
    return d.strftime("%d/%m/%Y (%A)")


def _round1(value: float | int | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 1)


def _available_view_dates(spot_id: str) -> list[date]:
    stored = list_snapshot_dates(db_paths.db_path, spot_id=spot_id)
    return sorted(set(stored) | {today}, reverse=True)


def _active_spots() -> list[dict]:
    return list_fishing_spots(db_paths.db_path, active_only=True)


with st.sidebar:
    page = st.radio("Vista", ["Dashboard", "Sitios de pesca"], index=0)

if page == "Sitios de pesca":
    render_spots_admin(db_paths.db_path)
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

    available_dates = _available_view_dates(spot_id)
    default_date = st.session_state.get("view_date", today)
    if default_date not in available_dates:
        default_date = today
    view_date = st.selectbox(
        "Día",
        options=available_dates,
        index=available_dates.index(default_date),
        format_func=_format_view_date,
        key="view_date",
    )
    viewing_today = view_date == today
    force_refresh = False
    if viewing_today:
        current_label = spot_by_id[spot_id]["label"]
        force_refresh = st.button(f"Actualizar: {current_label}")
        if st.button("Capturar Windy (todos los sitios)"):
            st.session_state["capture_all_spots"] = True
    else:
        st.caption("Vista histórica: datos del snapshot guardado ese día.")

spot = spot_by_id[spot_id]

if viewing_today and st.session_state.pop("capture_all_spots", False):
    with st.spinner(f"Capturando Windy para {len(active_spots)} sitios…"):
        capture_all_results = capture_all_spots(db_paths.db_path)
    if capture_all_results:
        st.session_state["capture_all_done"] = [
            (sid, snap_id, fishing_spot_label(db_paths.db_path, sid))
            for sid, snap_id, _ in capture_all_results
        ]
    else:
        st.session_state["capture_all_empty"] = True
    st.rerun()

force = viewing_today and force_refresh
features, snapshot_id, was_refreshed, view_mode = load_conditions_for_view(
    db_paths.db_path,
    spot_id=spot_id,
    view_date=view_date,
    max_age_hours=0.25,
    force_refresh=force,
)

header_cols = st.columns([3, 2])
with header_cols[0]:
    st.markdown(f"### {_format_view_date(view_date)} — {spot['label']}")
with header_cols[1]:
    if view_mode == "live":
        st.success("Datos en vivo")
    elif view_mode == "cached":
        st.info("Caché reciente")
    elif view_mode == "historical":
        st.warning("Histórico (snapshot)")
    else:
        st.error("Sin datos para este día")

if was_refreshed:
    st.toast(f"{spot['label']}: snapshot #{snapshot_id}")
elif done := st.session_state.pop("capture_all_done", None):
    parts = [f"{label} #{snap_id}" for _, snap_id, label in done]
    st.toast(f"Windy capturado ({len(done)} sitios): " + ", ".join(parts))
    st.success(
        "**Windy capturado para todos los sitios:**  \n"
        + "  \n".join(f"- {label} → snapshot #{snap_id}" for _, snap_id, label in done)
    )
elif st.session_state.pop("capture_all_empty", False):
    st.warning("No hay sitios activos para capturar.")
elif snapshot_id and view_mode == "cached":
    merge_n = (features.get("merge_meta") or {}).get("snapshot_count", 1)
    if merge_n > 1:
        st.caption(f"Vista fusionada de {merge_n} capturas del día (última ref. #{snapshot_id})")
    else:
        st.caption(f"Usando snapshot #{snapshot_id} en caché (< 15 min)")
elif snapshot_id and view_mode == "historical":
    merge_n = (features.get("merge_meta") or {}).get("snapshot_count", 1)
    if merge_n > 1:
        st.caption(f"Vista fusionada de {merge_n} capturas del día (ref. #{snapshot_id})")
    else:
        captured = (features.get("timestamp") or "")[:16].replace("T", " ")
        st.caption(f"Snapshot #{snapshot_id} capturado {captured or '—'}")

solunar = features.get("solunar") or {}
windy = features.get("windy") or {}

meta_cols = st.columns(3)
merge_meta = features.get("merge_meta") or {}
merge_n = merge_meta.get("snapshot_count", 0)
with meta_cols[0]:
    if merge_n > 1:
        st.metric("Capturas del día", f"{merge_n} → fusionadas")
    else:
        st.metric("Snapshot", f"#{snapshot_id}" if snapshot_id else "—")
with meta_cols[1]:
    st.metric("Capturado", features.get("timestamp", "—")[:16].replace("T", " "))
with meta_cols[2]:
    status = []
    if solunar:
        status.append("solunar ✓")
    if windy.get("hourly"):
        status.append("windy ✓")
    if features.get("tide_direction"):
        status.append("marea ✓")
    st.metric("Fuentes", " | ".join(status) if status else "—")

st.caption(
    f"Solunar/marea: {SOLUNAR_LOCATION['label']} | "
    f"Windy @ {spot['lat']:.5f}, {spot['lon']:.5f}"
)

st.subheader(f"Solunar — {_format_view_date(view_date)}")
if solunar:
    best_hour = solunar.get("best_hour")
    if best_hour:
        st.markdown(f"**Mejor hora para entrar al agua:** {best_hour}")
    for i, window in enumerate(solunar.get("best_windows") or [], start=1):
        label = window.get("period_label", "Periodo")
        start = window.get("start", "?")
        end = window.get("end", "?")
        entry = window.get("entry_time", start)
        extra = ""
        if window.get("coincides_with") == "sunset":
            extra = " (coincide con puesta de sol)"
        elif window.get("coincides_with") == "sunrise":
            extra = " (coincide con salida de sol)"
        st.markdown(f"**Franja #{i}:** {start} → {end} | entrar {entry}{extra}")
        st.caption(label)
    st.caption(
        f"Actividad del día: {solunar.get('activity_day')} | "
        f"Fase: {solunar.get('phase')} | "
        f"Iluminación: {solunar.get('illumination_pct')}%"
    )
else:
    if view_mode == "missing":
        st.caption("No hay snapshot de solunar para este día. Captura condiciones en vivo para hoy.")
    else:
        st.caption("No se pudo cargar solunar.")

st.subheader(f"Windy — {_format_view_date(view_date)} (franjas 3 h)")
if windy.get("hourly"):
    merge_line = ""
    if merge_meta.get("snapshot_count", 0) > 1:
        capture_times = ", ".join(
            c[11:16] for c in merge_meta.get("captures", [])
        )
        merge_line = f" | **fusionado** ({merge_meta['snapshot_count']} capturas: {capture_times})"
    st.caption(
        f"Modelo {windy.get('model')} | agua {windy.get('water_temp_c')}°C | "
        f"actualizado {windy.get('update', '?')} | "
        f"coords {features.get('lat', spot['lat']):.5f}, {features.get('lon', spot['lon']):.5f}"
        f"{merge_line}"
    )
    rows = []
    for slot in windy["hourly"]:
        rows.append(
            {
                "Hora": f"{slot['local_hour']:02d}:00",
                "Temp °C": _round1(slot.get("temp_c")),
                "Viento km/h": _round1(slot.get("wind_speed_kmh")),
                "Rachas": _round1(slot.get("wind_gusts_kmh")),
                "Dir viento": slot.get("wind_direction"),
                "Olas m": _round1(slot.get("wave_height_m")),
                "Dir olas": slot.get("wave_direction"),
                "Swell1 m": _round1(slot.get("swell1_height_m")),
                "Periodo s": _round1(slot.get("swell1_period_s")),
                "Power kW/m": _round1(slot.get("wave_power_kw_m")),
            }
        )
    df = pd.DataFrame(rows)
    st.dataframe(style_windy_table(df), width="stretch")
    st.markdown(render_windy_legend_html(), unsafe_allow_html=True)
    st.caption(
        "Datos de Windy (ECMWF + ECMWF-WAM) para las coordenadas del sitio. "
        "Los colores siguen la tabla de Windy: en mar plano casi no hay tinte."
    )
else:
    if view_mode == "missing":
        st.caption("No hay snapshot de Windy para este día.")
    else:
        st.caption("No se pudo cargar Windy.")

with st.expander("Ver raw features / JSON guardado"):
    st.json(features)

st.subheader(f"Recomendaciones heurísticas (top 2) — {_format_view_date(view_date)}")
if view_mode == "missing":
    st.caption("Sin datos de condiciones para recomendar este día.")
else:
    recs = recommend(features=features, spot_id=spot_id, top_n=2)
    for i, rec in enumerate(recs, start=1):
        method_label = DEFAULT_METHODS.get(rec["method_id"], rec["method_id"])
        reasons = rec.get("reasons", [])
        st.markdown(f"**#{i}**: {method_label} (score {rec['score']}/10)")
        if reasons:
            st.caption(" - " + " | ".join(reasons))

st.divider()

st.subheader("Registrar salida (tu feedback)")
st.caption("El log siempre guarda las condiciones **de hoy**, aunque estés revisando otro día.")
with st.form("log_form", clear_on_submit=True):
    fishing_time = st.datetime_input("Hora de la salida", value=now)
    log_spot_id = st.selectbox(
        "Sitio",
        options=spot_ids,
        index=spot_ids.index(spot_id),
        format_func=lambda sid: spot_by_id[sid]["label"],
    )
    method_id = st.selectbox(
        "Método",
        options=list(DEFAULT_METHODS.keys()),
        format_func=lambda mid: DEFAULT_METHODS[mid],
    )
    score = st.slider("Score (0-10)", min_value=0, max_value=10, value=5, step=1)
    pulpos_choice = st.selectbox("¿Viste pulpos?", options=["No registrado", "No", "Sí"], index=0)
    pulpos_seen = None if pulpos_choice == "No registrado" else (pulpos_choice == "Sí")

    vis_label_to_value = {"baja": 1, "media": 2, "alta": 3}
    vis_choice = st.selectbox("Visibilidad", options=["No registrada", "baja", "media", "alta"], index=0)
    visibilidad = None if vis_choice == "No registrada" else vis_label_to_value[vis_choice]

    notas = st.text_area("Notas (rápido, libre)", height=120, placeholder="Ej: agua clara, corriente suave, vi 1 pulpo, etc.")
    submit = st.form_submit_button("Guardar log")

    if submit:
        if isinstance(fishing_time, datetime) and fishing_time.tzinfo is None:
            fishing_time = fishing_time.replace(tzinfo=now_local().tzinfo)

        log_features, log_snapshot_id = load_today_conditions_for_log(
            db_paths.db_path,
            spot_id=log_spot_id,
        )

        log = FishingLog(
            fishing_time=fishing_time,
            spot_id=log_spot_id,
            method_id=method_id,
            score=int(score),
            pulpos_seen=pulpos_seen,
            visibilidad=visibilidad,
            notas=notas,
            features_snapshot=log_features,
            condition_snapshot_id=log_snapshot_id,
        )
        add_log(db_paths.db_path, log)
        st.success(f"Log guardado (enlazado a snapshot #{log_snapshot_id} de hoy).")
        st.rerun()

st.divider()

st.subheader("Historial")
tab_logs, tab_snapshots = st.tabs(["Salidas de pesca", "Snapshots de condiciones"])

with tab_logs:
    col1, col2 = st.columns([2, 1])
    with col1:
        rows = list_logs(db_paths.db_path, limit=20)
        if rows:
            st.write(f"Últimos {len(rows)} logs")
            for r in rows:
                spot_label = fishing_spot_label(db_paths.db_path, r["spot_id"])
                method_label = DEFAULT_METHODS.get(r["method_id"], r["method_id"])
                snap_ref = f" | snap #{r['condition_snapshot_id']}" if r.get("condition_snapshot_id") else ""
                st.markdown(
                    f"- {r['fishing_time']} | {spot_label} | {method_label} | "
                    f"score {r['score']}{snap_ref}"
                )
                if r.get("notas"):
                    st.caption(r["notas"])
        else:
            st.caption("Aún no hay logs.")
    with col2:
        if st.button("Exportar CSV logs (últimos 200)"):
            export_path = os.path.join(BASE_DIR, "data", f"export_logs_{now.date().isoformat()}.csv")
            export_logs_csv(db_paths.db_path, export_path, limit=200)
            st.success(f"CSV exportado: {export_path}")

with tab_snapshots:
    day_snaps = list_condition_snapshots(
        db_paths.db_path,
        snapshot_date=view_date,
        spot_id=spot_id,
        limit=50,
    )
    if day_snaps:
        st.caption(
            f"{len(day_snaps)} capturas guardadas este día (no se borran; "
            f"el dashboard fusiona Windy por franja horaria)."
        )
        for s in day_snaps:
            windy_slots = len((s.get("windy") or {}).get("hourly", []))
            st.markdown(
                f"**#{s['id']}** — {s['captured_at']} | "
                f"coords {s.get('lat')}, {s.get('lon')} | "
                f"solunar {s.get('source_status', {}).get('solunar', '?')} | "
                f"windy {s.get('source_status', {}).get('windy', '?')} | "
                f"{windy_slots} franjas horarias"
            )
    else:
        st.caption(f"No hay snapshots guardados para {_format_view_date(view_date)}.")
