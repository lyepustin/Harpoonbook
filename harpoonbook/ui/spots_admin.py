from __future__ import annotations

from datetime import timedelta

import streamlit as st

from harpoonbook.bg_capture import (
    is_capture_all_running,
    start_background_capture_all,
    take_finished_capture_all,
)
from harpoonbook.clipboard_image import read_clipboard_image
from harpoonbook.config import (
    BOTTOM_TYPES,
    COMPASS_DIRECTIONS,
    EMPTY_SPOT_PROFILE,
    EXPOSURE_TYPES,
    RUNOFF_RISK_TYPES,
    SOLUNAR_LOCATION,
    SPOT_ANALYSIS_PROMPT,
    compass_es,
    personalized_spot_prompt,
)
from harpoonbook.db import (
    create_fishing_spot,
    deactivate_fishing_spot,
    delete_fishing_spot,
    get_fishing_spot,
    get_spot_map_image,
    list_fishing_spots,
    update_fishing_spot,
)
from harpoonbook.spot_profile import parse_chatgpt_spot_response
from harpoonbook.spots import format_geocode, is_valid_spot_id, resolve_coords, slugify
from harpoonbook.ui.spot_image import show_spot_map_image


def _windy_map_url(lat: float, lon: float) -> str:
    return f"https://www.windy.com/{lat}/{lon}/ecmwfWaves/waves?waves,{lat},{lon},14"


def _copy_text_to_clipboard(text: str) -> bool:
    """Copia texto Unicode al portapapeles (Windows CF_UNICODETEXT / fallbacks)."""
    # 1) Win32 CF_UNICODETEXT (punteros void* para Win64)
    try:
        import ctypes
        from ctypes import wintypes

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        user32.CloseClipboard.restype = wintypes.BOOL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        payload = text.encode("utf-16-le") + b"\x00\x00"
        if not user32.OpenClipboard(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            user32.EmptyClipboard()
            h_global = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
            if not h_global:
                raise ctypes.WinError(ctypes.get_last_error())
            locked = kernel32.GlobalLock(h_global)
            if not locked:
                raise ctypes.WinError(ctypes.get_last_error())
            ctypes.memmove(locked, payload, len(payload))
            kernel32.GlobalUnlock(h_global)
            if not user32.SetClipboardData(CF_UNICODETEXT, h_global):
                raise ctypes.WinError(ctypes.get_last_error())
            return True
        finally:
            user32.CloseClipboard()
    except Exception:
        pass

    # 2) PowerShell vía archivo UTF-8 (BOM) — fiable con textos largos
    try:
        import os
        import subprocess
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".txt", prefix="hb_clip_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8-sig", newline="\n") as fh:
                fh.write(text)
            ps = (
                f"Get-Content -LiteralPath '{path}' -Raw -Encoding utf8 "
                f"| Set-Clipboard"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                check=True,
                capture_output=True,
                timeout=15,
            )
            return True
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    except Exception:
        pass

    # 3) tkinter
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.destroy()
        return True
    except Exception:
        return False


def _copy_text_button(text: str, *, key: str, label: str = "Copiar al portapapeles") -> None:
    """Botón nativo Streamlit (sin components.html / iframe deprecados)."""
    if st.button(label, key=key):
        if _copy_text_to_clipboard(text):
            st.success("Copiado al portapapeles.")
        else:
            st.warning("No se pudo copiar. Abre «Ver prompt» y usa Ctrl+C.")


