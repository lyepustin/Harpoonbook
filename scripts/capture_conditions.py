#!/usr/bin/env python3
"""Captura condiciones (solunar, windy, marea) y las guarda en SQLite."""

from __future__ import annotations

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from harpoonbook.config import make_db_paths, now_local
from harpoonbook.conditions import capture_all_spots, capture_and_save
from harpoonbook.db import get_fishing_spot, init_db, list_fishing_spots


def main() -> int:
    parser = argparse.ArgumentParser(description="Captura y guarda condiciones de pesca en SQLite")
    parser.add_argument(
        "--spot",
        default=None,
        help="ID del spot a capturar (de la tabla fishing_spots)",
    )
    parser.add_argument(
        "--all-spots",
        action="store_true",
        help="Captura todos los spots activos (Windy por coords; solunar una vez)",
    )
    args = parser.parse_args()

    db_paths = make_db_paths(BASE_DIR)
    init_db(db_paths.db_path)

    now = now_local()
    print(f"Capturando condiciones ({now.isoformat()})...")

    if args.all_spots or not args.spot:
        results = capture_all_spots(db_paths.db_path, now=now)
        for spot_id, snapshot_id, features in results:
            windy_slots = len((features.get("windy") or {}).get("hourly", []))
            print(
                f"  [{spot_id}] snapshot_id={snapshot_id} "
                f"solunar={'ok' if features.get('solunar') else 'fail'} "
                f"windy_slots={windy_slots} "
                f"coords={features.get('lat')},{features.get('lon')}"
            )
        if not results:
            print("  (sin spots activos)")
    else:
        spot = get_fishing_spot(db_paths.db_path, args.spot)
        if not spot:
            active = [s["id"] for s in list_fishing_spots(db_paths.db_path)]
            print(f"Spot desconocido: {args.spot}. Activos: {', '.join(active) or 'ninguno'}")
            return 1
        features, snapshot_id = capture_and_save(
            db_paths.db_path,
            spot_id=spot["id"],
            lat=float(spot["lat"]),
            lon=float(spot["lon"]),
            now=now,
        )
        windy_slots = len((features.get("windy") or {}).get("hourly", []))
        print(
            f"  [{spot['id']}] snapshot_id={snapshot_id} "
            f"solunar={'ok' if features.get('solunar') else 'fail'} "
            f"windy_slots={windy_slots}"
        )

    print("Hecho.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
