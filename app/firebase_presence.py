"""Firebase Realtime Database presence — who is online on which tablet.

Uses Firebase REST + Anonymous Auth (no Firebase Admin SDK on devices).
Configure via data/firebase_config.json or Settings → Who's online (Super Admin).
"""

from __future__ import annotations

import json
import platform
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from app.paths import get_data_dir

CONFIG_FILE = "firebase_config.json"
DEVICE_ID_FILE = "device_id.txt"
DEVICE_LABEL_KEY = "device_label"
AUTH_CACHE_FILE = "firebase_auth.json"

HEARTBEAT_SECONDS = 30
ONLINE_WITHIN_SECONDS = 90

_scheduler_stop = threading.Event()
_scheduler_thread: threading.Thread | None = None
_scheduler_lock = threading.Lock()
_auth_lock = threading.Lock()


@dataclass
class PresenceEntry:
    device_id: str
    device_label: str
    username: str | None
    role: str | None
    last_seen: str
    last_seen_epoch: float
    online: bool
    app_version: str
    is_this_device: bool = False
    fulfilments_today: int = 0
    fulfilments_total: int = 0
    stats_today: dict[str, int] | None = None
    stats_total: dict[str, int] | None = None


@dataclass
class UserFulfilmentRow:
    username: str
    today: int
    total: int
    online: bool
    devices: list[str]


def _config_path() -> Path:
    return get_data_dir() / CONFIG_FILE


def _device_id_path() -> Path:
    return get_data_dir() / DEVICE_ID_FILE


def _auth_cache_path() -> Path:
    return get_data_dir() / AUTH_CACHE_FILE


def _app_config_path() -> Path:
    return get_data_dir() / "config.json"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_device_id() -> str:
    path = _device_id_path()
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    device_id = str(uuid.uuid4())
    path.write_text(device_id, encoding="utf-8")
    return device_id


def get_device_label() -> str:
    cfg = _load_json(_app_config_path())
    label = str(cfg.get(DEVICE_LABEL_KEY) or "").strip()
    if label:
        return label
    try:
        return platform.node() or "Tablet"
    except Exception:
        return "Tablet"


def set_device_label(label: str) -> str:
    cleaned = (label or "").strip()[:60] or get_device_label()
    cfg = _load_json(_app_config_path())
    cfg[DEVICE_LABEL_KEY] = cleaned
    _save_json(_app_config_path(), cfg)
    return cleaned


def resolve_config() -> dict[str, str]:
    """Return api_key, database_url, project_id (empty strings if unset)."""
    file_cfg = _load_json(_config_path())
    return {
        "api_key": str(file_cfg.get("api_key") or "").strip(),
        "database_url": str(file_cfg.get("database_url") or "").strip().rstrip("/"),
        "project_id": str(file_cfg.get("project_id") or "").strip(),
    }


def is_configured() -> bool:
    cfg = resolve_config()
    return bool(cfg["api_key"] and cfg["database_url"])


def save_config(*, api_key: str, database_url: str, project_id: str = "") -> None:
    api_key = (api_key or "").strip()
    database_url = (database_url or "").strip().rstrip("/")
    project_id = (project_id or "").strip()
    if not api_key or not database_url:
        raise ValueError("api_key and database_url are required.")
    if not database_url.startswith("https://"):
        raise ValueError("database_url must start with https://")
    _save_json(
        _config_path(),
        {
            "api_key": api_key,
            "database_url": database_url,
            "project_id": project_id,
        },
    )
    # Force re-auth with new project.
    cache = _auth_cache_path()
    if cache.exists():
        cache.unlink()


def clear_config() -> None:
    path = _config_path()
    if path.exists():
        path.unlink()
    cache = _auth_cache_path()
    if cache.exists():
        cache.unlink()


def _app_version() -> str:
    try:
        import tomllib
        from pathlib import Path as P

        root = P(__file__).resolve().parent.parent / "pyproject.toml"
        data = tomllib.loads(root.read_text(encoding="utf-8"))
        return str(data.get("tool", {}).get("flet", {}).get("build_version") or "?")
    except Exception:
        return "?"


def _ensure_id_token() -> str:
    """Anonymous Firebase Auth — returns idToken."""
    cfg = resolve_config()
    if not cfg["api_key"]:
        raise RuntimeError("Firebase is not configured.")

    with _auth_lock:
        cache = _load_json(_auth_cache_path())
        token = str(cache.get("id_token") or "")
        refresh = str(cache.get("refresh_token") or "")
        expires_at = float(cache.get("expires_at") or 0)
        if token and time.time() < expires_at - 60:
            return token

        if refresh:
            try:
                return _refresh_id_token(cfg["api_key"], refresh)
            except Exception:
                pass

        return _sign_in_anonymous(cfg["api_key"])