def _map_paste_controls(db_path: str, spot_id: str, *, key_prefix: str) -> None:
    """Zona para pegar captura desde Snipping Tool / portapapeles del sistema."""
    st.markdown("**Mapa**")
    has_map = bool(get_spot_map_image(db_path, spot_id))
    if has_map:
        show_spot_map_image(db_path, spot_id)
    else:
        st.caption("Sin mapa. Haz Win+Shift+S y luego pega.")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Pegar del portapapeles", key=f"{key_prefix}_paste_clip_{spot_id}"):
            grabbed = read_clipboard_image()
            if not grabbed:
                st.warning(
                    "No hay imagen en el portapapeles. "
                    "Usa Win+Shift+S (Snipping Tool) y vuelve a pulsar."
                )
            else:
                blob, mime = grabbed
                update_fishing_spot(
                    db_path,
                    spot_id,
                    map_image_blob=blob,
                    map_image_mime=mime,
                )
                st.success("Mapa guardado desde el portapapeles.")
                st.rerun()
    with c2:
        if has_map and st.button("Quitar mapa", key=f"{key_prefix}_clear_map_{spot_id}"):
            update_fishing_spot(db_path, spot_id, clear_map_image=True)
            st.rerun()

    up = st.file_uploader(
        "O subir archivo",
        type=["png", "jpg", "jpeg", "webp"],
        key=f"{key_prefix}_upload_{spot_id}",
    )
    if up is not None and st.button("Guardar archivo como mapa", key=f"{key_prefix}_save_up_{spot_id}"):
        update_fishing_spot(
            db_path,
            spot_id,
            map_image_blob=up.getvalue(),
            map_image_mime=up.type,
        )
        st.success("Mapa guardado.")
        st.rerun()


def _chatgpt_block(
    db_path: str,
    spot_id: str,
    label: str,
    lat: float,
    lon: float,
    *,
    key_prefix: str,
    saved_response: str | None = None,
) -> None:
    """Prompt personalizado + pegar respuesta dentro del mismo bloque."""
    st.markdown("**Análisis ChatGPT**")
    st.caption(
        "Adjunta en ChatGPT: (1) satélite y (2) hillshade/batimetría del mismo sitio. "
        "Opcional: añade `ANCLA_PROFUNDIDAD: … ~12 m` en el mensaje."
    )
    prompt = personalized_spot_prompt(label, lat, lon)
    _copy_text_button(prompt, key=f"{key_prefix}_copy_{spot_id}", label="Copiar prompt personalizado")
    with st.expander("Ver prompt", expanded=False):
        st.code(prompt, language=None)

    ta_key = f"{key_prefix}_chatgpt_{spot_id}"
    # Tras "Aplicar", forzar textarea desde BD (antes de instanciar el widget)
    apply_msg = st.session_state.pop("_chatgpt_apply_ok", None)
    if apply_msg and apply_msg.get("spot_id") == spot_id:
        st.session_state[ta_key] = saved_response or st.session_state.get(ta_key, "")
        if apply_msg.get("ok"):
            st.success(
                f"Ficha actualizada: fondo={apply_msg.get('bottom')}, "
                f"exposición={apply_msg.get('exposure')}, "
                f"costa={apply_msg.get('coast')}, "
                f"{apply_msg.get('dirs_n')} direcciones. Respuesta guardada."
            )
        else:
            st.error("No se pudo guardar la ficha.")
    elif ta_key not in st.session_state:
        st.session_state[ta_key] = saved_response or ""

    chatgpt_paste = st.text_area(
        "Pegar respuesta ChatGPT",
        height=160,
        key=ta_key,
        placeholder="Pega aquí la respuesta de ChatGPT…",
    )
    if st.button("Aplicar respuesta a la ficha", key=f"{key_prefix}_apply_{spot_id}"):
        if not chatgpt_paste.strip():
            st.warning("Pega primero la respuesta.")
        else:
            parsed = parse_chatgpt_spot_response(chatgpt_paste)
            profile = parsed["profile"]
            dirs = parsed["approach_directions"] or []
            dirs_n = len(dirs)
            ok = update_fishing_spot(
                db_path,
                spot_id,
                bottom_type=profile.get("bottom_type"),
                profile=profile,
                approach_directions=dirs,
                coast_facing=parsed["coast_facing"],
                chatgpt_response=chatgpt_paste.strip(),
            )
            # No tocar ta_key aquí (widget ya instanciado). Forzar resync en el siguiente run.
            st.session_state.pop("_edit_form_spot_id", None)
            st.session_state["_chatgpt_apply_ok"] = {
                "spot_id": spot_id,
                "bottom": profile.get("bottom_type"),
                "exposure": profile.get("exposure"),
                "coast": compass_es(parsed.get("coast_facing")) or "—",
                "dirs_n": dirs_n,
                "ok": ok,
            }
            st.rerun()


