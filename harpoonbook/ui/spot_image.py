from __future__ import annotations

import base64
import io
from typing import Sequence

import streamlit as st

from harpoonbook.db import get_spot_map_image
from harpoonbook.ui.condition_arrows import ArrowSpec, overlay_svg

def _placeholder_png(width: int, height: int) -> bytes:
    from PIL import Image

    im = Image.new("RGB", (width, height), (176, 212, 230))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _prepare_png(
    data: bytes | None,
    max_width: int,
) -> tuple[bytes, int, int]:
    """Redimensiona el mapa (o un placeholder) y devuelve PNG + tamaño."""
    from PIL import Image

    if data:
        im = Image.open(io.BytesIO(data))
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA")
        w, h = im.size
        if w > max_width:
            ratio = max_width / float(w)
            im = im.resize((max_width, max(1, int(h * ratio))), Image.Resampling.LANCZOS)
    else:
        im = Image.new("RGB", (max_width, max_width), (176, 212, 230))

    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue(), im.size[0], im.size[1]


def _overlay_html(png: bytes, width: int, height: int, arrows: Sequence[ArrowSpec]) -> str:
    b64 = base64.b64encode(png).decode("ascii")
    svg = overlay_svg(width, height, arrows)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/></head>
<body style="margin:0;padding:0;overflow:hidden;background:transparent">
<div style="position:relative;width:{width}px;height:{height}px;line-height:0">
  <img src="data:image/png;base64,{b64}" width="{width}" height="{height}"
       style="display:block;width:{width}px;height:{height}px"/>
  {svg}
</div>
</body></html>"""


def show_spot_map_image(
    db_path: str,
    spot_id: str,
    *,
    caption: str | None = None,
    max_width: int = 320,
    arrows: Sequence[ArrowSpec] | None = None,
) -> bool:
    """Muestra el mapa del spot reducido manteniendo proporción.

    Si `arrows` no está vacío, superpone flechas Windy ancladas al centro.
    Returns True if an image (or overlay) was shown.
    """
    img = get_spot_map_image(db_path, spot_id)
    data, _mime = img if img else (None, None)

    if arrows:
        try:
            png, width, height = _prepare_png(data, max_width)
        except Exception:
            png, width, height = _placeholder_png(max_width, max_width), max_width, max_width
        st.iframe(
            _overlay_html(png, width, height, arrows),
            height=height,
            width=width,
        )
        if caption:
            st.caption(caption)
        return True

    if not data:
        return False
    try:
        png, width, _height = _prepare_png(data, max_width)
        st.image(png, caption=caption or "Mapa del spot", width=width)
    except Exception:
        st.image(data, caption=caption or "Mapa del spot", width=max_width)
    return True
