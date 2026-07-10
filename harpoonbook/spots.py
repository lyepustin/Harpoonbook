from __future__ import annotations

import re
import unicodedata


def slugify(label: str) -> str:
    normalized = unicodedata.normalize("NFKD", label)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")
    return slug or "spot"


def is_valid_spot_id(spot_id: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{1,48}", spot_id))


def format_geocode(lat: float, lon: float) -> str:
    """Formato habitual al copiar desde Google Maps."""
    return f"{lat}, {lon}"


def parse_geocode(text: str) -> tuple[float, float] | None:
    """
    Parsea coordenadas pegadas desde Google Maps.
    Ej: 37.8235652557405, -0.7553338523683103
    """
    cleaned = text.strip().strip("()[]")
    if not cleaned:
        return None

    comma_match = re.match(
        r"^\s*(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)\s*$",
        cleaned,
    )
    if comma_match:
        return float(comma_match.group(1)), float(comma_match.group(2))

    numbers = re.findall(r"-?\d+(?:\.\d+)?", cleaned)
    if len(numbers) >= 2:
        return float(numbers[0]), float(numbers[1])

    return None


def resolve_coords(
    geocode_text: str,
    lat_fallback: float,
    lon_fallback: float,
) -> tuple[float, float] | None:
    """Usa el texto pegado si es válido; si no, los campos numéricos."""
    parsed = parse_geocode(geocode_text)
    if parsed is not None:
        return parsed
    if geocode_text.strip():
        return None
    return float(lat_fallback), float(lon_fallback)