def _clear_edit_form_widget_state() -> None:
    """Fuerza que los campos de Editar lean de la BD (cambio de sitio o ChatGPT)."""
    prefixes = (
        "edit_bottom",
        "edit_bottom_detail",
        "edit_exposure",
        "edit_runoff",
        "edit_dmin",
        "edit_dmax",
        "edit_structure",
        "edit_entry",
        "edit_landmarks",
        "edit_current",
        "edit_summary",
        "edit_ndir",
        "edit_dir_",
        "edit_facing",
        "edit_label",
        "edit_geocode",
        "edit_lat",
        "edit_lon",
        "edit_notes",
        "edit_chatgpt_",
    )
    for key in list(st.session_state.keys()):
        if any(key == p or key.startswith(p) for p in prefixes):
            del st.session_state[key]


def _seed_edit_widgets_from_spot(spot: dict) -> None:
    """Rellena session_state ANTES de crear widgets (value= se ignora si hay key)."""
    profile = spot.get("profile") or {}
    spot_id = spot["id"]
    st.session_state["edit_label"] = spot.get("label") or ""
    st.session_state["edit_geocode"] = format_geocode(float(spot["lat"]), float(spot["lon"]))
    st.session_state["edit_lat"] = float(spot["lat"])
    st.session_state["edit_lon"] = float(spot["lon"])
    st.session_state["edit_notes"] = spot.get("notes") or ""
    st.session_state["edit_facing"] = compass_es(spot.get("coast_facing")) or ""
    bottom = spot.get("bottom_type") or profile.get("bottom_type") or "mixto"
    st.session_state["edit_bottom"] = bottom
    st.session_state["edit_bottom_detail"] = str(profile.get("bottom_detail") or "")
    st.session_state["edit_exposure"] = profile.get("exposure") or "mixto"
    risk = profile.get("runoff_risk") or "medio"
    st.session_state["edit_runoff"] = risk if risk in RUNOFF_RISK_TYPES else "medio"
    st.session_state["edit_dmin"] = (
        float(profile["depth_min_m"]) if profile.get("depth_min_m") is not None else 0.0
    )
    st.session_state["edit_dmax"] = (
        float(profile["depth_max_m"]) if profile.get("depth_max_m") is not None else 0.0
    )
    st.session_state["edit_structure"] = str(profile.get("structure_notes") or "")
    st.session_state["edit_entry"] = str(profile.get("entry_notes") or "")
    st.session_state["edit_landmarks"] = str(profile.get("landmarks") or "")
    st.session_state["edit_current"] = str(profile.get("local_current_notes") or "")
    st.session_state["edit_summary"] = str(profile.get("summary") or "")
    st.session_state[f"edit_chatgpt_{spot_id}"] = spot.get("chatgpt_response") or ""
    dirs = list(spot.get("approach_directions") or [])
    st.session_state["edit_ndir"] = len(dirs)
    for i, d in enumerate(dirs):
        st.session_state[f"edit_dir_c_{i}"] = compass_es(d.get("compass")) or "S"
        st.session_state[f"edit_dir_l_{i}"] = str(d.get("label") or "")
        st.session_state[f"edit_dir_n_{i}"] = str(d.get("notes") or "")


def _sync_edit_form_to_spot(spot_id: str, spot: dict | None = None, *, force: bool = False) -> None:
    """Si cambia el sitio (o force), resetea y siembra widgets desde la BD."""
    if not force and st.session_state.get("_edit_form_spot_id") == spot_id:
        return
    st.session_state["_edit_form_spot_id"] = spot_id
    _clear_edit_form_widget_state()
    st.session_state["_edit_dirs_spot"] = spot_id
    st.session_state.pop("confirm_delete_spot", None)
    if spot:
        _seed_edit_widgets_from_spot(spot)


def _ensure_state(key: str, default: object) -> None:
    if key not in st.session_state:
        st.session_state[key] = default


