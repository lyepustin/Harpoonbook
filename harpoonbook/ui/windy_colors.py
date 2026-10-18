from __future__ import annotations

from typing import Sequence

import pandas as pd

# Paleta alineada con la tabla de previsión de windy.com (no el overlay del mapa).
# En mar tranquilo casi todo queda blanco; el color aparece de forma gradual.

# Viento / rachas (km/h). Hasta ~20 km/h sin tinte; cian suave a partir de ~30.
_WIND_TABLE_KMH: list[tuple[float, tuple[int, int, int] | None]] = [
    (0.0, None),
    (18.0, None),
    (22.0, (228, 237, 246)),
    (26.0, (205, 225, 242)),
    (30.0, (175, 215, 235)),
    (34.0, (130, 200, 218)),
    (38.0, (95, 185, 205)),
    (45.0, (70, 175, 195)),
    (55.0, (55, 165, 175)),
    (65.0, (80, 175, 130)),
    (80.0, (200, 140, 60)),
    (100.0, (200, 66, 13)),
]

# Olas / swell (m). Umbrales de percepción en costa:
#   < 0.3 plano | 0.3 suave | 0.5 ligero | 0.9 mareado
_WAVE_TABLE_M: list[tuple[float, tuple[int, int, int] | None]] = [
    (0.0, None),
    (0.299, None),
    (0.30, (248, 246, 252)),
    (0.50, (240, 236, 248)),
    (0.70, (232, 226, 244)),
    (0.90, (210, 200, 232)),
    (1.20, (188, 172, 220)),
    (1.80, (160, 140, 205)),
    (2.50, (130, 115, 195)),
    (4.00, (100, 95, 185)),
]

# Potencia de ola (kW/m). Casi imperceptible en condiciones planas.
_POWER_TABLE_KW: list[tuple[float, tuple[int, int, int] | None]] = [
    (0.0, None),
    (0.8, None),
    (1.5, (247, 245, 250)),
    (3.0, (235, 228, 244)),
    (6.0, (215, 205, 235)),
    (12.0, (185, 168, 220)),
    (25.0, (150, 130, 200)),
]

WIND_COLUMNS = ("Viento km/h", "Rachas")
WAVE_COLUMNS = ("Olas m", "Swell1 m")
POWER_COLUMNS = ("Power kW/m",)
NUMERIC_DISPLAY_COLUMNS = (
    "Temp °C",
    "Viento km/h",
    "Rachas",
    "Olas m",
    "Swell1 m",
    "Periodo s",
    "Power kW/m",
)

# Muestras para la leyenda del dashboard (sincronizadas con las escalas de arriba).
LEGEND_WIND_SWATCHES = (
    ("#ffffff", "flojo"),
    ("rgb(205, 225, 242)", "suave"),
    ("rgb(130, 200, 218)", "moderado"),
    ("rgb(55, 165, 175)", "fuerte"),
)

LEGEND_WAVE_SWATCHES = (
    ("#ffffff", "plano"),
    ("rgb(248, 246, 252)", "oleaje suave"),
    ("rgb(240, 236, 248)", "oleaje ligero"),
    ("rgb(210, 200, 232)", "mareado"),
)


def _interpolate_rgb(
    value: float,
    scale: Sequence[tuple[float, tuple[int, int, int] | None]],
) -> tuple[int, int, int] | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    v = float(value)
    if v <= scale[0][0]:
        return scale[0][1]

    for i in range(1, len(scale)):
        v0, c0 = scale[i - 1]
        v1, c1 = scale[i]
        if v <= v1:
            if c0 is None and c1 is None:
                return None
            if c0 is None:
                c0 = (255, 255, 255)
            if c1 is None:
                c1 = c0
            t = 0.0 if v1 == v0 else (v - v0) / (v1 - v0)
            return (
                int(c0[0] + (c1[0] - c0[0]) * t),
                int(c0[1] + (c1[1] - c0[1]) * t),
                int(c0[2] + (c1[2] - c0[2]) * t),
            )

    last_color = scale[-1][1]
    return last_color


def wind_bg_kmh(kmh: float | None) -> str:
    if kmh is None or (isinstance(kmh, float) and pd.isna(kmh)):
        return ""
    rgb = _interpolate_rgb(float(kmh), _WIND_TABLE_KMH)
    if rgb is None:
        return ""
    return f"background-color: rgb({rgb[0]}, {rgb[1]}, {rgb[2]})"


