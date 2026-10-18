from __future__ import annotations

import re
from typing import Any

from harpoonbook.config import (
    COMPASS_DIRECTIONS,
    EMPTY_SPOT_PROFILE,
    EXPOSURE_TYPES,
    compass_es,
    rewrite_compass_es,
)

KNOWN_SECTIONS = (
    "TIPO_DE_FONDO",
    "ESTRUCTURA_FONDO",
    "EXPOSICION",
    "ORIENTACION_COSTA",
    "PROFUNDIDAD_APARENTE",
    "ENTRADAS",
    "DIRECCIONES_PESCA",
    "PELIGROS_Y_REFERENCIAS",
    "CORRIENTE_LOCAL",
    "RESUMEN",
    "ANCLA_PROFUNDIDAD",  # a veces el usuario la deja en la respuesta
)

# Brújulas más largas primero para no comerse NE como N, etc.
_COMPASS_KEYS_LONG_FIRST = sorted(COMPASS_DIRECTIONS.keys(), key=len, reverse=True)


def empty_profile() -> dict[str, Any]:
    return dict(EMPTY_SPOT_PROFILE)


def _normalize_chatgpt_text(raw: str) -> str:
    """Quita markdown habitual de ChatGPT (**cabeceras**, bullets) sin cambiar el contenido."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    # **TIPO_DE_FONDO:** / **TIPO_DE_FONDO**:
    text = re.sub(r"\*\*([A-ZÁÉÍÓÚÑÜ_]+)\s*:\*\*", r"\1:", text)
    text = re.sub(r"\*\*([A-ZÁÉÍÓÚÑÜ_]+)\*\*\s*:", r"\1:", text)
    # ### TIPO_DE_FONDO:
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    # * **E:** → * E:  (direcciones en negrita)
    text = re.sub(r"\*\*([A-Za-zÁÉÍÓÚáéíóú]{1,3})\s*:\*\*", r"\1:", text)
    text = re.sub(r"\*\*([A-Za-zÁÉÍÓÚáéíóú]{1,3})\*\*\s*:", r"\1:", text)
    return text


def _extract_section(raw: str, name: str) -> str | None:
    """Extrae una sección solo hasta la siguiente cabecera conocida (no cualquier N:/S:)."""
    # Permite restos de markdown: **NAME:** o * NAME:
    start_re = rf"(?im)^\s*\**\s*{re.escape(name)}\s*\**\s*:\s*"
    start_m = re.search(start_re, raw)
    if not start_m:
        return None
    rest = raw[start_m.end() :]
    next_headers = "|".join(re.escape(s) for s in KNOWN_SECTIONS if s != name)
    end_m = re.search(rf"(?im)^\s*\**(?:{next_headers})\s*\**\s*:", rest)
    if end_m:
        return rest[: end_m.start()].strip()
    return rest.strip()


def _match_bottom_type(text: str) -> str | None:
    low = text.lower().strip()
    head = re.split(r"[—\-–:|,]", low, maxsplit=1)[0].strip()
    aliases = {
        "mixto": "mixto",
        "mixta": "mixto",
        "roca": "roca",
        "rocoso": "roca",
        "rocosa": "roca",
        "arena": "arena",
        "arenoso": "arena",
        "arenosa": "arena",
    }
    for alias, key in aliases.items():
        if re.search(rf"\b{alias}\b", head):
            return key
    for alias, key in aliases.items():
        if re.search(rf"\b{alias}\b", low):
            return key
    return None


def _match_exposure(text: str) -> str | None:
    low = text.lower()
    head = re.split(r"[—\-–:|,]", low, maxsplit=1)[0].strip()
    aliases = {
        "abierta": "abierta",
        "abierto": "abierta",
        "resguardada": "resguardada",
        "resguardado": "resguardada",
        "mixto": "mixto",
        "mixta": "mixto",
        "mixtas": "mixto",
    }
    for alias, key in aliases.items():
        if re.search(rf"\b{alias}\b", head):
            return key
    for alias, key in aliases.items():
        if re.search(rf"\b{alias}\b", low):
            return key
    return None


def _parse_directions(block: str) -> list[dict[str, str]]:
    """
    Acepta:
      - E: hacia Cala X
      - E: hacia Cala X — lengüeta rocosa
      * **E:** hacia… (tras normalizar)
    """
    directions: list[dict[str, str]] = []
    for line in block.splitlines():
        line = line.strip()
        line = re.sub(r"^\*\s+", "", line)
        line = line.lstrip("-•* ").strip()
        line = line.replace("**", "").strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-zÁÉÍÓÚáéíóú]{1,3})\s*[:\-–]\s*(.+)$", line)
        if m:
            raw_c = m.group(1).upper()
            raw_c = (
                raw_c.replace("Á", "A")
                .replace("É", "E")
                .replace("Í", "I")
                .replace("Ó", "O")
                .replace("Ú", "U")
            )
            raw_c = compass_es(raw_c) or raw_c
            if raw_c in COMPASS_DIRECTIONS:
                rest = m.group(2).strip()
                label, notes = rest, ""
                for sep in (" — ", " – ", " - ", " | "):
                    if sep in rest:
                        label, notes = rest.split(sep, 1)
                        label, notes = label.strip(), notes.strip()
                        break
                directions.append(
                    {
                        "compass": raw_c,
                        "label": label[:120],
                        "notes": notes[:200],
                    }
                )
                continue
        upper = rewrite_compass_es(line.upper()) or line.upper()
        for key in _COMPASS_KEYS_LONG_FIRST:
            if re.search(rf"\b{key}\b", upper):
                directions.append({"compass": key, "label": line[:120], "notes": ""})
                break
    return directions


def parse_chatgpt_spot_response(text: str) -> dict[str, Any]:
    """
    Parseo de la respuesta ChatGPT (secciones TYPE: valor).
    Tolera markdown (**cabeceras**, listas con *).
    """
    profile = empty_profile()
    directions: list[dict[str, str]] = []
    coast_facing: str | None = None

    if not text or not text.strip():
        return {"profile": profile, "approach_directions": directions, "coast_facing": coast_facing}

    raw = _normalize_chatgpt_text(text.strip())
    profile["summary"] = raw

    fondo = _extract_section(raw, "TIPO_DE_FONDO")
    if fondo:
        matched = _match_bottom_type(fondo)
        if matched:
            profile["bottom_type"] = matched
        profile["bottom_detail"] = fondo

    estructura = _extract_section(raw, "ESTRUCTURA_FONDO")
    if estructura:
        profile["structure_notes"] = estructura

    exposicion = _extract_section(raw, "EXPOSICION")
    if exposicion:
        matched = _match_exposure(exposicion)
        if matched and matched in EXPOSURE_TYPES:
            profile["exposure"] = matched

    orient = _extract_section(raw, "ORIENTACION_COSTA")
    if orient:
        upper = rewrite_compass_es(orient.upper().replace("**", "")) or ""
        # Preferir compuestos tipo E-SE / SE-E si aparecen
        compound = re.search(
            r"\b(N|NE|E|SE|S|SO|O|NO)\s*[-/]\s*(N|NE|E|SE|S|SO|O|NO)\b",
            upper,
        )
        if compound:
            # Usar el segundo (hacia mar abierto) o el primero; E-SE → SE
            coast_facing = compound.group(2)
        else:
            for key in _COMPASS_KEYS_LONG_FIRST:
                if re.search(rf"\b{key}\b", upper):
                    coast_facing = key
                    break

    profundidad = _extract_section(raw, "PROFUNDIDAD_APARENTE")
    if profundidad:
        norm = profundidad.replace("–", "-").replace("—", "-").replace("~", "")
        nums = re.findall(r"(\d+(?:[.,]\d+)?)", norm)
        if len(nums) >= 2:
            profile["depth_min_m"] = float(nums[0].replace(",", "."))
            profile["depth_max_m"] = float(nums[1].replace(",", "."))
        elif len(nums) == 1:
            profile["depth_min_m"] = float(nums[0].replace(",", "."))

    entradas = _extract_section(raw, "ENTRADAS")
    if entradas:
        profile["entry_notes"] = entradas

    dirs_block = _extract_section(raw, "DIRECCIONES_PESCA")
    if dirs_block:
        directions = _parse_directions(dirs_block)

    peligros = _extract_section(raw, "PELIGROS_Y_REFERENCIAS")
    if peligros:
        profile["landmarks"] = peligros

    corriente = _extract_section(raw, "CORRIENTE_LOCAL")
    if corriente:
        profile["local_current_notes"] = corriente

    resumen = _extract_section(raw, "RESUMEN")
    if resumen:
        profile["summary"] = resumen

    return {
        "profile": profile,
        "approach_directions": directions,
        "coast_facing": coast_facing,
    }


def format_direction_label(direction: dict[str, Any]) -> str:
    compass = compass_es(direction.get("compass")) or "?"
    name = COMPASS_DIRECTIONS.get(compass, compass)
    label = direction.get("label") or ""
    notes = (direction.get("notes") or "").strip()
    if label and notes:
        return f"{compass} ({name}) — {label} ({notes})"
    if label:
        return f"{compass} ({name}) — {label}"
    return f"{compass} ({name})"
