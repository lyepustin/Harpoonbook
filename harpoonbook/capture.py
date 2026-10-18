from __future__ import annotations

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from harpoonbook.config import make_db_paths, now_local
from harpoonbook.conditions import capture_all_spots
from harpoonbook.db import fishing_spot_label, init_db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Captura condiciones marinas para todos los sitios activos (Windy, marea, corrientes, optica)."
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Ruta a harpoonbook.db (por defecto: data/harpoonbook.db del proyecto)",
    )
    args = parser.parse_args(argv)

    db_path = args.db or make_db_paths(BASE_DIR).db_path
    init_db(db_path)

    now = now_local()
    results = capture_all_spots(db_path, now=now)
    if not results:
        print("No hay sitios activos para capturar.")
        return 1

    print(f"Captura completada ({now.isoformat()}) — {len(results)} sitios:")
    for spot_id, snapshot_id, _features in results:
        label = fishing_spot_label(db_path, spot_id)
        print(f"  - {label} ({spot_id}) → snapshot #{snapshot_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