def _profile_form_fields(prefix: str, profile: dict, bottom_default: str) -> dict:
    bottom_keys = list(BOTTOM_TYPES.keys())
    exposure_keys = list(EXPOSURE_TYPES.keys())
    runoff_keys = list(RUNOFF_RISK_TYPES.keys())
    _ensure_state(f"{prefix}_bottom", bottom_default if bottom_default in bottom_keys else "mixto")
    _ensure_state(f"{prefix}_bottom_detail", str(profile.get("bottom_detail") or ""))
    _ensure_state(f"{prefix}_exposure", profile.get("exposure") or "mixto")
    risk_default = profile.get("runoff_risk") or "medio"
    _ensure_state(
        f"{prefix}_runoff",
        risk_default if risk_default in runoff_keys else "medio",
    )
    _ensure_state(
        f"{prefix}_dmin",
        float(profile["depth_min_m"]) if profile.get("depth_min_m") is not None else 0.0,
    )
    _ensure_state(
        f"{prefix}_dmax",
        float(profile["depth_max_m"]) if profile.get("depth_max_m") is not None else 0.0,
    )
    _ensure_state(f"{prefix}_structure", str(profile.get("structure_notes") or ""))
    _ensure_state(f"{prefix}_entry", str(profile.get("entry_notes") or ""))
    _ensure_state(f"{prefix}_landmarks", str(profile.get("landmarks") or ""))
    _ensure_state(f"{prefix}_current", str(profile.get("local_current_notes") or ""))
    _ensure_state(f"{prefix}_summary", str(profile.get("summary") or ""))

    bottom = st.selectbox(
        "Tipo de fondo",
        options=bottom_keys,
        format_func=lambda k: BOTTOM_TYPES[k],
        key=f"{prefix}_bottom",
    )
    bottom_detail = st.text_input("Detalle del fondo", key=f"{prefix}_bottom_detail")
    exposure = st.selectbox(
        "Exposición",
        options=exposure_keys,
        format_func=lambda k: EXPOSURE_TYPES[k],
        key=f"{prefix}_exposure",
    )
    runoff_risk = st.selectbox(
        "Riesgo de escorrentía",
        options=runoff_keys,
        format_func=lambda k: RUNOFF_RISK_TYPES[k],
        key=f"{prefix}_runoff",
        help="Alto = barrancos / drenaje urbano a calas. Tras lluvia fuerte baja la visibilidad de orilla.",
    )
    c1, c2 = st.columns(2)
    with c1:
        depth_min = st.number_input(
            "Prof. min (m)",
            min_value=0.0,
            step=0.5,
            key=f"{prefix}_dmin",
        )
    with c2:
        depth_max = st.number_input(
            "Prof. max (m)",
            min_value=0.0,
            step=0.5,
            key=f"{prefix}_dmax",
        )
    structure = st.text_area(
        "Estructura del fondo (hillshade)",
        height=70,
        key=f"{prefix}_structure",
        help="Lengüetas, canales, caídas — suele venir del análisis con batimetría.",
    )
    entry_notes = st.text_area(
        "Entradas desde orilla",
        height=60,
        key=f"{prefix}_entry",
    )
    landmarks = st.text_area(
        "Referencias / peligros",
        height=60,
        key=f"{prefix}_landmarks",
    )
    current_notes = st.text_area(
        "Notas de corriente local",
        height=60,
        key=f"{prefix}_current",
    )
    summary = st.text_area("Resumen", height=100, key=f"{prefix}_summary")
    return {
        "bottom_type": bottom,
        "bottom_detail": bottom_detail,
        "exposure": exposure,
        "runoff_risk": runoff_risk,
        "depth_min_m": depth_min if depth_min > 0 else None,
        "depth_max_m": depth_max if depth_max > 0 else None,
        "structure_notes": structure,
        "entry_notes": entry_notes,
        "landmarks": landmarks,
        "local_current_notes": current_notes,
        "summary": summary,
    }


