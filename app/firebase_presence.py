"""Firebase Realtime Database presence — who is online on which tablet.

Uses Firebase REST + Anonymous Auth (no Firebase Admin SDK on devices).
Configure via data/firebase_config.json or Settings → Who's online (Super Admin).
"""

from __future__ import annotations

import json
import platform
import re
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
DEFAULT_PICKER_KEY = "default_picker_name"
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
    current_picker: str | None = None
    fulfilments_today: int = 0
    fulfilments_total: int = 0
    stats_today: dict[str, int] | None = None
    stats_total: dict[str, int] | None = None
    stats_week: dict[str, int] | None = None
    stats_last_week: dict[str, int] | None = None


@dataclass
class UserFulfilmentRow:
    """One picker's fulfilment totals (aggregated across devices)."""

    picker_name: str
    today: int
    total: int
    week: int = 0
    last_week: int = 0
    online: bool = False
    devices: list[str] | None = None

    def __post_init__(self) -> None:
        if self.devices is None:
            self.devices = []

    @property
    def username(self) -> str:
        """Back-compat alias used by older UI code."""
        return self.picker_name


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


def get_default_picker() -> str:
    cfg = _load_json(_app_config_path())
    return str(cfg.get(DEFAULT_PICKER_KEY) or "").strip()


def set_default_picker(name: str) -> str:
    """Remember the picker used on New Scan until the user chooses another."""
    from app.components import capitalize_person_name

    cleaned = capitalize_person_name(name or "").strip()[:80]
    cfg = _load_json(_app_config_path())
    if cleaned:
        cfg[DEFAULT_PICKER_KEY] = cleaned
    elif DEFAULT_PICKER_KEY in cfg:
        del cfg[DEFAULT_PICKER_KEY]
    _save_json(_app_config_path(), cfg)
    return cleaned


def _picker_names_url(name: str | None = None) -> str:
    base = f"{resolve_config()['database_url']}/picker_names"
    if not name:
        return f"{base}.json"
    from app.components import capitalize_person_name

    cleaned = capitalize_person_name(name).strip()
    # Firebase keys cannot contain . # $ [ ]
    key = re.sub(r"[.#$\[\]]+", "_", cleaned)
    return f"{base}/{key}.json"


