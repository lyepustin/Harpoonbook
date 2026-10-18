from __future__ import annotations

import html
import math
from dataclasses import dataclass
from typing import Any, Sequence

from harpoonbook.config import COMPASS_16_ES, compass_es


# Paleta del picker de Windy: viento cian, swell naranja, corriente (en lugar de swell 2) verde.
WIND_COLOR = "#3EC6E8"
SWELL_COLOR = "#F0A020"
CURRENT_COLOR = "#5CDB5C"
PILL_BG = "#2c2c2c"
HUB_FILL = "#ffffff"
HUB_STROKE = "#444444"


@dataclass(frozen=True)
class ArrowSpec:
    """Flecha estilo Windy. travel_deg: 0 = norte, 90 = este (hacia dónde apunta la barra)."""

    travel_deg: float
    color: str
    value_text: str
    pill_text: str


def compass_to_deg(label: str | None) -> float | None:
    """Rosa de 16 rumbos → grados (N=0, E=90)."""
    compass = compass_es(label)
    if not compass or compass not in COMPASS_16_ES:
        return None
    return COMPASS_16_ES.index(compass) * 22.5


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def _direction_deg(slot: dict[str, Any] | None, deg_key: str, label_key: str) -> float | None:
    if not slot:
        return None
    deg = _as_float(slot.get(deg_key))
    if deg is not None:
        return deg % 360.0
    return compass_to_deg(slot.get(label_key) if isinstance(slot.get(label_key), str) else None)


def _fmt_kmh(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value - round(value)) < 0.05:
        return f"{int(round(value))} km/h"
    return f"{value:.1f} km/h"


def _fmt_swell(height_m: float | None, period_s: float | None) -> str:
    if height_m is None and period_s is None:
        return "—"
    parts: list[str] = []
    if height_m is not None:
        parts.append(f"{height_m:.1f}m")
    if period_s is not None:
        period = int(round(period_s)) if abs(period_s - round(period_s)) < 0.15 else period_s
        parts.append(f"{period}s" if isinstance(period, int) else f"{period:.0f}s")
    return ", ".join(parts)


def overlay_arrows_from_slots(
    sea_slot: dict[str, Any] | None,
    current_slot: dict[str, Any] | None,
) -> list[ArrowSpec]:
    """Misma orientación que las barras del picker de Windy.

    Viento y swell: el ángulo guardado ya es «de dónde vienen» y la barra
    apunta a ese ángulo (Windy no le suma 180°).
    Corriente: Open-Meteo dice hacia dónde fluye; la barra apunta hacia allí.
    """
    arrows: list[ArrowSpec] = []

    wind_from = _direction_deg(sea_slot, "wind_direction_deg", "wind_direction")
    if wind_from is not None:
        arrows.append(
            ArrowSpec(
                travel_deg=wind_from % 360.0,
                color=WIND_COLOR,
                value_text=_fmt_kmh(_as_float((sea_slot or {}).get("wind_speed_kmh"))),
                pill_text="VIENTO",
            )
        )

    swell_from = _direction_deg(sea_slot, "swell1_direction_deg", "swell1_direction")
    swell_h = _as_float((sea_slot or {}).get("swell1_height_m"))
    swell_p = _as_float((sea_slot or {}).get("swell1_period_s"))
    if swell_from is not None and (swell_h is None or swell_h > 0):
        arrows.append(
            ArrowSpec(
                travel_deg=swell_from % 360.0,
                color=SWELL_COLOR,
                value_text=_fmt_swell(swell_h, swell_p),
                pill_text="SWELL",
            )
        )

    current_to = _direction_deg(current_slot, "current_direction_deg", "current_direction")
    current_speed = _as_float((current_slot or {}).get("current_speed_kmh"))
    if current_to is not None and current_speed is not None and current_speed > 0:
        arrows.append(
            ArrowSpec(
                travel_deg=current_to % 360.0,
                color=CURRENT_COLOR,
                value_text=_fmt_kmh(current_speed),
                pill_text="CORRIENTE",
            )
        )

    return arrows


def _arrow_layout(
    spec: ArrowSpec,
    size: float,
    *,
    length_scale: float,
    pill_along: float,
) -> dict[str, float | str]:
    """Geometría compartida: cuerpo (fondo) y etiquetas (encima)."""
    travel = spec.travel_deg % 360.0
    r_hub = max(13.0, size * 0.05)
    r_ring = r_hub + max(7.0, size * 0.028)
    r_in = r_ring + 4.0
    length = size * 0.36 * length_scale
    r_out = r_in + length
    w_in = max(18.0, size * 0.072)
    w_out = max(26.0, size * 0.10)
    font = max(9.0, size * 0.036)
    pill_font = max(7.0, size * 0.027)
    pill_w = max(56.0, size * 0.20, len(spec.pill_text) * pill_font * 0.72 + 12)
    pill_h = max(12.0, size * 0.04)
    pill_r = r_in + length * pill_along
    return {
        "travel": travel,
        "r_in": r_in,
        "r_out": r_out,
        "w_in": w_in,
        "w_out": w_out,
        "flip": 180 if 90.0 < travel < 270.0 else 0,
        "font": font,
        "pill_font": pill_font,
        "pill_w": pill_w,
        "pill_h": pill_h,
        "pill_cy": -pill_r,
        "value_cy": -(r_in + length * 0.62),
        "color": spec.color,
        "value": html.escape(spec.value_text),
        "pill": html.escape(spec.pill_text),
    }