def _directions_editor(prefix: str, current: list[dict], *, spot_id: str | None = None) -> list[dict]:
    st.markdown("**Direcciones de pesca posibles**")
    n_key = f"{prefix}_ndir"
    sync_key = f"_{prefix}_dirs_spot"
    # Si cambiamos de spot o el conteo en BD es mayor que el del widget, sincronizar
    if spot_id is not None and st.session_state.get(sync_key) != spot_id:
        st.session_state[sync_key] = spot_id
        st.session_state[n_key] = len(current)
        for key in list(st.session_state.keys()):
            if key.startswith(f"{prefix}_dir_"):
                del st.session_state[key]
    elif n_key not in st.session_state:
        st.session_state[n_key] = len(current)
    elif len(current) > int(st.session_state.get(n_key) or 0):
        # BD tiene más direcciones que el widget (p.ej. tras ChatGPT)
        st.session_state[n_key] = len(current)

    n = st.number_input(
        "Número de direcciones",
        min_value=0,
        max_value=8,
        step=1,
        key=n_key,
    )
    compass_keys = list(COMPASS_DIRECTIONS.keys())
    out: list[dict] = []
    for i in range(int(n)):
        base = current[i] if i < len(current) else {}
        cols = st.columns([1, 3, 2])
        with cols[0]:
            cur_c = compass_es(base.get("compass")) or "S"
            # Selectbox con key: fijar índice solo si la key no existe
            c_key = f"{prefix}_dir_c_{i}"
            if c_key not in st.session_state:
                st.session_state[c_key] = cur_c if cur_c in compass_keys else compass_keys[0]
            compass = st.selectbox(
                f"Brújula #{i+1}",
                options=compass_keys,
                key=c_key,
            )
        with cols[1]:
            l_key = f"{prefix}_dir_l_{i}"
            if l_key not in st.session_state:
                st.session_state[l_key] = str(base.get("label") or "")
            label = st.text_input(f"Etiqueta #{i+1}", key=l_key)
        with cols[2]:
            n_key_i = f"{prefix}_dir_n_{i}"
            if n_key_i not in st.session_state:
                st.session_state[n_key_i] = str(base.get("notes") or "")
            notes = st.text_input(f"Notas #{i+1}", key=n_key_i)
        out.append({"compass": compass, "label": label, "notes": notes})
    return out


