"""Reproduce dual visibility calc for Cabo Roig today (post-fix)."""
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from harpoonbook.config import make_db_paths
from harpoonbook.db import get_fishing_spot, list_snapshots_for_day
from harpoonbook.merge_snapshots import merge_snapshots_for_day
from harpoonbook.visibility import (
    DEFAULT_LOOKBACK_DAYS,
    forecast_visibility,
    summarize_recent_days,
)

BASE_DIR = str(Path(__file__).resolve().parent)
DB_PATH = make_db_paths(BASE_DIR).db_path
SPOT_ID = "cabo_roig_calas"
VIEW_DATE = date(2026, 8, 13)
NOW = datetime(2026, 8, 13, 11, 20, tzinfo=ZoneInfo("Europe/Madrid"))


def main() -> None:
    spot = get_fishing_spot(DB_PATH, SPOT_ID) or {}
    snaps = list_snapshots_for_day(DB_PATH, snapshot_date=VIEW_DATE, spot_id=SPOT_ID)
    features = merge_snapshots_for_day(snaps) or {}
    recent = summarize_recent_days(
        DB_PATH,
        spot_id=SPOT_ID,
        lookback_days=DEFAULT_LOOKBACK_DAYS,
        end_date=VIEW_DATE,
    )
    vis = forecast_visibility(
        features,
        now=NOW,
        recent_context=recent,
        bottom_type=spot.get("bottom_type"),
        coast_facing=spot.get("coast_facing"),
    )
    near = vis.get("nearshore") or {}
    off = vis.get("offshore") or {}
    print("NEARSHORE", near.get("label"), near.get("meters_mid"), near.get("score"), near.get("factor"))
    print("REASONS", near.get("reasons"))
    print("OFFSHORE", off.get("secchi_m"), off.get("label"))
    print("SETTLING", recent.get("settling_factor"), "agitated", recent.get("agitated_days"))


if __name__ == "__main__":
    main()
