from __future__ import annotations

import io
from typing import Any


def read_clipboard_image() -> tuple[bytes, str] | None:
    """
    Lee una imagen del portapapeles del sistema (Windows Snipping Tool / Ctrl+C).
    Requiere Pillow. Devuelve (bytes, mime) o None.
    """
    try:
        from PIL import ImageGrab
    except ImportError:
        return None

    try:
        grabbed: Any = ImageGrab.grabclipboard()
    except Exception:
        return None

    if grabbed is None:
        return None

    # A veces el clipboard trae una lista de rutas de archivo
    if isinstance(grabbed, list):
        for path in grabbed:
            try:
                from PIL import Image

                img = Image.open(path)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue(), "image/png"
            except Exception:
                continue
        return None

    try:
        buf = io.BytesIO()
        grabbed.save(buf, format="PNG")
        return buf.getvalue(), "image/png"
    except Exception:
        return None
