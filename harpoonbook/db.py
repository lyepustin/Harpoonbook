from __future__ import annotations

import csv
import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from harpoonbook.config import EMPTY_SPOT_PROFILE, SEED_SPECIES, SEED_SPOTS, rewrite_compass_es


@dataclass
class FishingLog:
    """Legacy log shape (migración / compat). Prefer FishingTrip."""

    fishing_time: datetime
    spot_id: str
    method_id: str
    score: int
    pulpos_seen: bool | None
    visibilidad: int | None
    notas: str
    features_snapshot: dict[str, Any]
    condition_snapshot_id: int | None = None
    species: str | None = None


@dataclass
class TripSighting:
    species_id: str
    count_seen: int = 1
    times_seen: int = 1
    tried_to_fish: bool = False
    undersized: bool = False
    caught_count: int = 0
    avg_weight_g: float | None = None
    shot_quality: str = "n_a"
    notes: str = ""


@dataclass
class FishingTrip:
    spot_id: str
    method_id: str
    started_at: datetime
    ended_at: datetime
    score: int
    approach_direction: str | None = None
    notas: str = ""
    # Agua (efectiva)
    water_temp_c: float | None = None
    visibility_m: float | None = None
    current_speed_kmh: float | None = None
    current_direction: str | None = None
    turbidity: str = "media"
    # Agua sugerida (preset)
    water_temp_c_suggested: float | None = None
    visibility_m_suggested: float | None = None
    current_speed_kmh_suggested: float | None = None
    current_direction_suggested: str | None = None
    # Apneas
    apnea_feel: str | None = None
    apnea_avg_seconds: int | None = None
    apnea_avg_depth_m: float | None = None
    apnea_equalize: str | None = None  # keys en APNEA_EQUALIZE
    features_snapshot: dict[str, Any] = field(default_factory=dict)
    condition_snapshot_id: int | None = None
    sightings: list[TripSighting] = field(default_factory=list)


# init_db escribe el esquema. En cada rerun de Streamlit eso peleaba el
# candado con la captura y congelaba la página.
_init_done: set[str] = set()


