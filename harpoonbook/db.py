from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from harpoonbook.config import SEED_SPOTS


@dataclass
class FishingLog:
    fishing_time: datetime
    spot_id: str
    method_id: str
    score: int
    pulpos_seen: bool | None
    visibilidad: int | None  # 1..3 (baja/media/alta)
    notas: str
    features_snapshot: dict[str, Any]
    condition_snapshot_id: int | None = None


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
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
        _ensure_column(conn, "fishing_logs", "condition_snapshot_id", "INTEGER")
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
        conn.commit()
        seed_fishing_spots_if_empty(db_path)
    finally:
        conn.close()


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


def _row_to_spot(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["active"] = bool(d.get("active", 1))
    return d


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
                INSERT INTO fishing_spots (id, label, lat, lon, active, sort_order, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, '', ?, ?)
                """,
                (
                    spot_id,
                    str(meta["label"]),
                    float(meta["lat"]),
                    float(meta["lon"]),
                    i,
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
        query = "SELECT * FROM fishing_spots"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY sort_order, label"
        cur = conn.execute(query)
        return [_row_to_spot(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_fishing_spot(db_path: str, spot_id: str) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        cur = conn.execute("SELECT * FROM fishing_spots WHERE id = ?", (spot_id,))
        row = cur.fetchone()
        return _row_to_spot(row) if row else None
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
) -> None:
    conn = _connect(db_path)
    try:
        now = datetime.now().astimezone().isoformat()
        if sort_order is None:
            cur = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM fishing_spots")
            sort_order = int(cur.fetchone()["n"])
        conn.execute(
            """
            INSERT INTO fishing_spots (id, label, lat, lon, active, sort_order, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (spot_id, label.strip(), float(lat), float(lon), sort_order, notes.strip(), now, now),
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


def fishing_spot_label(db_path: str, spot_id: str) -> str:
    spot = get_fishing_spot(db_path, spot_id)
    return spot["label"] if spot else spot_id


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
              captured_at,
              snapshot_date,
              spot_id,
              lat,
              lon,
              solunar_json,
              windy_json,
              tide_json,
              source_status_json,
              raw_json
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
    """Fechas con al menos un snapshot guardado (más reciente primero)."""
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


def list_snapshots_for_day(
    db_path: str,
    snapshot_date: date,
    spot_id: str,
) -> list[dict[str, Any]]:
    """Todos los snapshots de un día y spot, del más antiguo al más reciente."""
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
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (snapshot_date.isoformat(), spot_id),
            )
        else:
            cur = conn.execute(
                """
                SELECT * FROM condition_snapshots
                WHERE snapshot_date = ?
                ORDER BY captured_at DESC
                LIMIT 1
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


def add_log(db_path: str, log: FishingLog) -> int:
    conn = _connect(db_path)
    try:
        created_at = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO fishing_logs (
              created_at,
              fishing_time,
              spot_id,
              method_id,
              score,
              pulpos_seen,
              visibilidad,
              notas,
              features_snapshot_json,
              condition_snapshot_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            SELECT *
            FROM fishing_logs
            ORDER BY fishing_time DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            fs = d.get("features_snapshot_json")
            if isinstance(fs, str):
                try:
                    d["features_snapshot"] = json.loads(fs)
                except json.JSONDecodeError:
                    d["features_snapshot"] = {}
            else:
                d["features_snapshot"] = {}
            d.pop("features_snapshot_json", None)
            out.append(d)
        return out
    finally:
        conn.close()


def export_logs_csv(db_path: str, csv_path: str, limit: int | None = None) -> None:
    conn = _connect(db_path)
    try:
        query = """
            SELECT id, created_at, fishing_time, spot_id, method_id, score, pulpos_seen, visibilidad, notas
            FROM fishing_logs
            ORDER BY fishing_time DESC
        """
        params: list[Any] = []
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))
        cur = conn.execute(query, params)

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "id",
                    "created_at",
                    "fishing_time",
                    "spot_id",
                    "method_id",
                    "score",
                    "pulpos_seen",
                    "visibilidad",
                    "notas",
                ]
            )
            for row in cur.fetchall():
                writer.writerow(
                    [
                        row.get("id"),
                        row.get("created_at"),
                        row.get("fishing_time"),
                        row.get("spot_id"),
                        row.get("method_id"),
                        row.get("score"),
                        row.get("pulpos_seen"),
                        row.get("visibilidad"),
                        row.get("notas"),
                    ]
                )
    finally:
        conn.close()

