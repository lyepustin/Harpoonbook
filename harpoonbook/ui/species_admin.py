from __future__ import annotations

import streamlit as st

from harpoonbook.config import SPECIES_CATEGORIES
from harpoonbook.db import (
    create_species,
    deactivate_species,
    get_species,
    list_species,
    update_species,
)
from harpoonbook.spots import is_valid_spot_id, slugify


def render_species_admin(db_path: str) -> None:
    st.subheader("Especies")
    st.caption("Catálogo de lo que puedes registrar como visto o pescado (peces, crustáceos, medusas…).")

    col_list, col_form = st.columns([1.2, 1])

    with col_list:
        show_inactive = st.checkbox("Mostrar inactivas", value=False, key="sp_show_inactive")
        rows = list_species(db_path, active_only=not show_inactive)
        if not rows and not show_inactive:
            rows = list_species(db_path, active_only=False)

        if rows:
            for sp in rows:
                status = "activa" if sp["active"] else "inactiva"
                cat = SPECIES_CATEGORIES.get(sp.get("category") or "otro", sp.get("category"))
                st.markdown(f"**{sp['label']}** (`{sp['id']}`) — {cat} — {status}")
                if sp.get("notes"):
                    st.caption(sp["notes"])
        else:
            st.info("No hay especies. Crea la primera.")

    with col_form:
        tab_new, tab_edit = st.tabs(["Nueva", "Editar"])

        with tab_new:
            with st.form("new_species_form"):
                label = st.text_input("Nombre", placeholder="Ej: Corvina")
                sid = st.text_input("ID interno", placeholder="corvina")
                category = st.selectbox(
                    "Categoría",
                    options=list(SPECIES_CATEGORIES.keys()),
                    format_func=lambda k: SPECIES_CATEGORIES[k],
                )
                notes = st.text_area("Notas", height=60)
                create = st.form_submit_button("Crear especie")

            if create:
                final_id = (sid or slugify(label)).strip().lower()
                if not label.strip():
                    st.error("El nombre es obligatorio.")
                elif not is_valid_spot_id(final_id):
                    st.error("ID inválido (a-z, 0-9, _, empieza por letra).")
                elif get_species(db_path, final_id):
                    st.error(f"Ya existe `{final_id}`.")
                else:
                    create_species(db_path, final_id, label, category, notes=notes)
                    st.success(f"Especie `{final_id}` creada.")
                    st.rerun()

        with tab_edit:
            all_sp = list_species(db_path, active_only=False)
            if not all_sp:
                st.caption("Nada que editar.")
            else:
                edit_id = st.selectbox(
                    "Especie",
                    options=[s["id"] for s in all_sp],
                    format_func=lambda sid: next(s["label"] for s in all_sp if s["id"] == sid),
                    key="edit_species_select",
                )
                current = get_species(db_path, edit_id)
                if current:
                    with st.form("edit_species_form"):
                        new_label = st.text_input("Nombre", value=current["label"])
                        cat_keys = list(SPECIES_CATEGORIES.keys())
                        cur_cat = current.get("category") or "otro"
                        new_cat = st.selectbox(
                            "Categoría",
                            options=cat_keys,
                            index=cat_keys.index(cur_cat) if cur_cat in cat_keys else 0,
                            format_func=lambda k: SPECIES_CATEGORIES[k],
                        )
                        new_notes = st.text_area("Notas", value=current.get("notes") or "", height=60)
                        new_order = st.number_input(
                            "Orden",
                            value=int(current.get("sort_order") or 0),
                            step=1,
                        )
                        save = st.form_submit_button("Guardar")
                    if save:
                        update_species(
                            db_path,
                            edit_id,
                            label=new_label,
                            category=new_cat,
                            notes=new_notes,
                            sort_order=int(new_order),
                        )
                        st.success("Especie actualizada.")
                        st.rerun()

                    if current["active"]:
                        if st.button("Desactivar", type="secondary"):
                            deactivate_species(db_path, edit_id)
                            st.warning(f"`{edit_id}` desactivada.")
                            st.rerun()
                    else:
                        if st.button("Reactivar"):
                            update_species(db_path, edit_id, active=True)
                            st.success(f"`{edit_id}` reactivada.")
                            st.rerun()