def wave_bg_m(meters: float | None) -> str:
    if meters is None or (isinstance(meters, float) and pd.isna(meters)):
        return ""
    rgb = _interpolate_rgb(float(meters), _WAVE_TABLE_M)
    if rgb is None:
        return ""
    return f"background-color: rgb({rgb[0]}, {rgb[1]}, {rgb[2]})"


def power_bg_kw(value: float | None) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    rgb = _interpolate_rgb(float(value), _POWER_TABLE_KW)
    if rgb is None:
        return ""
    return f"background-color: rgb({rgb[0]}, {rgb[1]}, {rgb[2]})"


def _cell_style(value, column: str) -> str:
    if column in WIND_COLUMNS:
        return wind_bg_kmh(value)
    if column in WAVE_COLUMNS:
        return wave_bg_m(value)
    if column in POWER_COLUMNS:
        return power_bg_kw(value)
    return ""


def render_windy_legend_html() -> str:
    wind_swatches = " ".join(
        f'<span style="display:inline-block;width:14px;height:14px;background:{color};'
        f'{"border:1px solid #ddd;" if color == "#ffffff" else ""}'
        f'vertical-align:middle;margin-left:{"0" if i == 0 else "6px"}"></span> {label}'
        for i, (color, label) in enumerate(LEGEND_WIND_SWATCHES)
    )
    wave_swatches = " ".join(
        f'<span style="display:inline-block;width:14px;height:14px;background:{color};'
        f'{"border:1px solid #ddd;" if color == "#ffffff" else ""}'
        f'vertical-align:middle;margin-left:{"0" if i == 0 else "6px"}"></span> {label}'
        for i, (color, label) in enumerate(LEGEND_WAVE_SWATCHES)
    )
    return f"""
        <div style="font-size:0.85rem;color:#666;margin-top:0.25rem">
          <span style="margin-right:1.2rem"><b>Viento</b>: {wind_swatches}</span>
          <span><b>Olas / swell</b>: {wave_swatches}</span>
        </div>
    """


SELECTED_HOUR_STYLE = "box-shadow: inset 4px 0 0 #1565c0;"


def apply_hour_highlight(
    styler: pd.io.formats.style.Styler,
    highlight_hour: int | None,
    hour_col: str = "Hora",
) -> pd.io.formats.style.Styler:
    """Marca la fila de la franja seleccionada (columna Hora = HH:00)."""
    if highlight_hour is None:
        return styler
    target = f"{int(highlight_hour):02d}:00"

    def _hl(row: pd.Series) -> list[str]:
        if str(row.get(hour_col, "")).strip() == target:
            return [SELECTED_HOUR_STYLE] * len(row)
        return [""] * len(row)

    return styler.apply(_hl, axis=1)


def style_hour_table(
    df: pd.DataFrame,
    highlight_hour: int | None,
    *,
    hour_col: str = "Hora",
) -> pd.io.formats.style.Styler:
    """Tabla simple (visibilidad / corrientes) con la franja activa resaltada."""
    styler = df.style.hide(axis="index")
    return apply_hour_highlight(styler, highlight_hour, hour_col=hour_col)


def style_windy_table(
    df: pd.DataFrame,
    highlight_hour: int | None = None,
) -> pd.io.formats.style.Styler:
    def _row_styles(row: pd.Series) -> list[str]:
        return [_cell_style(row[col], col) for col in row.index]

    display_format = {col: "{:.1f}" for col in NUMERIC_DISPLAY_COLUMNS if col in df.columns}

    styler = (
        df.style.apply(_row_styles, axis=1)
        .format(display_format, na_rep="—")
        .set_table_styles(
            [
                {
                    "selector": "th",
                    "props": [
                        ("font-weight", "600"),
                        ("text-align", "center"),
                        ("padding", "6px 8px"),
                    ],
                },
                {
                    "selector": "td",
                    "props": [
                        ("text-align", "center"),
                        ("padding", "6px 8px"),
                        ("color", "#111"),
                    ],
                },
            ]
        )
        .hide(axis="index")
    )
    return apply_hour_highlight(styler, highlight_hour)
