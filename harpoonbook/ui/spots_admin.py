from __future__ import annotations

import streamlit as st

from harpoonbook.conditions import capture_all_spots
from harpoonbook.config import SOLUNAR_LOCATION
from harpoonbook.db import (
    create_fishing_spot,
    deactivate_fishing_spot,
    get_fishing_spot,
    list_fishing_spots,
    update_fishing_spot,
)
from harpoonbook.spots import format_geocode, is_valid_spot_id, resolve_coords, slugify


def _windy_map_url(lat: float, lon: float) -> str:
    return f"https://www.windy.com/{lat}/{lon}/ecmwfWaves/waves?waves,{lat},{lon},14"


def render_spots_admin(db_path: str) -> None:
    st.subheader("Sitios de pesca")
    st.caption(
        f"**Solunar y marea** siempre desde {SOLUNAR_LOCATION['label']} "
        f"({SOLUNAR_LOCATION['lat']}, {SOLUNAR_LOCATION['lon']}). "
        "Las coordenadas de cada sitio solo afectan a **Windy** (viento y olas)."
    )

    col_list, col_form = st.columns([1.2, 1])

    with col_list:
        st.markdown("#### Sitios configurados")
        show_inactive = st.checkbox("Mostrar inactivos", value=False)
        spots = list_fishing_spots(db_path, active_only=not show_inactive)
        if not spots and not show_inactive:
            spots = list_fishing_spots(db_path, active_only=False)

        if spots:
            for spot in spots:
                status = "activo" if spot["active"] else "inactivo"
                st.markdown(
                    f"**{spot['label']}** (`{spot['id']}`) — {status}  \n"
                    f"Windy: `{format_geocode(float(spot['lat']), float(spot['lon']))}`"
                )
                st.link_button(
                    "Abrir en Windy",
                    _windy_map_url(float(spot["lat"]), float(spot["lon"])),
                    key=f"windy_link_{spot['id']}",
                )
        else:
            st.info("No hay sitios. Crea el primero en el formulario.")

        if st.button("Capturar Windy para todos los sitios activos", type="primary"):
            with st.spinner("Capturando…"):
                results = capture_all_spots(db_path)
            if results:
                for spot_id, snapshot_id, features in results:
                    slots = len((features.get("windy") or {}).get("hourly", []))
                    st.success(f"`{spot_id}` → snapshot #{snapshot_id} ({slots} franjas)")
            else:
                st.warning("No hay sitios activos para capturar.")

    with col_form:
        tab_new, tab_edit = st.tabs(["Nuevo sitio", "Editar sitio"])

        with tab_new:
            with st.form("new_spot_form"):
                label = st.text_input("Nombre", placeholder="Ej: Punta de la Glea")
                spot_id = st.text_input(
                    "ID interno (slug)",
                    placeholder="punta_glea",
                    help="Solo minúsculas, números y guión bajo. Se sugiere automáticamente.",
                )
                geocode = st.text_input(
                    "Coordenadas Windy (Google Maps)",
                    placeholder="37.8235652557405, -0.7553338523683103",
                    help="Copia y pega directamente desde Google Maps: latitud, longitud",
                )
                with st.expander("Ajuste manual lat/lon"):
                    lat = st.number_input("Latitud", value=37.907, format="%.10f", step=0.0001)
                    lon = st.number_input("Longitud", value=-0.717, format="%.10f", step=0.0001)
                notes = st.text_area("Notas", height=80)
                create = st.form_submit_button("Crear sitio")

            if create:
                final_id = (spot_id or slugify(label)).strip().lower()
                coords = resolve_coords(geocode, lat, lon)
                if not label.strip():
                    st.error("El nombre es obligatorio.")
                elif coords is None:
                    st.error(
                        "Coordenadas no válidas. Usa el formato de Google Maps: "
                        "`37.8235652557405, -0.7553338523683103`"
                    )
                elif not is_valid_spot_id(final_id):
                    st.error("ID inválido. Usa solo a-z, 0-9 y _, empezando por letra.")
                elif get_fishing_spot(db_path, final_id):
                    st.error(f"Ya existe un sitio con id `{final_id}`.")
                else:
                    lat_v, lon_v = coords
                    create_fishing_spot(
                        db_path,
                        spot_id=final_id,
                        label=label,
                        lat=lat_v,
                        lon=lon_v,
                        notes=notes,
                    )
                    st.success(f"Sitio `{final_id}` creado en `{format_geocode(lat_v, lon_v)}`.")
                    st.rerun()

        with tab_edit:
            all_spots = list_fishing_spots(db_path, active_only=False)
            if not all_spots:
                st.caption("No hay sitios para editar.")
            else:
                edit_id = st.selectbox(
                    "Seleccionar sitio",
                    options=[s["id"] for s in all_spots],
                    format_func=lambda sid: next(s["label"] for s in all_spots if s["id"] == sid),
                    key="edit_spot_select",
                )
                current = get_fishing_spot(db_path, edit_id)
                if current:
                    with st.form("edit_spot_form"):
                        new_label = st.text_input("Nombre", value=current["label"])
                        new_geocode = st.text_input(
                            "Coordenadas Windy (Google Maps)",
                            value=format_geocode(float(current["lat"]), float(current["lon"])),
                            help="Pega desde Google Maps para reemplazar las coords",
                        )
                        with st.expander("Ajuste manual lat/lon"):
                            new_lat = st.number_input(
                                "Latitud",
                                value=float(current["lat"]),
                                format="%.10f",
                                step=0.0001,
                            )
                            new_lon = st.number_input(
                                "Longitud",
                                value=float(current["lon"]),
                                format="%.10f",
                                step=0.0001,
                            )
                        new_notes = st.text_area("Notas", value=current.get("notes") or "", height=80)
                        new_order = st.number_input(
                            "Orden en listas",
                            value=int(current.get("sort_order") or 0),
                            step=1,
                        )
                        save = st.form_submit_button("Guardar cambios")
                    if save:
                        coords = resolve_coords(new_geocode, new_lat, new_lon)
                        if coords is None:
                            st.error(
                                "Coordenadas no válidas. Usa el formato de Google Maps: "
                                "`37.8235652557405, -0.7553338523683103`"
                            )
                        else:
                            lat_v, lon_v = coords
                            update_fishing_spot(
                                db_path,
                                edit_id,
                                label=new_label,
                                lat=lat_v,
                                lon=lon_v,
                                notes=new_notes,
                                sort_order=int(new_order),
                            )
                            st.success(f"Sitio actualizado → `{format_geocode(lat_v, lon_v)}`.")
                            st.rerun()

                    if current["active"]:
                        if st.button("Desactivar sitio", type="secondary"):
                            deactivate_fishing_spot(db_path, edit_id)
                            st.warning(f"`{edit_id}` desactivado (los logs históricos se conservan).")
                            st.rerun()
                    else:
                        if st.button("Reactivar sitio"):
                            update_fishing_spot(db_path, edit_id, active=True)
                            st.success(f"`{edit_id}` reactivado.")
                            st.rerun()