def fetch_cloud_picker_names() -> list[str]:
    """Return global picker names from Firebase (empty if not configured / error)."""
    if not is_configured():
        return []
    token = _ensure_id_token()
    resp = requests.get(
        _picker_names_url(),
        params={"auth": token},
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not load picker names"))
    raw = resp.json() or {}
    if not isinstance(raw, dict):
        return []
    names: list[str] = []
    for _key, row in raw.items():
        if isinstance(row, dict):
            label = str(row.get("name") or "").strip()
        else:
            label = str(row or "").strip()
        if label:
            names.append(label)
    from app.components import capitalize_person_name

    cleaned = sorted(
        {capitalize_person_name(n) for n in names if n},
        key=str.casefold,
    )
    return cleaned


def publish_cloud_picker_name(name: str) -> None:
    """Add/update one picker name in the shared Firebase list."""
    from app.components import capitalize_person_name

    cleaned = capitalize_person_name(name or "").strip()
    if not cleaned or not is_configured():
        return
    token = _ensure_id_token()
    payload = {
        "name": cleaned,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "firebase_uid": _firebase_uid(),
    }
    resp = requests.put(
        _picker_names_url(cleaned),
        params={"auth": token},
        json=payload,
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not save picker name"))


def remove_cloud_picker_name(name: str) -> None:
    """Remove one picker name from the shared Firebase list."""
    from app.components import capitalize_person_name

    cleaned = capitalize_person_name(name or "").strip()
    if not cleaned or not is_configured():
        return
    token = _ensure_id_token()
    resp = requests.delete(
        _picker_names_url(cleaned),
        params={"auth": token},
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not remove picker name"))


def sync_picker_names() -> list[str]:
    """Pull cloud pickers into local DB, push any local-only names up, return merged list."""
    from app import database

    local = database.list_picker_names()
    if not is_configured():
        return local
    try:
        cloud = fetch_cloud_picker_names()
    except Exception:
        return local

    cloud_folded = {n.casefold() for n in cloud}
    local_folded = {n.casefold() for n in local}

    for name in cloud:
        if name.casefold() not in local_folded:
            database.remember_picker_name(name)

    for name in local:
        if name.casefold() not in cloud_folded:
            try:
                publish_cloud_picker_name(name)
            except Exception:
                pass

    return database.list_picker_names()


def remember_picker_name_synced(name: str) -> str:
    """Save picker locally and to Firebase so all tablets see it."""
    from app import database
    from app.components import capitalize_person_name

    cleaned = capitalize_person_name(name or "").strip()
    if not cleaned:
        return ""
    database.remember_picker_name(cleaned)
    if is_configured():
        try:
            publish_cloud_picker_name(cleaned)
        except Exception:
            # Local save still succeeded; next sync can retry.
            pass
    return cleaned


def delete_picker_name_synced(name: str) -> None:
    """Remove picker locally and from Firebase."""
    from app import database
    from app.components import capitalize_person_name

    cleaned = capitalize_person_name(name or "").strip()
    if not cleaned:
        return
    database.delete_picker_name(cleaned)
    if is_configured():
        try:
            remove_cloud_picker_name(cleaned)
        except Exception:
            pass


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
        "current_picker": get_default_picker() or None,
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
        week_map = _as_int_map(local_stats.get("week"))
        last_week_map = _as_int_map(local_stats.get("last_week"))
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
                current_picker=(
                    str(row["current_picker"]).strip()
                    if row.get("current_picker")
                    else None
                ),
                fulfilments_today=today_sum,
                fulfilments_total=total_sum,
                stats_today=today_map,
                stats_total=total_map,
                stats_week=week_map,
                stats_last_week=last_week_map,
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
    """Sum completed fulfilments per picker across all reporting devices."""
    today: dict[str, int] = {}
    total: dict[str, int] = {}
    week: dict[str, int] = {}
    last_week: dict[str, int] = {}

    for entry in entries:
        for name, count in (entry.stats_today or {}).items():
            today[name] = today.get(name, 0) + int(count)
        for name, count in (entry.stats_total or {}).items():
            total[name] = total.get(name, 0) + int(count)
        for name, count in (entry.stats_week or {}).items():
            week[name] = week.get(name, 0) + int(count)
        for name, count in (entry.stats_last_week or {}).items():
            last_week[name] = last_week.get(name, 0) + int(count)

    # Mark a picker "online" if any online tablet recently reported that picker
    # in today's stats (they are actively being fulfilled on an open device).
    online_pickers: set[str] = set()
    for entry in entries:
        if not entry.online:
            continue
        for name in (entry.stats_today or {}):
            if int((entry.stats_today or {}).get(name, 0)) > 0:
                online_pickers.add(name.casefold())

    names = sorted(
        set(today) | set(total) | set(week) | set(last_week),
        key=lambda n: (-today.get(n, 0), n.lower()),
    )
    rows: list[UserFulfilmentRow] = []
    for name in names:
        rows.append(
            UserFulfilmentRow(
                picker_name=name,
                today=int(today.get(name, 0)),
                total=int(total.get(name, 0)),
                week=int(week.get(name, 0)),
                last_week=int(last_week.get(name, 0)),
                online=name.casefold() in online_pickers,
                devices=[],
            )
        )
    return rows


def _dashboard_settings_url() -> str:
    return f"{resolve_config()['database_url']}/dashboard_settings.json"


def get_dashboard_settings() -> dict[str, Any]:
    """Load shared dashboard settings (week filter, prize message, etc.)."""
    local = _load_json(_app_config_path())
    week = str(local.get("dashboard_week_filter") or "this").strip().lower()
    prize = str(local.get("dashboard_prize_message") or "").strip()
    settings = {
        "week_filter": "last" if week == "last" else "this",
        "prize_message": prize,
        "updated_by": str(local.get("dashboard_settings_updated_by") or "").strip() or None,
    }
    if not is_configured():
        return settings
    try:
        token = _ensure_id_token()
        resp = requests.get(
            _dashboard_settings_url(),
            params={"auth": token},
            timeout=15,
        )
        if resp.status_code >= 400:
            return settings
        raw = resp.json() or {}
        if not isinstance(raw, dict):
            return settings
        remote_week = str(raw.get("week_filter") or settings["week_filter"]).strip().lower()
        settings["week_filter"] = "last" if remote_week == "last" else "this"
        settings["prize_message"] = str(raw.get("prize_message") or "").strip()
        settings["updated_by"] = (
            str(raw.get("updated_by") or "").strip() or settings["updated_by"]
        )
        return settings
    except Exception:
        return settings


def save_dashboard_settings(
    *,
    week_filter: str | None = None,
    prize_message: str | None = None,
    updated_by: str | None = None,
) -> dict[str, Any]:
    """Merge and persist dashboard settings locally and to Firebase when configured."""
    current = get_dashboard_settings()
    if week_filter is not None:
        current["week_filter"] = (
            "last" if str(week_filter).strip().lower() == "last" else "this"
        )
    if prize_message is not None:
        # Optional — empty string clears the message.
        current["prize_message"] = str(prize_message).strip()[:280]
    if updated_by is not None:
        current["updated_by"] = (updated_by or "").strip() or None

    local = _load_json(_app_config_path())
    local["dashboard_week_filter"] = current["week_filter"]
    local["dashboard_prize_message"] = current["prize_message"]
    local["dashboard_settings_updated_by"] = current.get("updated_by") or ""
    _save_json(_app_config_path(), local)

    if not is_configured():
        return current

    token = _ensure_id_token()
    payload = {
        "week_filter": current["week_filter"],
        "prize_message": current["prize_message"],
        "updated_by": current.get("updated_by"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "firebase_uid": _firebase_uid(),
    }
    resp = requests.put(
        _dashboard_settings_url(),
        params={"auth": token},
        json=payload,
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not save dashboard settings"))
    return current


def get_week_filter() -> str:
    """Return ``this`` or ``last``. Falls back to this week."""
    return str(get_dashboard_settings().get("week_filter") or "this")


def set_week_filter(which: str, *, updated_by: str | None = None) -> str:
    """Persist week graph filter. Returns normalized ``this`` or ``last``."""
    settings = save_dashboard_settings(week_filter=which, updated_by=updated_by)
    return str(settings["week_filter"])


def get_prize_message() -> str:
    return str(get_dashboard_settings().get("prize_message") or "").strip()


def set_prize_message(message: str, *, updated_by: str | None = None) -> str:
    settings = save_dashboard_settings(prize_message=message, updated_by=updated_by)
    return str(settings.get("prize_message") or "")


def dashboard_snapshot() -> dict[str, Any]:
    """Presence + fulfilment rows for the Home dashboard."""
    from app import database

    settings = get_dashboard_settings()
    week_filter = str(settings.get("week_filter") or "this")
    prize_message = str(settings.get("prize_message") or "").strip()
    week_start, week_end = database.week_date_bounds(week_filter)
    if not is_configured():
        local = database.local_fulfilment_snapshot()
        today = local.get("today") or {}
        total = local.get("total") or {}
        week = local.get("week") or {}
        last_week = local.get("last_week") or {}
        names = sorted(
            set(today) | set(total) | set(week) | set(last_week),
            key=lambda n: (-int(today.get(n, 0)), n.lower()),
        )
        rows = [
            UserFulfilmentRow(
                picker_name=name,
                today=int(today.get(name, 0)),
                total=int(total.get(name, 0)),
                week=int(week.get(name, 0)),
                last_week=int(last_week.get(name, 0)),
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
            "week_filter": week_filter,
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "prize_message": prize_message,
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
        "week_filter": week_filter,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "prize_message": prize_message,
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


# --- Shared app users (usernames / password hashes) ---


def _app_users_url(username: str | None = None) -> str:
    base = f"{resolve_config()['database_url']}/app_users"
    if not username:
        return f"{base}.json"
    key = re.sub(r"[.#$\[\]]+", "_", (username or "").strip().lower())
    return f"{base}/{key}.json"


def fetch_cloud_app_users() -> list[dict[str, str]]:
    """Return shared user records from Firebase (hashes only, never plaintext)."""
    if not is_configured():
        return []
    token = _ensure_id_token()
    resp = requests.get(
        _app_users_url(),
        params={"auth": token},
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not load app users"))
    raw = resp.json() or {}
    if not isinstance(raw, dict):
        return []
    users: list[dict[str, str]] = []
    for _key, row in raw.items():
        if not isinstance(row, dict):
            continue
        username = str(row.get("username") or "").strip()
        salt = str(row.get("salt") or "").strip()
        password_hash = str(row.get("password_hash") or "").strip()
        if not username or not salt or not password_hash:
            continue
        users.append(
            {
                "username": username,
                "role": str(row.get("role") or "admin").strip(),
                "salt": salt,
                "password_hash": password_hash,
                "updated_at": str(row.get("updated_at") or "").strip(),
            }
        )
    return users


def publish_cloud_app_user(record: dict) -> None:
    """Create/update one shared user account in Firebase."""
    username = str(record.get("username") or "").strip()
    if not username or not is_configured():
        return
    token = _ensure_id_token()
    payload = {
        "username": username,
        "role": str(record.get("role") or "admin"),
        "salt": str(record.get("salt") or ""),
        "password_hash": str(record.get("password_hash") or ""),
        "updated_at": str(record.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        "firebase_uid": _firebase_uid(),
    }
    if not payload["salt"] or not payload["password_hash"]:
        return
    resp = requests.put(
        _app_users_url(username),
        params={"auth": token},
        json=payload,
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not save app user"))


def remove_cloud_app_user(username: str) -> None:
    """Remove one shared user account from Firebase."""
    cleaned = (username or "").strip()
    if not cleaned or not is_configured():
        return
    token = _ensure_id_token()
    resp = requests.delete(
        _app_users_url(cleaned),
        params={"auth": token},
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not remove app user"))