def _connect(db_path: str) -> sqlite3.Connection:
    # WAL: la UI lee mientras el proceso de captura escribe.
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
    cur = conn.execute(f"PRAGMA table_info({table})")
    cols = {row["name"] for row in cur.fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def _parse_json_field(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (name,),
    )
    return cur.fetchone() is not None


def init_db(db_path: str) -> None:
    key = os.path.abspath(db_path)
    if key in _init_done:
        return
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fishing_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL,
              fishing_time TEXT NOT NULL,
              spot_id TEXT NOT NULL,
              method_id TEXT NOT NULL,
              score INTEGER NOT NULL,
              pulpos_seen INTEGER,
              visibilidad INTEGER,
              notas TEXT,
              features_snapshot_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fishing_time ON fishing_logs (fishing_time)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS condition_snapshots (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              captured_at TEXT NOT NULL,
              snapshot_date TEXT NOT NULL,
              spot_id TEXT NOT NULL,
              lat REAL NOT NULL,
              lon REAL NOT NULL,
              solunar_json TEXT,
              windy_json TEXT,
              tide_json TEXT,
              source_status_json TEXT,
              raw_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_condition_snapshots_date ON condition_snapshots (snapshot_date, spot_id, captured_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fishing_spots (
              id TEXT PRIMARY KEY,
              label TEXT NOT NULL,
              lat REAL NOT NULL,
              lon REAL NOT NULL,
              active INTEGER NOT NULL DEFAULT 1,
              sort_order INTEGER NOT NULL DEFAULT 0,
              notes TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fishing_spots_active ON fishing_spots (active, sort_order, label)"
        )

        _ensure_column(conn, "fishing_logs", "condition_snapshot_id", "INTEGER")
        _ensure_column(conn, "fishing_logs", "species", "TEXT")
        _ensure_column(conn, "fishing_spots", "bottom_type", "TEXT")
        _ensure_column(conn, "fishing_spots", "map_image_blob", "BLOB")
        _ensure_column(conn, "fishing_spots", "map_image_mime", "TEXT")
        _ensure_column(conn, "fishing_spots", "profile_json", "TEXT")
        _ensure_column(conn, "fishing_spots", "approach_directions_json", "TEXT")
        _ensure_column(conn, "fishing_spots", "coast_facing", "TEXT")
        _ensure_column(conn, "fishing_spots", "chatgpt_response", "TEXT")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS species (
              id TEXT PRIMARY KEY,
              label TEXT NOT NULL,
              category TEXT NOT NULL DEFAULT 'otro',
              active INTEGER NOT NULL DEFAULT 1,
              notes TEXT,
              sort_order INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_species_active ON species (active, sort_order, label)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fishing_trips (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              spot_id TEXT NOT NULL,
              method_id TEXT NOT NULL,
              started_at TEXT NOT NULL,
              ended_at TEXT NOT NULL,
              score INTEGER NOT NULL,
              approach_direction TEXT,
              notas TEXT,
              water_temp_c REAL,
              visibility_m REAL,
              current_speed_kmh REAL,
              current_direction TEXT,
              turbidity TEXT,
              water_temp_c_suggested REAL,
              visibility_m_suggested REAL,
              current_speed_kmh_suggested REAL,
              current_direction_suggested TEXT,
              apnea_feel TEXT,
              apnea_avg_seconds INTEGER,
              apnea_avg_depth_m REAL,
              apnea_hard_to_equalize INTEGER,
              apnea_equalize TEXT,
              features_snapshot_json TEXT,
              condition_snapshot_id INTEGER,
              legacy_log_id INTEGER
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fishing_trips_started ON fishing_trips (started_at DESC)"
        )
        _ensure_column(conn, "fishing_trips", "apnea_equalize", "TEXT")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trip_sightings (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              trip_id INTEGER NOT NULL,
              species_id TEXT NOT NULL,
              count_seen INTEGER NOT NULL DEFAULT 1,
              times_seen INTEGER NOT NULL DEFAULT 1,
              tried_to_fish INTEGER NOT NULL DEFAULT 0,
              undersized INTEGER NOT NULL DEFAULT 0,
              caught_count INTEGER NOT NULL DEFAULT 0,
              avg_weight_g REAL,
              shot_quality TEXT,
              notes TEXT,
              FOREIGN KEY (trip_id) REFERENCES fishing_trips(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trip_sightings_trip ON trip_sightings (trip_id)"
        )
        _ensure_column(conn, "trip_sightings", "undersized", "INTEGER NOT NULL DEFAULT 0")

        conn.commit()
    finally:
        conn.close()

    seed_fishing_spots_if_empty(db_path)
    seed_species_if_empty(db_path)
    migrate_legacy_logs_to_trips(db_path)
    _init_done.add(key)


# ---------------------------------------------------------------------------
# Spots
# ---------------------------------------------------------------------------


def _row_to_spot(row: sqlite3.Row, *, include_map_blob: bool = False) -> dict[str, Any]:
    d = dict(row)
    d["active"] = bool(d.get("active", 1))
    d["profile"] = _parse_json_field(d.pop("profile_json", None)) or dict(EMPTY_SPOT_PROFILE)
    d["approach_directions"] = _parse_json_field(d.pop("approach_directions_json", None)) or []
    has_map = bool(d.get("map_image_blob")) or bool(d.get("map_image_mime"))
    # When selecting without blob column, map_image_blob may be absent
    if "map_image_blob" in d:
        has_map = d["map_image_blob"] is not None
        if not include_map_blob:
            d.pop("map_image_blob", None)
    d["has_map_image"] = has_map
    return d


_SPOT_LIST_COLS = (
    "id, label, lat, lon, active, sort_order, notes, bottom_type, coast_facing, "
    "map_image_mime, profile_json, approach_directions_json, chatgpt_response, "
    "created_at, updated_at, "
    "(map_image_blob IS NOT NULL) AS has_map_blob"
)


def seed_fishing_spots_if_empty(db_path: str) -> None:
    conn = _connect(db_path)
    try:
        cur = conn.execute("SELECT COUNT(*) AS n FROM fishing_spots")
        if int(cur.fetchone()["n"]) > 0:
            return
        now = datetime.now().astimezone().isoformat()
        for i, (spot_id, meta) in enumerate(SEED_SPOTS.items()):
            conn.execute(
                """
                INSERT INTO fishing_spots (
                  id, label, lat, lon, active, sort_order, notes, bottom_type,
                  profile_json, approach_directions_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 1, ?, '', 'mixto', ?, '[]', ?, ?)
                """,
                (
                    spot_id,
                    str(meta["label"]),
                    float(meta["lat"]),
                    float(meta["lon"]),
                    i,
                    json.dumps(EMPTY_SPOT_PROFILE, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def list_fishing_spots(db_path: str, *, active_only: bool = True) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        query = f"SELECT {_SPOT_LIST_COLS} FROM fishing_spots"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY label COLLATE NOCASE"
        cur = conn.execute(query)
        out = []
        for r in cur.fetchall():
            spot = _row_to_spot(r)
            spot["has_map_image"] = bool(r["has_map_blob"])
            out.append(spot)
        return out
    finally:
        conn.close()


def get_fishing_spot(db_path: str, spot_id: str, *, include_map_blob: bool = False) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        if include_map_blob:
            cur = conn.execute("SELECT * FROM fishing_spots WHERE id = ?", (spot_id,))
        else:
            cur = conn.execute(
                f"SELECT {_SPOT_LIST_COLS} FROM fishing_spots WHERE id = ?",
                (spot_id,),
            )
        row = cur.fetchone()
        if not row:
            return None
        spot = _row_to_spot(row, include_map_blob=include_map_blob)
        if not include_map_blob and "has_map_blob" in row.keys():
            spot["has_map_image"] = bool(row["has_map_blob"])
        return spot
    finally:
        conn.close()


def get_spot_map_image(db_path: str, spot_id: str) -> tuple[bytes, str] | None:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "SELECT map_image_blob, map_image_mime FROM fishing_spots WHERE id = ?",
            (spot_id,),
        )
        row = cur.fetchone()
        if not row or row["map_image_blob"] is None:
            return None
        return bytes(row["map_image_blob"]), row["map_image_mime"] or "image/png"
    finally:
        conn.close()


def create_fishing_spot(
    db_path: str,
    spot_id: str,
    label: str,
    lat: float,
    lon: float,
    *,
    notes: str = "",
    sort_order: int | None = None,
    bottom_type: str = "mixto",
    profile: dict[str, Any] | None = None,
    approach_directions: list[dict[str, Any]] | None = None,
    coast_facing: str | None = None,
    map_image_blob: bytes | None = None,
    map_image_mime: str | None = None,
    chatgpt_response: str | None = None,
) -> None:
    conn = _connect(db_path)
    try:
        now = datetime.now().astimezone().isoformat()
        if sort_order is None:
            cur = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM fishing_spots")
            sort_order = int(cur.fetchone()["n"])
        profile_data = dict(EMPTY_SPOT_PROFILE)
        if profile:
            profile_data.update(profile)
        profile_data["bottom_type"] = bottom_type
        conn.execute(
            """
            INSERT INTO fishing_spots (
              id, label, lat, lon, active, sort_order, notes, bottom_type,
              profile_json, approach_directions_json, coast_facing,
              map_image_blob, map_image_mime, chatgpt_response, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                spot_id,
                label.strip(),
                float(lat),
                float(lon),
                sort_order,
                notes.strip(),
                bottom_type,
                json.dumps(profile_data, ensure_ascii=False),
                json.dumps(approach_directions or [], ensure_ascii=False),
                coast_facing,
                map_image_blob,
                map_image_mime,
                chatgpt_response,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def update_fishing_spot(
    db_path: str,
    spot_id: str,
    *,
    label: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    notes: str | None = None,
    sort_order: int | None = None,
    active: bool | None = None,
    bottom_type: str | None = None,
    profile: dict[str, Any] | None = None,
    approach_directions: list[dict[str, Any]] | None = None,
    coast_facing: str | None = None,
    map_image_blob: bytes | None = None,
    map_image_mime: str | None = None,
    clear_map_image: bool = False,
    chatgpt_response: str | None = None,
) -> bool:
    conn = _connect(db_path)
    try:
        fields: list[str] = []
        params: list[Any] = []
        if label is not None:
            fields.append("label = ?")
            params.append(label.strip())
        if lat is not None:
            fields.append("lat = ?")
            params.append(float(lat))
        if lon is not None:
            fields.append("lon = ?")
            params.append(float(lon))
        if notes is not None:
            fields.append("notes = ?")
            params.append(notes.strip())
        if sort_order is not None:
            fields.append("sort_order = ?")
            params.append(int(sort_order))
        if active is not None:
            fields.append("active = ?")
            params.append(int(bool(active)))
        if bottom_type is not None:
            fields.append("bottom_type = ?")
            params.append(bottom_type.strip())
        if profile is not None:
            fields.append("profile_json = ?")
            params.append(json.dumps(profile, ensure_ascii=False))
        if approach_directions is not None:
            fields.append("approach_directions_json = ?")
            params.append(json.dumps(approach_directions, ensure_ascii=False))
        if coast_facing is not None:
            fields.append("coast_facing = ?")
            params.append(coast_facing.strip() or None)
        if chatgpt_response is not None:
            fields.append("chatgpt_response = ?")
            params.append(chatgpt_response)
        if clear_map_image:
            fields.append("map_image_blob = NULL")
            fields.append("map_image_mime = NULL")
        elif map_image_blob is not None:
            fields.append("map_image_blob = ?")
            params.append(map_image_blob)
            fields.append("map_image_mime = ?")
            params.append(map_image_mime or "image/png")
        if not fields:
            return False
        fields.append("updated_at = ?")
        params.append(datetime.now().astimezone().isoformat())
        params.append(spot_id)
        cur = conn.execute(
            f"UPDATE fishing_spots SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def deactivate_fishing_spot(db_path: str, spot_id: str) -> bool:
    return update_fishing_spot(db_path, spot_id, active=False)


def delete_fishing_spot(db_path: str, spot_id: str) -> bool:
    """
    Borra el sitio y sus condition_snapshots.
    Las salidas (fishing_trips) se conservan en historial con el spot_id huérfano.
    """
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM condition_snapshots WHERE spot_id = ?", (spot_id,))
        cur = conn.execute("DELETE FROM fishing_spots WHERE id = ?", (spot_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def fishing_spot_label(db_path: str, spot_id: str) -> str:
    spot = get_fishing_spot(db_path, spot_id)
    return spot["label"] if spot else spot_id


# ---------------------------------------------------------------------------
# Species
# ---------------------------------------------------------------------------


def seed_species_if_empty(db_path: str) -> None:
    conn = _connect(db_path)
    try:
        cur = conn.execute("SELECT COUNT(*) AS n FROM species")
        if int(cur.fetchone()["n"]) > 0:
            return
        now = datetime.now().astimezone().isoformat()
        for i, sp in enumerate(SEED_SPECIES):
            conn.execute(
                """
                INSERT INTO species (id, label, category, active, notes, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, 1, '', ?, ?, ?)
                """,
                (sp["id"], sp["label"], sp["category"], i, now, now),
            )
        conn.commit()
    finally:
        conn.close()


def list_species(db_path: str, *, active_only: bool = True) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        query = "SELECT * FROM species"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY label COLLATE NOCASE"
        cur = conn.execute(query)
        return [{**dict(r), "active": bool(r["active"])} for r in cur.fetchall()]
    finally:
        conn.close()


def get_species(db_path: str, species_id: str) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        cur = conn.execute("SELECT * FROM species WHERE id = ?", (species_id,))
        row = cur.fetchone()
        if not row:
            return None
        return {**dict(row), "active": bool(row["active"])}
    finally:
        conn.close()


def create_species(
    db_path: str,
    species_id: str,
    label: str,
    category: str = "otro",
    *,
    notes: str = "",
    sort_order: int | None = None,
) -> None:
    conn = _connect(db_path)
    try:
        now = datetime.now().astimezone().isoformat()
        if sort_order is None:
            cur = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM species")
            sort_order = int(cur.fetchone()["n"])
        conn.execute(
            """
            INSERT INTO species (id, label, category, active, notes, sort_order, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (species_id, label.strip(), category, notes.strip(), sort_order, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def update_species(
    db_path: str,
    species_id: str,
    *,
    label: str | None = None,
    category: str | None = None,
    notes: str | None = None,
    sort_order: int | None = None,
    active: bool | None = None,
) -> bool:
    conn = _connect(db_path)
    try:
        fields: list[str] = []
        params: list[Any] = []
        if label is not None:
            fields.append("label = ?")
            params.append(label.strip())
        if category is not None:
            fields.append("category = ?")
            params.append(category)
        if notes is not None:
            fields.append("notes = ?")
            params.append(notes.strip())
        if sort_order is not None:
            fields.append("sort_order = ?")
            params.append(int(sort_order))
        if active is not None:
            fields.append("active = ?")
            params.append(int(bool(active)))
        if not fields:
            return False
        fields.append("updated_at = ?")
        params.append(datetime.now().astimezone().isoformat())
        params.append(species_id)
        cur = conn.execute(f"UPDATE species SET {', '.join(fields)} WHERE id = ?", params)
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def deactivate_species(db_path: str, species_id: str) -> bool:
    return update_species(db_path, species_id, active=False)


# ---------------------------------------------------------------------------
# Condition snapshots (unchanged API)
# ---------------------------------------------------------------------------


def _row_to_snapshot(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["solunar"] = _parse_json_field(d.pop("solunar_json", None))
    d["windy"] = _parse_json_field(d.pop("windy_json", None))
    d["tide"] = _parse_json_field(d.pop("tide_json", None))
    d["source_status"] = _parse_json_field(d.pop("source_status_json", None))
    d["raw"] = _parse_json_field(d.pop("raw_json", None)) or {}
    return d


def save_condition_snapshot(
    db_path: str,
    snapshot_date: date,
    spot_id: str,
    lat: float,
    lon: float,
    solunar: dict[str, Any] | None,
    windy: dict[str, Any] | None,
    tide: dict[str, Any] | None,
    source_status: dict[str, Any],
    raw: dict[str, Any],
    captured_at: datetime | None = None,
) -> int:
    conn = _connect(db_path)
    try:
        captured_at = captured_at or datetime.now().astimezone()
        conn.execute(
            """
            INSERT INTO condition_snapshots (
              captured_at, snapshot_date, spot_id, lat, lon,
              solunar_json, windy_json, tide_json, source_status_json, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                captured_at.isoformat(),
                snapshot_date.isoformat(),
                spot_id,
                lat,
                lon,
                json.dumps(solunar or {}, ensure_ascii=False),
                json.dumps(windy or {}, ensure_ascii=False),
                json.dumps(tide or {}, ensure_ascii=False),
                json.dumps(source_status or {}, ensure_ascii=False),
                json.dumps(raw or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        cur = conn.execute("SELECT last_insert_rowid() AS id")
        return int(cur.fetchone()["id"])
    finally:
        conn.close()


def list_snapshot_dates(db_path: str, spot_id: str | None = None) -> list[date]:
    conn = _connect(db_path)
    try:
        query = "SELECT DISTINCT snapshot_date FROM condition_snapshots WHERE 1=1"
        params: list[Any] = []
        if spot_id:
            query += " AND spot_id = ?"
            params.append(spot_id)
        query += " ORDER BY snapshot_date DESC"
        cur = conn.execute(query, params)
        return [date.fromisoformat(row["snapshot_date"]) for row in cur.fetchall()]
    finally:
        conn.close()


def count_capture_rounds_on_day(
    db_path: str,
    snapshot_date: date,
    *,
    captured_on: date | None = None,
) -> int:
    """Rondas reales ese día (mismo captured_at = una ronda multi-sitio).

    Ignora previsiones guardadas en días previos (captured_at de otra fecha).
    """
    captured_on = captured_on or snapshot_date
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT COUNT(DISTINCT captured_at) AS n
            FROM condition_snapshots
            WHERE snapshot_date = ?
              AND substr(captured_at, 1, 10) = ?
            """,
            (snapshot_date.isoformat(), captured_on.isoformat()),
        )
        row = cur.fetchone()
        return int(row["n"] if row else 0)
    finally:
        conn.close()


def list_snapshots_for_day(
    db_path: str,
    snapshot_date: date,
    spot_id: str,
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT * FROM condition_snapshots
            WHERE snapshot_date = ? AND spot_id = ?
            ORDER BY captured_at ASC
            """,
            (snapshot_date.isoformat(), spot_id),
        )
        return [_row_to_snapshot(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_latest_snapshot(
    db_path: str,
    snapshot_date: date,
    spot_id: str | None = None,
) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        if spot_id:
            cur = conn.execute(
                """
                SELECT * FROM condition_snapshots
                WHERE snapshot_date = ? AND spot_id = ?
                ORDER BY captured_at DESC LIMIT 1
                """,
                (snapshot_date.isoformat(), spot_id),
            )
        else:
            cur = conn.execute(
                """
                SELECT * FROM condition_snapshots
                WHERE snapshot_date = ?
                ORDER BY captured_at DESC LIMIT 1
                """,
                (snapshot_date.isoformat(),),
            )
        row = cur.fetchone()
        return _row_to_snapshot(row) if row else None
    finally:
        conn.close()


def list_condition_snapshots(
    db_path: str,
    snapshot_date: date | None = None,
    spot_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        query = "SELECT * FROM condition_snapshots WHERE 1=1"
        params: list[Any] = []
        if snapshot_date:
            query += " AND snapshot_date = ?"
            params.append(snapshot_date.isoformat())
        if spot_id:
            query += " AND spot_id = ?"
            params.append(spot_id)
        query += " ORDER BY captured_at DESC LIMIT ?"
        params.append(limit)
        cur = conn.execute(query, params)
        return [_row_to_snapshot(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Trips + sightings
# ---------------------------------------------------------------------------


def _normalize_apnea_equalize(d: dict[str, Any]) -> str | None:
    """Prefer apnea_equalize TEXT; fallback desde el bool legacy."""
    raw = d.get("apnea_equalize")
    if raw:
        return str(raw)
    legacy = d.get("apnea_hard_to_equalize")
    if legacy is None:
        return None
    if isinstance(legacy, bool):
        return "si_otro" if legacy else "no"
    try:
        return "si_otro" if int(legacy) else "no"
    except (TypeError, ValueError):
        return None


def _row_to_trip(row: sqlite3.Row, sightings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    d = dict(row)
    fs = d.pop("features_snapshot_json", None)
    d["features_snapshot"] = _parse_json_field(fs) or {}
    d["apnea_equalize"] = _normalize_apnea_equalize(d)
    # Compat: bool derivado (True si costó compensar).
    eq = d["apnea_equalize"]
    d["apnea_hard_to_equalize"] = None if eq is None else (eq != "no")
    d["sightings"] = sightings if sightings is not None else []
    return d


def _list_sightings_for_trip(conn: sqlite3.Connection, trip_id: int) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM trip_sightings WHERE trip_id = ? ORDER BY id ASC",
        (trip_id,),
    )
    out = []
    for r in cur.fetchall():
        d = dict(r)
        d["tried_to_fish"] = bool(d.get("tried_to_fish"))
        d["undersized"] = bool(d.get("undersized"))
        out.append(d)
    return out


def _insert_sightings(conn: sqlite3.Connection, trip_id: int, sightings: list[TripSighting]) -> None:
    for s in sightings:
        if not s.species_id:
            continue
        conn.execute(
            """
            INSERT INTO trip_sightings (
              trip_id, species_id, count_seen, times_seen, tried_to_fish,
              undersized, caught_count, avg_weight_g, shot_quality, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trip_id,
                s.species_id,
                int(s.count_seen),
                int(s.times_seen),
                int(bool(s.tried_to_fish)),
                int(bool(s.undersized)),
                int(s.caught_count),
                s.avg_weight_g,
                s.shot_quality,
                s.notes,
            ),
        )


def add_trip(db_path: str, trip: FishingTrip, *, legacy_log_id: int | None = None) -> int:
    conn = _connect(db_path)
    try:
        now = datetime.now().astimezone().isoformat()
        eq = trip.apnea_equalize
        eq_legacy = None if not eq else int(eq != "no")
        conn.execute(
            """
            INSERT INTO fishing_trips (
              created_at, updated_at, spot_id, method_id, started_at, ended_at, score,
              approach_direction, notas,
              water_temp_c, visibility_m, current_speed_kmh, current_direction, turbidity,
              water_temp_c_suggested, visibility_m_suggested,
              current_speed_kmh_suggested, current_direction_suggested,
              apnea_feel, apnea_avg_seconds, apnea_avg_depth_m, apnea_hard_to_equalize,
              apnea_equalize,
              features_snapshot_json, condition_snapshot_id, legacy_log_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                now,
                trip.spot_id,
                trip.method_id,
                trip.started_at.isoformat(),
                trip.ended_at.isoformat(),
                int(trip.score),
                trip.approach_direction,
                trip.notas,
                trip.water_temp_c,
                trip.visibility_m,
                trip.current_speed_kmh,
                trip.current_direction,
                trip.turbidity,
                trip.water_temp_c_suggested,
                trip.visibility_m_suggested,
                trip.current_speed_kmh_suggested,
                trip.current_direction_suggested,
                trip.apnea_feel,
                trip.apnea_avg_seconds,
                trip.apnea_avg_depth_m,
                eq_legacy,
                eq,
                json.dumps(trip.features_snapshot or {}, ensure_ascii=False),
                trip.condition_snapshot_id,
                legacy_log_id,
            ),
        )
        trip_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
        _insert_sightings(conn, trip_id, trip.sightings)
        conn.commit()
        return trip_id
    finally:
        conn.close()


def update_trip(db_path: str, trip_id: int, trip: FishingTrip) -> bool:
    conn = _connect(db_path)
    try:
        now = datetime.now().astimezone().isoformat()
        eq = trip.apnea_equalize
        eq_legacy = None if not eq else int(eq != "no")
        cur = conn.execute(
            """
            UPDATE fishing_trips SET
              updated_at = ?,
              spot_id = ?, method_id = ?, started_at = ?, ended_at = ?, score = ?,
              approach_direction = ?, notas = ?,
              water_temp_c = ?, visibility_m = ?, current_speed_kmh = ?,
              current_direction = ?, turbidity = ?,
              water_temp_c_suggested = ?, visibility_m_suggested = ?,
              current_speed_kmh_suggested = ?, current_direction_suggested = ?,
              apnea_feel = ?, apnea_avg_seconds = ?, apnea_avg_depth_m = ?,
              apnea_hard_to_equalize = ?, apnea_equalize = ?,
              features_snapshot_json = ?, condition_snapshot_id = ?
            WHERE id = ?
            """,
            (
                now,
                trip.spot_id,
                trip.method_id,
                trip.started_at.isoformat(),
                trip.ended_at.isoformat(),
                int(trip.score),
                trip.approach_direction,
                trip.notas,
                trip.water_temp_c,
                trip.visibility_m,
                trip.current_speed_kmh,
                trip.current_direction,
                trip.turbidity,
                trip.water_temp_c_suggested,
                trip.visibility_m_suggested,
                trip.current_speed_kmh_suggested,
                trip.current_direction_suggested,
                trip.apnea_feel,
                trip.apnea_avg_seconds,
                trip.apnea_avg_depth_m,
                eq_legacy,
                eq,
                json.dumps(trip.features_snapshot or {}, ensure_ascii=False),
                trip.condition_snapshot_id,
                trip_id,
            ),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return False
        conn.execute("DELETE FROM trip_sightings WHERE trip_id = ?", (trip_id,))
        _insert_sightings(conn, trip_id, trip.sightings)
        conn.commit()
        return True
    finally:
        conn.close()


def get_trip(db_path: str, trip_id: int) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        cur = conn.execute("SELECT * FROM fishing_trips WHERE id = ?", (trip_id,))
        row = cur.fetchone()
        if not row:
            return None
        return _row_to_trip(row, _list_sightings_for_trip(conn, trip_id))
    finally:
        conn.close()


def list_trips(db_path: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT * FROM fishing_trips
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return [_row_to_trip(r, _list_sightings_for_trip(conn, int(r["id"]))) for r in rows]
    finally:
        conn.close()


def migrate_legacy_logs_to_trips(db_path: str) -> int:
    """Migra fishing_logs aún no convertidos. Idempotente vía legacy_log_id."""
    conn = _connect(db_path)
    migrated = 0
    try:
        if not _table_exists(conn, "fishing_logs"):
            return 0
        cur = conn.execute("SELECT * FROM fishing_logs ORDER BY id ASC")
        logs = cur.fetchall()
        done = {
            int(r["legacy_log_id"])
            for r in conn.execute(
                "SELECT legacy_log_id FROM fishing_trips WHERE legacy_log_id IS NOT NULL"
            ).fetchall()
        }
        now = datetime.now().astimezone().isoformat()
        for log in logs:
            log_id = int(log["id"])
            if log_id in done:
                continue
            fishing_time = log["fishing_time"]
            features = _parse_json_field(log["features_snapshot_json"]) or {}
            conn.execute(
                """
                INSERT INTO fishing_trips (
                  created_at, updated_at, spot_id, method_id, started_at, ended_at, score,
                  approach_direction, notas,
                  water_temp_c, visibility_m, current_speed_kmh, current_direction, turbidity,
                  water_temp_c_suggested, visibility_m_suggested,
                  current_speed_kmh_suggested, current_direction_suggested,
                  apnea_feel, apnea_avg_seconds, apnea_avg_depth_m, apnea_hard_to_equalize,
                  features_snapshot_json, condition_snapshot_id, legacy_log_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, NULL, NULL, 'media',
                        NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, ?)
                """,
                (
                    log["created_at"] or now,
                    now,
                    log["spot_id"],
                    log["method_id"],
                    fishing_time,
                    fishing_time,
                    int(log["score"]),
                    log["notas"] or "",
                    log["features_snapshot_json"] or "{}",
                    log["condition_snapshot_id"] if "condition_snapshot_id" in log.keys() else None,
                    log_id,
                ),
            )
            trip_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
            species = log["species"] if "species" in log.keys() else None
            pulpos = log["pulpos_seen"] if "pulpos_seen" in log.keys() else None
            if species:
                sid = str(species).strip().lower().replace("ó", "o").replace(" ", "_")
                # Map common labels to seed ids
                alias = {
                    "dentón": "denton",
                    "denton": "denton",
                    "pulpo": "pulpo",
                    "mero": "mero",
                    "serviola": "serviola",
                    "lubina": "lubina",
                    "sargo": "sargo",
                    "otro": "otro",
                }
                sid = alias.get(str(species).strip().lower(), sid)
                exists = conn.execute("SELECT 1 FROM species WHERE id = ?", (sid,)).fetchone()
                if not exists:
                    sid = "otro"
                conn.execute(
                    """
                    INSERT INTO trip_sightings (
                      trip_id, species_id, count_seen, times_seen, tried_to_fish,
                      caught_count, avg_weight_g, shot_quality, notes
                    )
                    VALUES (?, ?, 1, 1, 0, 0, NULL, 'n_a', '')
                    """,
                    (trip_id, sid),
                )
            elif pulpos is not None and int(pulpos) == 1:
                conn.execute(
                    """
                    INSERT INTO trip_sightings (
                      trip_id, species_id, count_seen, times_seen, tried_to_fish,
                      caught_count, avg_weight_g, shot_quality, notes
                    )
                    VALUES (?, 'pulpo', 1, 1, 0, 0, NULL, 'n_a', 'migrado desde pulpos_seen')
                    """,
                    (trip_id,),
                )
            # Visibility legacy 1/2/3 → approximate meters
            vis = log["visibilidad"] if "visibilidad" in log.keys() else None
            if vis is not None:
                vis_m = {1: 3.0, 2: 6.0, 3: 12.0}.get(int(vis))
                if vis_m is not None:
                    conn.execute(
                        "UPDATE fishing_trips SET visibility_m = ? WHERE id = ?",
                        (vis_m, trip_id),
                    )
            migrated += 1
        conn.commit()
        return migrated
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Legacy logs API (compat)
# ---------------------------------------------------------------------------


def add_log(db_path: str, log: FishingLog) -> int:
    conn = _connect(db_path)
    try:
        created_at = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO fishing_logs (
              created_at, fishing_time, spot_id, method_id, score,
              pulpos_seen, visibilidad, notas, features_snapshot_json,
              condition_snapshot_id, species
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                log.fishing_time.isoformat(),
                log.spot_id,
                log.method_id,
                int(log.score),
                None if log.pulpos_seen is None else int(bool(log.pulpos_seen)),
                log.visibilidad,
                log.notas,
                json.dumps(log.features_snapshot or {}, ensure_ascii=False),
                log.condition_snapshot_id,
                log.species,
            ),
        )
        conn.commit()
        cur = conn.execute("SELECT last_insert_rowid() AS id")
        return int(cur.fetchone()["id"])
    finally:
        conn.close()


def list_logs(db_path: str, limit: int = 50) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT * FROM fishing_logs
            ORDER BY fishing_time DESC LIMIT ?
            """,
            (limit,),
        )
        out: list[dict[str, Any]] = []
        for r in cur.fetchall():
            d = dict(r)
            fs = d.get("features_snapshot_json")
            d["features_snapshot"] = _parse_json_field(fs) if isinstance(fs, str) else {}
            d.pop("features_snapshot_json", None)
            out.append(d)
        return out
    finally:
        conn.close()


def export_trips_csv(db_path: str, csv_path: str, limit: int | None = None) -> None:
    trips = list_trips(db_path, limit=limit or 500)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "id",
                "started_at",
                "ended_at",
                "spot_id",
                "method_id",
                "score",
                "approach_direction",
                "visibility_m",
                "turbidity",
                "water_temp_c",
                "current_speed_kmh",
                "current_direction",
                "apnea_feel",
                "apnea_avg_seconds",
                "apnea_avg_depth_m",
                "apnea_equalize",
                "sightings",
                "notas",
            ]
        )
        for t in trips:
            sightings_txt = "; ".join(
                f"{s['species_id']}x{s['count_seen']}"
                + (f" caught={s['caught_count']}" if s.get("caught_count") else "")
                for s in t.get("sightings") or []
            )
            writer.writerow(
                [
                    t.get("id"),
                    t.get("started_at"),
                    t.get("ended_at"),
                    t.get("spot_id"),
                    t.get("method_id"),
                    t.get("score"),
                    t.get("approach_direction"),
                    t.get("visibility_m"),
                    t.get("turbidity"),
                    t.get("water_temp_c"),
                    t.get("current_speed_kmh"),
                    rewrite_compass_es(t.get("current_direction")),
                    t.get("apnea_feel"),
                    t.get("apnea_avg_seconds"),
                    t.get("apnea_avg_depth_m"),
                    t.get("apnea_equalize"),
                    sightings_txt,
                    t.get("notas"),
                ]
            )


def export_logs_csv(db_path: str, csv_path: str, limit: int | None = None) -> None:
    """Compat: exporta trips (nuevo modelo)."""
    export_trips_csv(db_path, csv_path, limit=limit)