def _sign_in_anonymous(api_key: str) -> str:
    url = (
        "https://identitytoolkit.googleapis.com/v1/accounts:signUp"
        f"?key={api_key}"
    )
    resp = requests.post(
        url,
        json={"returnSecureToken": True},
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Anonymous sign-in failed"))
    data = resp.json()
    _store_auth(data)
    return str(data["idToken"])


def _refresh_id_token(api_key: str, refresh_token: str) -> str:
    url = f"https://securetoken.googleapis.com/v1/token?key={api_key}"
    resp = requests.post(
        url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Token refresh failed"))
    data = resp.json()
    # Refresh response uses snake_case keys.
    packed = {
        "idToken": data.get("id_token") or data.get("idToken"),
        "refreshToken": data.get("refresh_token") or data.get("refreshToken"),
        "expiresIn": data.get("expires_in") or data.get("expiresIn") or "3600",
        "localId": data.get("user_id") or data.get("localId") or "",
    }
    _store_auth(packed)
    return str(packed["idToken"])


def _store_auth(data: dict[str, Any]) -> None:
    expires_in = int(str(data.get("expiresIn") or "3600"))
    _save_json(
        _auth_cache_path(),
        {
            "id_token": data.get("idToken"),
            "refresh_token": data.get("refreshToken"),
            "firebase_uid": data.get("localId") or "",
            "expires_at": time.time() + expires_in,
        },
    )


def _firebase_uid() -> str:
    return str(_load_json(_auth_cache_path()).get("firebase_uid") or "")


def _firebase_error(resp: requests.Response, fallback: str) -> str:
    try:
        payload = resp.json()
        err = payload.get("error") if isinstance(payload, dict) else payload
        if isinstance(err, dict):
            message = err.get("message") or str(err)
        else:
            message = str(err or resp.text)
        hint = ""
        lower = message.lower()
        if "permission" in lower or resp.status_code in (401, 403):
            hint = (
                " — open Realtime Database → Rules, paste the presence rules "
                "from docs/FIREBASE_SETUP.md, then click Publish."
            )
        return f"{fallback}: {message}{hint}"
    except Exception:
        return f"{fallback} (HTTP {resp.status_code})"


def _presence_url(device_id: str | None = None) -> str:
    cfg = resolve_config()
    base = cfg["database_url"]
    if device_id:
        return f"{base}/presence/{device_id}.json"
    return f"{base}/presence.json"


def publish_heartbeat(
    *,
    username: str | None,
    role: str | None,
    online: bool = True,
) -> None:
    if not is_configured():
        return

    token = _ensure_id_token()
    uid = _firebase_uid()
    device_id = get_device_id()
    now = datetime.now(timezone.utc)
    try:
        from app import database

        local_stats = database.local_fulfilment_snapshot()
    except Exception:
        local_stats = {
            "today": {},
            "total": {},
            "today_sum": 0,
            "total_sum": 0,
            "as_of": now.isoformat(),
        }

    payload = {
        "device_id": device_id,
        "device_label": get_device_label(),
        "username": (username or "").strip() or None,
        "role": (role or "").strip() or None,
        "firebase_uid": uid,
        "online": bool(online),
        "last_seen": now.isoformat(),
        "last_seen_epoch": now.timestamp(),
        "app_version": _app_version(),
        "platform": platform.system(),
        "local_stats": local_stats,
    }
    resp = requests.put(
        _presence_url(device_id),
        params={"auth": token},
        json=payload,
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Presence update failed"))


def mark_offline(*, username: str | None = None, role: str | None = None) -> None:
    if not is_configured():
        return
    try:
        publish_heartbeat(username=username, role=role, online=False)
    except Exception:
        pass


def _as_int_map(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        name = str(key or "").strip()
        if not name:
            continue
        try:
            out[name] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def fetch_presence() -> list[PresenceEntry]:
    if not is_configured():
        return []

    token = _ensure_id_token()
    resp = requests.get(
        _presence_url(),
        params={"auth": token},
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not load presence"))

    raw = resp.json() or {}
    if not isinstance(raw, dict):
        return []

    now = time.time()
    this_id = get_device_id()
    entries: list[PresenceEntry] = []
    for device_id, row in raw.items():
        if not isinstance(row, dict):
            continue
        epoch = float(row.get("last_seen_epoch") or 0)
        flagged_online = bool(row.get("online"))
        recent = epoch > 0 and (now - epoch) <= ONLINE_WITHIN_SECONDS
        local_stats = row.get("local_stats") if isinstance(row.get("local_stats"), dict) else {}
        today_map = _as_int_map(local_stats.get("today"))
        total_map = _as_int_map(local_stats.get("total"))
        try:
            today_sum = int(local_stats.get("today_sum") or sum(today_map.values()))
        except (TypeError, ValueError):
            today_sum = sum(today_map.values())
        try:
            total_sum = int(local_stats.get("total_sum") or sum(total_map.values()))
        except (TypeError, ValueError):
            total_sum = sum(total_map.values())
        entries.append(
            PresenceEntry(
                device_id=str(row.get("device_id") or device_id),
                device_label=str(row.get("device_label") or device_id)[:60],
                username=(str(row["username"]) if row.get("username") else None),
                role=(str(row["role"]) if row.get("role") else None),
                last_seen=str(row.get("last_seen") or ""),
                last_seen_epoch=epoch,
                online=flagged_online and recent,
                app_version=str(row.get("app_version") or ""),
                is_this_device=str(device_id) == this_id,
                fulfilments_today=today_sum,
                fulfilments_total=total_sum,
                stats_today=today_map,
                stats_total=total_map,
            )
        )

    entries.sort(
        key=lambda e: (
            0 if e.online else 1,
            (e.username or "").lower(),
            e.device_label.lower(),
        )
    )
    return entries


def aggregate_fulfilments(entries: list[PresenceEntry]) -> list[UserFulfilmentRow]:
    """Sum completed fulfilments per checker across all reporting devices."""
    today: dict[str, int] = {}
    total: dict[str, int] = {}
    online_users: set[str] = set()
    devices_by_user: dict[str, list[str]] = {}

    for entry in entries:
        if entry.username and entry.online:
            key = entry.username.strip()
            if key:
                online_users.add(key.casefold())
                devices_by_user.setdefault(key, [])
                if entry.device_label not in devices_by_user[key]:
                    devices_by_user[key].append(entry.device_label)

        for name, count in (entry.stats_today or {}).items():
            today[name] = today.get(name, 0) + int(count)
        for name, count in (entry.stats_total or {}).items():
            total[name] = total.get(name, 0) + int(count)

    names = sorted(set(today) | set(total), key=lambda n: (-today.get(n, 0), n.lower()))
    rows: list[UserFulfilmentRow] = []
    for name in names:
        matching_devices = []
        for key, devices in devices_by_user.items():
            if key.casefold() == name.casefold():
                matching_devices.extend(devices)
        rows.append(
            UserFulfilmentRow(
                username=name,
                today=int(today.get(name, 0)),
                total=int(total.get(name, 0)),
                online=name.casefold() in online_users,
                devices=matching_devices,
            )
        )
    return rows


def dashboard_snapshot() -> dict[str, Any]:
    """Presence + fulfilment rows for the Home dashboard."""
    if not is_configured():
        from app import database

        local = database.local_fulfilment_snapshot()
        today = local.get("today") or {}
        total = local.get("total") or {}
        names = sorted(set(today) | set(total), key=lambda n: (-int(today.get(n, 0)), n.lower()))
        rows = [
            UserFulfilmentRow(
                username=name,
                today=int(today.get(name, 0)),
                total=int(total.get(name, 0)),
                online=False,
                devices=[],
            )
            for name in names
        ]
        return {
            "configured": False,
            "online_count": 0,
            "device_count": 0,
            "presence": [],
            "fulfilments": rows,
            "today_sum": int(local.get("today_sum") or 0),
            "source": "local",
        }

    presence = fetch_presence()
    fulfilments = aggregate_fulfilments(presence)
    online = [e for e in presence if e.online]
    return {
        "configured": True,
        "online_count": len(online),
        "device_count": len(presence),
        "presence": presence,
        "fulfilments": fulfilments,
        "today_sum": sum(r.today for r in fulfilments),
        "source": "firebase",
    }

def presence_status_text() -> str:
    if not is_configured():
        return "Firebase not set up yet — Super Admin must add project keys once."
    return (
        f"Reporting as “{get_device_label()}”. "
        f"Devices count as online if seen within {ONLINE_WITHIN_SECONDS}s."
    )


def start_presence_scheduler(
    *,
    get_user: Callable[[], tuple[str | None, str | None]],
) -> None:
    """Background heartbeat while the app is open."""
    global _scheduler_thread
    with _scheduler_lock:
        if _scheduler_thread and _scheduler_thread.is_alive():
            return
        _scheduler_stop.clear()

        def loop():
            while not _scheduler_stop.is_set():
                if is_configured():
                    try:
                        username, role = get_user()
                        publish_heartbeat(username=username, role=role, online=True)
                    except Exception:
                        pass
                _scheduler_stop.wait(HEARTBEAT_SECONDS)

        _scheduler_thread = threading.Thread(
            target=loop,
            name="firebase-presence",
            daemon=True,
        )
        _scheduler_thread.start()


def stop_presence_scheduler() -> None:
    _scheduler_stop.set()