def render_spots_admin(db_path: str) -> None:
    st.subheader("Sitios de pesca")
    st.caption(
        f"**Solunar y marea** siempre desde {SOLUNAR_LOCATION['label']}. "
        "Cada sitio: coordenadas, mapa (portapapeles), ficha y direcciones."
    )

    col_list, col_form = st.columns([1.2, 1])

    with col_list:
        st.markdown("#### Sitios configurados")
        show_inactive = st.checkbox("Mostrar inactivos", value=False)
        spots = list_fishing_spots(db_path, active_only=not show_inactive)
        if not spots and not show_inactive:
            spots = list_fishing_spots(db_path, active_only=False)

        if spots:
            spot_by_id = {s["id"]: s for s in spots}
            spot_ids = [s["id"] for s in spots]

            def _spot_option_label(sid: str) -> str:
                s = spot_by_id[sid]
                status_mark = "" if s["active"] else " · inactivo"
                map_mark = " · mapa" if s.get("has_map_image") else ""
                return f"{s['label']}{status_mark}{map_mark}"

            selected_id = st.radio(
                "Sitio",
                options=spot_ids,
                format_func=_spot_option_label,
                key="configured_spot_select",
                label_visibility="collapsed",
            )

            # Al elegir un sitio → abrir Editar a la derecha (antes de renderizar el form)
            prev_id = st.session_state.get("_prev_configured_spot")
            if selected_id != prev_id:
                st.session_state["_prev_configured_spot"] = selected_id
                st.session_state["edit_spot_select"] = selected_id
                st.session_state["spots_form_mode"] = "Editar sitio"
                # Invalidar form: se resincroniza al renderizar Editar
                st.session_state.pop("_edit_form_spot_id", None)

            spot = spot_by_id[selected_id]
            left, right = st.columns([1.4, 1])
            with left:
                facing = compass_es(spot.get("coast_facing")) or "—"
                facing_txt = (
                    f"{facing} ({COMPASS_DIRECTIONS.get(facing, '')})"
                    if facing != "—"
                    else "—"
                )
                dirs = spot.get("approach_directions") or []
                profile = spot.get("profile") or {}
                st.caption(f"`{spot['id']}`")
                st.markdown(
                    f"**Coords:** `{format_geocode(float(spot['lat']), float(spot['lon']))}`  \n"
                    f"**Fondo:** {BOTTOM_TYPES.get(spot.get('bottom_type') or 'mixto', 'Mixto')}  \n"
                    f"**Costa mira:** {facing_txt}  \n"
                    f"**Exposición:** {profile.get('exposure') or '—'}  \n"
                    f"**Escorrentía:** {RUNOFF_RISK_TYPES.get(profile.get('runoff_risk') or 'medio', 'Medio')}"
                )
                if dirs:
                    dir_txt = ", ".join(
                        f"{compass_es(d.get('compass')) or '?'}"
                        + (f" ({d['label']})" if d.get("label") else "")
                        for d in dirs
                    )
                    st.markdown(f"**Direcciones:** {dir_txt}")
                if profile.get("summary"):
                    summary = str(profile["summary"])
                    st.caption(summary if len(summary) <= 220 else summary[:220] + "…")
                st.link_button(
                    "Abrir en Windy",
                    _windy_map_url(float(spot["lat"]), float(spot["lon"])),
                    key=f"windy_link_{spot['id']}",
                )
            with right:
                if spot.get("has_map_image"):
                    show_spot_map_image(db_path, spot["id"], max_width=260)
                else:
                    st.caption("Sin mapa.")
        else:
            st.info("No hay sitios. Crea el primero en el formulario.")

        finished_all = take_finished_capture_all(db_path)
        if finished_all:
            if finished_all["status"] == "done":
                results = finished_all.get("results") or []
                if results:
                    for row in results:
                        st.success(
                            f"`{row['spot_id']}` → snapshot #{row['snapshot_id']}"
                        )
                else:
                    st.warning("No hay sitios activos para capturar.")
            elif finished_all["status"] == "error":
                st.warning(
                    finished_all.get("error") or "Error desconocido al capturar todo"
                )

        capture_all_running = is_capture_all_running(db_path)
        n_active = len(list_fishing_spots(db_path, active_only=True))
        if st.button(
            f"Capturar todo ({n_active} sitios)",
            type="primary",
            disabled=capture_all_running,
            help="Windy, marea, corrientes y óptica para todos los sitios activos (en segundo plano).",
        ):
            if not start_background_capture_all(db_path):
                st.warning("Ya hay una captura en curso. Espera a que termine.")
            else:
                st.rerun()

        if capture_all_running:
            st.info(
                "Capturando todas las fuentes en segundo plano "
                "(Windy, marea, corrientes, óptica)…"
            )

            @st.fragment(run_every=timedelta(seconds=2))
            def _poll_capture_all() -> None:
                if not is_capture_all_running(db_path):
                    st.rerun(scope="app")
                st.caption("Sigue en otro proceso. Puedes seguir usando esta página.")

            _poll_capture_all()

    with col_form:
        if "spots_form_mode" not in st.session_state:
            st.session_state["spots_form_mode"] = "Nuevo sitio"

        mode = st.radio(
            "Formulario",
            ["Nuevo sitio", "Editar sitio", "Prompt base"],
            key="spots_form_mode",
            horizontal=True,
        )

        if mode == "Prompt base":
            st.markdown(
                "Plantilla genérica (sin nombre/coords). Mejor usa **Copiar prompt personalizado** en cada sitio. "
                "En ChatGPT: satélite + hillshade; opcional `ANCLA_PROFUNDIDAD`."
            )
            st.code(SPOT_ANALYSIS_PROMPT, language=None)
            _copy_text_button(SPOT_ANALYSIS_PROMPT, key="base_prompt_copy", label="Copiar prompt base")
            st.download_button(
                "Descargar prompt .txt",
                data=SPOT_ANALYSIS_PROMPT,
                file_name="prompt_analisis_spot.txt",
                mime="text/plain",
            )

        elif mode == "Nuevo sitio":
            st.markdown("1) Nombre y coordenadas → 2) Copiar prompt → 3) Pegar respuesta → 4) Crear")
            label = st.text_input("Nombre", placeholder="Ej: Punta de la Glea", key="new_label")
            spot_id = st.text_input(
                "ID interno (slug)",
                placeholder="punta_glea",
                help="Solo minúsculas, números y guión bajo.",
                key="new_slug",
            )
            geocode = st.text_input(
                "Coordenadas (Google Maps)",
                placeholder="37.8235652557405, -0.7553338523683103",
                key="new_geocode",
            )
            with st.expander("Ajuste manual lat/lon"):
                lat = st.number_input("Latitud", value=37.907, format="%.10f", step=0.0001, key="new_lat")
                lon = st.number_input("Longitud", value=-0.717, format="%.10f", step=0.0001, key="new_lon")

            coords_preview = resolve_coords(geocode, lat, lon)
            if label.strip() and coords_preview:
                lat_p, lon_p = coords_preview
                prompt = personalized_spot_prompt(label.strip(), lat_p, lon_p)
                _copy_text_button(prompt, key="new_copy_prompt", label="Copiar prompt personalizado")
                with st.expander("Ver prompt personalizado"):
                    st.code(prompt, language=None)

            chatgpt_paste = st.text_area(
                "Pegar respuesta ChatGPT (opcional)",
                height=120,
                key="new_chatgpt",
            )
            notes = st.text_area("Notas", height=60, key="new_notes")
            st.caption("Mapa: tras crear el sitio, ábrelo y usa «Pegar del portapapeles».")

            coast_keys = [""] + list(COMPASS_DIRECTIONS.keys())
            coast_facing = st.selectbox(
                "Orientación de la costa (hacia el mar)",
                options=coast_keys,
                format_func=lambda k: "—" if not k else f"{k} ({COMPASS_DIRECTIONS[k]})",
                key="new_facing",
            )
            profile = _profile_form_fields("new", dict(EMPTY_SPOT_PROFILE), "mixto")
            directions = _directions_editor("new", [])

            if st.button("Crear sitio", type="primary", key="new_create"):
                final_id = (spot_id or slugify(label)).strip().lower()
                coords = resolve_coords(geocode, lat, lon)
                if chatgpt_paste.strip():
                    parsed = parse_chatgpt_spot_response(chatgpt_paste)
                    profile = {**profile, **parsed["profile"]}
                    if parsed["approach_directions"]:
                        directions = parsed["approach_directions"]
                    if parsed["coast_facing"]:
                        coast_facing = parsed["coast_facing"]
                if not label.strip():
                    st.error("El nombre es obligatorio.")
                elif coords is None:
                    st.error("Coordenadas no válidas.")
                elif not is_valid_spot_id(final_id):
                    st.error("ID inválido. Usa solo a-z, 0-9 y _, empezando por letra.")
                elif get_fishing_spot(db_path, final_id):
                    st.error(f"Ya existe un sitio con id `{final_id}`.")
                else:
                    lat_v, lon_v = coords
                    # Mapa opcional desde portapapeles al crear
                    blob = mime = None
                    grabbed = read_clipboard_image()
                    if grabbed:
                        blob, mime = grabbed
                    create_fishing_spot(
                        db_path,
                        spot_id=final_id,
                        label=label,
                        lat=lat_v,
                        lon=lon_v,
                        notes=notes,
                        bottom_type=profile["bottom_type"],
                        profile=profile,
                        approach_directions=directions,
                        coast_facing=coast_facing or None,
                        map_image_blob=blob,
                        map_image_mime=mime,
                        chatgpt_response=chatgpt_paste.strip() or None,
                    )
                    msg = f"Sitio `{final_id}` creado."
                    if blob:
                        msg += " Mapa tomado del portapapeles."
                    st.success(msg)
                    st.session_state["configured_spot_select"] = final_id
                    st.session_state["_prev_configured_spot"] = final_id
                    st.session_state["edit_spot_select"] = final_id
                    st.session_state["spots_form_mode"] = "Editar sitio"
                    st.rerun()

        else:  # Editar sitio
            all_spots = list_fishing_spots(db_path, active_only=False)
            if not all_spots:
                st.caption("No hay sitios para editar.")
            else:
                ids = [s["id"] for s in all_spots]
                if st.session_state.get("edit_spot_select") not in ids:
                    st.session_state["edit_spot_select"] = ids[0]

                edit_id = st.selectbox(
                    "Seleccionar sitio",
                    options=ids,
                    format_func=lambda sid: next(s["label"] for s in all_spots if s["id"] == sid),
                    key="edit_spot_select",
                )
                current = get_fishing_spot(db_path, edit_id)
                force_resync = (
                    st.session_state.get("_chatgpt_apply_ok", {}).get("spot_id") == edit_id
                )
                if current:
                    _sync_edit_form_to_spot(edit_id, current, force=force_resync)
                    st.markdown("##### Datos básicos")
                    new_label = st.text_input("Nombre", key="edit_label")
                    new_geocode = st.text_input("Coordenadas (Google Maps)", key="edit_geocode")
                    with st.expander("Ajuste manual lat/lon"):
                        new_lat = st.number_input(
                            "Latitud",
                            format="%.10f",
                            step=0.0001,
                            key="edit_lat",
                        )
                        new_lon = st.number_input(
                            "Longitud",
                            format="%.10f",
                            step=0.0001,
                            key="edit_lon",
                        )
                    new_notes = st.text_area("Notas", height=60, key="edit_notes")

                    coords_now = resolve_coords(new_geocode, new_lat, new_lon)
                    lat_for_prompt = coords_now[0] if coords_now else float(current["lat"])
                    lon_for_prompt = coords_now[1] if coords_now else float(current["lon"])

                    _chatgpt_block(
                        db_path,
                        edit_id,
                        new_label or current["label"],
                        lat_for_prompt,
                        lon_for_prompt,
                        key_prefix="edit",
                        saved_response=current.get("chatgpt_response"),
                    )
                    _map_paste_controls(db_path, edit_id, key_prefix="edit")

                    st.markdown("##### Ficha y direcciones")
                    coast_keys = [""] + list(COMPASS_DIRECTIONS.keys())
                    _ensure_state("edit_facing", compass_es(current.get("coast_facing")) or "")
                    new_facing = st.selectbox(
                        "Orientación de la costa (hacia el mar)",
                        options=coast_keys,
                        format_func=lambda k: "—" if not k else f"{k} ({COMPASS_DIRECTIONS[k]})",
                        key="edit_facing",
                    )
                    profile = _profile_form_fields(
                        "edit",
                        current.get("profile") or dict(EMPTY_SPOT_PROFILE),
                        current.get("bottom_type") or "mixto",
                    )
                    directions = _directions_editor(
                        "edit",
                        list(current.get("approach_directions") or []),
                        spot_id=edit_id,
                    )

                    if st.button("Guardar cambios", type="primary", key="edit_save"):
                        coords = resolve_coords(new_geocode, new_lat, new_lon)
                        if coords is None:
                            st.error("Coordenadas no válidas.")
                        else:
                            lat_v, lon_v = coords
                            chatgpt_saved = st.session_state.get(f"edit_chatgpt_{edit_id}")
                            update_fishing_spot(
                                db_path,
                                edit_id,
                                label=new_label,
                                lat=lat_v,
                                lon=lon_v,
                                notes=new_notes,
                                bottom_type=profile["bottom_type"],
                                profile=profile,
                                approach_directions=directions,
                                coast_facing=new_facing or None,
                                chatgpt_response=chatgpt_saved if chatgpt_saved is not None else None,
                            )
                            st.success("Sitio actualizado.")
                            st.rerun()

                    act_col, del_col = st.columns(2)
                    with act_col:
                        if current["active"]:
                            if st.button("Desactivar sitio", type="secondary", key="edit_deact"):
                                deactivate_fishing_spot(db_path, edit_id)
                                st.warning(f"`{edit_id}` desactivado.")
                                st.rerun()
                        else:
                            if st.button("Reactivar sitio", key="edit_react"):
                                update_fishing_spot(db_path, edit_id, active=True)
                                st.success(f"`{edit_id}` reactivado.")
                                st.rerun()
                    with del_col:
                        if st.button("Borrar sitio", type="secondary", key="edit_delete"):
                            st.session_state["confirm_delete_spot"] = edit_id

                    if st.session_state.get("confirm_delete_spot") == edit_id:
                        st.error(
                            f"Vas a borrar **{current['label']}** (`{edit_id}`) y sus snapshots. "
                            "Las salidas de pesca se conservan en el historial."
                        )
                        c_ok, c_no = st.columns(2)
                        with c_ok:
                            if st.button("Confirmar borrado", type="primary", key="edit_delete_ok"):
                                if delete_fishing_spot(db_path, edit_id):
                                    st.session_state.pop("confirm_delete_spot", None)
                                    st.session_state.pop("edit_spot_select", None)
                                    st.success(f"Sitio `{edit_id}` borrado.")
                                    st.rerun()
                                else:
                                    st.error("No se pudo borrar el sitio.")
                        with c_no:
                            if st.button("Cancelar", key="edit_delete_cancel"):
                                st.session_state.pop("confirm_delete_spot", None)
                                st.rerun()