def _arrow_body_svg(layout: dict[str, float | str]) -> str:
    travel = float(layout["travel"])
    r_in = float(layout["r_in"])
    r_out = float(layout["r_out"])
    w_in = float(layout["w_in"])
    w_out = float(layout["w_out"])
    return (
        f'<g transform="rotate({travel:.1f})">'
        f'<path d="M {-w_in/2:.1f},{-r_in:.1f} L {-w_out/2:.1f},{-r_out:.1f} '
        f"L {w_out/2:.1f},{-r_out:.1f} L {w_in/2:.1f},{-r_in:.1f} Z\" "
        f'fill="{layout["color"]}" fill-opacity="0.92" stroke="#1a1a1a" '
        f'stroke-width="0.8" stroke-opacity="0.4"/>'
        f"</g>"
    )


def _arrow_label_svg(layout: dict[str, float | str]) -> str:
    travel = float(layout["travel"])
    flip = int(layout["flip"])
    pill_w = float(layout["pill_w"])
    pill_h = float(layout["pill_h"])
    pill_cy = float(layout["pill_cy"])
    value_cy = float(layout["value_cy"])
    return (
        f'<g transform="rotate({travel:.1f})">'
        f'<rect x="{-pill_w/2:.1f}" y="{pill_cy - pill_h/2:.1f}" width="{pill_w:.1f}" '
        f'height="{pill_h:.1f}" rx="{pill_h/2:.1f}" fill="{PILL_BG}"/>'
        f'<text x="0" y="{pill_cy:.1f}" transform="rotate({flip} 0 {pill_cy:.1f})" '
        f'text-anchor="middle" dominant-baseline="middle" fill="#fff" '
        f'font-size="{float(layout["pill_font"]):.1f}" font-weight="700" '
        f'font-family="Segoe UI, Arial, sans-serif">{layout["pill"]}</text>'
        f'<text x="0" y="{value_cy:.1f}" transform="rotate({flip} 0 {value_cy:.1f})" '
        f'text-anchor="middle" dominant-baseline="middle" fill="#111" stroke="#fff" '
        f'stroke-width="2.2" paint-order="stroke" font-size="{float(layout["font"]):.1f}" '
        f'font-weight="700" font-family="Segoe UI, Arial, sans-serif">{layout["value"]}</text>'
        f"</g>"
    )


def overlay_svg(width: int, height: int, arrows: Sequence[ArrowSpec]) -> str:
    """SVG anclado al centro geométrico. Norte = arriba.

    Capas: cuerpos de flecha (fondo) → hub → pastillas y texto (siempre encima).
    """
    cx = width / 2.0
    cy = height / 2.0
    size = float(min(width, height))
    r_hub = max(13.0, size * 0.05)
    r_ring = r_hub + max(7.0, size * 0.028)
    layouts_cfg = (
        (1.0, 0.14),
        (0.84, 0.30),
        (0.70, 0.46),
    )
    drawn = [
        _arrow_layout(spec, size, length_scale=ls, pill_along=pt)
        for spec, (ls, pt) in zip(arrows, layouts_cfg)
    ]
    bodies = "\n".join(_arrow_body_svg(item) for item in drawn)
    labels = "\n".join(_arrow_label_svg(item) for item in drawn)
    n_size = max(10.0, size * 0.04)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
      viewBox="0 0 {width} {height}" style="position:absolute;left:0;top:0;pointer-events:none">
  <text x="{cx:.1f}" y="{n_size + 2:.1f}" text-anchor="middle" font-size="{n_size:.1f}"
        font-weight="700" fill="#fff" stroke="#222" stroke-width="3.5" paint-order="stroke"
        font-family="Segoe UI, Arial, sans-serif">N</text>
  <g transform="translate({cx:.1f},{cy:.1f})">
    {bodies}
    <circle r="{r_ring:.1f}" fill="none" stroke="#fff" stroke-width="1.4" opacity="0.9"/>
    <circle r="{r_hub:.1f}" fill="{HUB_FILL}" stroke="{HUB_STROKE}" stroke-width="1.6"/>
    <circle r="{max(3.0, size * 0.012):.1f}" fill="#333"/>
    {labels}
  </g>
</svg>"""
