"""Captura de condiciones en otro proceso (no bloquea el script de Streamlit)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALL_SPOTS_ID = "__all__"
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class _JobPaths:
    pid: Path
    result: Path
    log: Path


def _jobs_dir() -> Path:
    path = Path(tempfile.gettempdir()) / "harpoonbook-capture"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _paths(db_path: str, spot_id: str) -> _JobPaths:
    digest = hashlib.sha1(
        f"{os.path.abspath(db_path)}::{spot_id}".encode()
    ).hexdigest()[:20]
    root = _jobs_dir()
    return _JobPaths(
        pid=root / f"{digest}.pid.json",
        result=root / f"{digest}.result.json",
        log=root / f"{digest}.log",
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        # OpenProcess solo consulta. os.kill en Windows no sirve para esto.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_exe(pid: int) -> str | None:
    if os.name != "nt" or pid <= 0:
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        kernel32.CloseHandle(handle)


def _pid_is_our_worker(pid: int) -> bool:
    if not _pid_alive(pid):
        return False
    exe = _process_exe(pid)
    if not exe:
        return True
    return os.path.basename(exe).lower().startswith("python")


def _log_tail(path: Path, limit: int = 500) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if len(text) <= limit:
        return text
    return text[-limit:]


def _clear(paths: _JobPaths) -> None:
    for path in (paths.pid, paths.result, paths.log):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _job_state(db_path: str, spot_id: str) -> dict[str, Any] | None:
    paths = _paths(db_path, spot_id)
    meta = _read_json(paths.pid)
    pid = int(meta["pid"]) if meta and meta.get("pid") else 0
    if _pid_is_our_worker(pid):
        return {"status": "running", "pid": pid}
    result = _read_json(paths.result)
    if result and result.get("status") in {"done", "error"}:
        return result
    if meta:
        return {
            "status": "error",
            "error": _log_tail(paths.log)
            or "La captura se interrumpió antes de guardar el resultado.",
        }
    return None


def _any_running(db_path: str) -> bool:
    root = _jobs_dir()
    target = os.path.abspath(db_path)
    for pid_path in root.glob("*.pid.json"):
        meta = _read_json(pid_path)
        if not meta:
            continue
        if os.path.abspath(str(meta.get("db_path") or "")) != target:
            continue
        if _pid_is_our_worker(int(meta.get("pid") or 0)):
            return True
    return False


def is_capture_running(db_path: str, spot_id: str) -> bool:
    state = _job_state(db_path, spot_id)
    return bool(state and state.get("status") == "running")


def is_capture_all_running(db_path: str) -> bool:
    return is_capture_running(db_path, ALL_SPOTS_ID)


def _spawn(db_path: str, spot_id: str, mode: str) -> bool:
    paths = _paths(db_path, spot_id)
    _clear(paths)
    log_handle = paths.log.open("w", encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "harpoonbook.bg_worker",
        "--db",
        db_path,
        "--mode",
        mode,
        "--out",
        str(paths.result),
    ]
    if mode == "one":
        cmd.extend(["--spot", spot_id])
    env = os.environ.copy()
    root = str(_PROJECT_ROOT)
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
        )
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log_handle,
            close_fds=True,
            **kwargs,
        )
    except OSError:
        log_handle.close()
        _clear(paths)
        raise
    finally:
        log_handle.close()

    _write_json(
        paths.pid,
        {
            "pid": proc.pid,
            "db_path": os.path.abspath(db_path),
            "spot_id": spot_id,
            "mode": mode,
        },
    )
    return True


def start_background_capture(db_path: str, spot_id: str) -> bool:
    """
    Lanza capture_and_save en otro proceso.
    Devuelve True si arrancó un trabajo nuevo; False si ya había uno en curso.
    """
    if is_capture_all_running(db_path) or is_capture_running(db_path, spot_id):
        return False
    return _spawn(db_path, spot_id, "one")


def start_background_capture_all(db_path: str) -> bool:
    """
    Lanza capture_all_spots en otro proceso (todas las fuentes, todos los spots activos).
    Devuelve True si arrancó un trabajo nuevo; False si ya había uno en curso.
    """
    if _any_running(db_path):
        return False
    return _spawn(db_path, ALL_SPOTS_ID, "all")


def _take(db_path: str, spot_id: str) -> dict[str, Any] | None:
    state = _job_state(db_path, spot_id)
    if not state or state.get("status") == "running":
        return None
    _clear(_paths(db_path, spot_id))
    return state


def take_finished_capture(db_path: str, spot_id: str) -> dict[str, Any] | None:
    """
    Si el trabajo terminó (ok o error), lo saca del registro y lo devuelve.
    Si sigue en curso o no existe, None.
    """
    state = _take(db_path, spot_id)
    if not state:
        return None
    return {
        "status": state.get("status"),
        "snapshot_id": state.get("snapshot_id"),
        "error": state.get("error"),
    }


def take_finished_capture_all(db_path: str) -> dict[str, Any] | None:
    """
    Si la captura global terminó (ok o error), la saca del registro y la devuelve.
    results: lista de {spot_id, snapshot_id} cuando status == done.
    """
    state = _take(db_path, ALL_SPOTS_ID)
    if not state:
        return None
    return {
        "status": state.get("status"),
        "results": state.get("results") or [],
        "error": state.get("error"),
    }
