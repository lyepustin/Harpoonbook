"""Proceso de captura. Lo lanza la UI; no comparte el intérprete con Streamlit."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from harpoonbook.conditions import capture_all_spots, capture_and_save
from harpoonbook.db import init_db


def _lower_priority() -> None:
    if os.name != "nt":
        return
    import ctypes

    # BELOW_NORMAL_PRIORITY_CLASS: la UI se queda con la CPU.
    ctypes.windll.kernel32.SetPriorityClass(
        ctypes.windll.kernel32.GetCurrentProcess(),
        0x00004000,
    )


def _write_result(path: str, payload: dict) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Captura de condiciones en un proceso aparte.")
    parser.add_argument("--db", required=True)
    parser.add_argument("--mode", choices=("all", "one"), required=True)
    parser.add_argument("--spot", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    _lower_priority()
    try:
        init_db(args.db)
        if args.mode == "all":
            results = capture_all_spots(args.db)
            payload = {
                "status": "done",
                "results": [
                    {"spot_id": spot_id, "snapshot_id": snapshot_id}
                    for spot_id, snapshot_id, _features in results
                ],
                "error": None,
            }
        else:
            if not args.spot:
                raise ValueError("Falta --spot para la captura de un sitio.")
            _features, snapshot_id = capture_and_save(args.db, spot_id=args.spot)
            payload = {
                "status": "done",
                "snapshot_id": snapshot_id,
                "error": None,
            }
    except Exception as exc:  # noqa: BLE001 — el texto vuelve a la UI
        traceback.print_exc()
        payload = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "snapshot_id": None,
            "results": [],
        }

    _write_result(args.out, payload)
    return 0 if payload["status"] == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
