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
from datetime import date, datetime, timezone
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
    """Return api_key, database_url, project_id, and optional GA4 fields."""
    file_cfg = _load_json(_config_path())
    return {
        "api_key": str(file_cfg.get("api_key") or "").strip(),
        "database_url": str(file_cfg.get("database_url") or "").strip().rstrip("/"),
        "project_id": str(file_cfg.get("project_id") or "").strip(),
        "ga_measurement_id": str(file_cfg.get("ga_measurement_id") or "").strip(),
        "ga_api_secret": str(file_cfg.get("ga_api_secret") or "").strip(),
    }


def is_configured() -> bool:
    cfg = resolve_config()
    return bool(cfg["api_key"] and cfg["database_url"])


def save_config(
    *,
    api_key: str,
    database_url: str,
    project_id: str = "",
    ga_measurement_id: str | None = None,
    ga_api_secret: str | None = None,
) -> None:
    api_key = (api_key or "").strip()
    database_url = (database_url or "").strip().rstrip("/")
    project_id = (project_id or "").strip()
    if not api_key or not database_url:
        raise ValueError("api_key and database_url are required.")
    if not database_url.startswith("https://"):
        raise ValueError("database_url must start with https://")

    existing = _load_json(_config_path())
    if ga_measurement_id is None:
        ga_mid = str(existing.get("ga_measurement_id") or "").strip()
    else:
        ga_mid = (ga_measurement_id or "").strip()
    if ga_api_secret is None:
        ga_secret = str(existing.get("ga_api_secret") or "").strip()
    else:
        ga_secret = (ga_api_secret or "").strip()

    payload = {
        "api_key": api_key,
        "database_url": database_url,
        "project_id": project_id,
    }
    if ga_mid:
        payload["ga_measurement_id"] = ga_mid
    if ga_secret:
        payload["ga_api_secret"] = ga_secret

    _save_json(_config_path(), payload)
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
                " — open Realtime Database -> Rules, paste the rules "
                "from docs/FIREBASE_SETUP.md (including fleet_sync_settings "
                "and device_backups), then click Publish."
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

    # Google Analytics (optional) — daily active tablet + session.
    if online:
        try:
            from app import analytics

            analytics.track_tablet_active(
                username=username,
                device_label=get_device_label(),
            )
        except Exception:
            pass


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


# --- Shared barcode master list ---

_BARCODE_MASTER_MAX_BYTES = 2_500_000  # ~2.5 MB Excel


def _barcode_master_meta_url() -> str:
    return f"{resolve_config()['database_url']}/barcode_master/meta.json"


def _barcode_master_content_url() -> str:
    return f"{resolve_config()['database_url']}/barcode_master/content.json"


def fetch_barcode_master_meta() -> dict[str, Any] | None:
    """Return cloud barcode master metadata, or None if missing/not configured."""
    if not is_configured():
        return None
    token = _ensure_id_token()
    resp = requests.get(
        _barcode_master_meta_url(),
        params={"auth": token},
        timeout=20,
    )
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not load barcode master metadata"))
    raw = resp.json()
    if not isinstance(raw, dict) or not raw:
        return None
    return raw


def publish_barcode_master(
    file_bytes: bytes,
    *,
    updated_by: str,
    filename: str = "BarcodeMasterList.xlsx",
    row_count: int = 0,
) -> dict[str, Any]:
    """Upload barcode master Excel to Firebase for all tablets."""
    import base64
    import hashlib

    if not is_configured():
        raise RuntimeError("Firebase is not set up on this device.")
    if not file_bytes:
        raise ValueError("Barcode master file is empty.")
    if len(file_bytes) > _BARCODE_MASTER_MAX_BYTES:
        raise ValueError(
            f"Barcode master file is too large ({len(file_bytes):,} bytes). "
            f"Keep under {_BARCODE_MASTER_MAX_BYTES:,} bytes."
        )

    token = _ensure_id_token()
    digest = hashlib.sha256(file_bytes).hexdigest()
    updated_at = datetime.now(timezone.utc).isoformat()
    meta = {
        "updated_at": updated_at,
        "updated_by": (updated_by or "").strip() or "unknown",
        "filename": (filename or "BarcodeMasterList.xlsx").strip(),
        "byte_count": len(file_bytes),
        "row_count": int(row_count or 0),
        "sha256": digest,
        "firebase_uid": _firebase_uid(),
    }
    content = {
        "encoding": "base64",
        "data": base64.b64encode(file_bytes).decode("ascii"),
        "sha256": digest,
        "updated_at": updated_at,
    }

    resp = requests.put(
        _barcode_master_content_url(),
        params={"auth": token},
        json=content,
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not upload barcode master file"))

    resp = requests.put(
        _barcode_master_meta_url(),
        params={"auth": token},
        json=meta,
        timeout=20,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not save barcode master metadata"))
    return meta


def download_barcode_master_bytes() -> tuple[bytes, dict[str, Any]]:
    """Download the shared barcode master Excel from Firebase."""
    import base64

    if not is_configured():
        raise RuntimeError("Firebase is not set up on this device.")
    meta = fetch_barcode_master_meta()
    if not meta:
        raise FileNotFoundError("No barcode master list has been published yet.")

    token = _ensure_id_token()
    resp = requests.get(
        _barcode_master_content_url(),
        params={"auth": token},
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not download barcode master file"))
    raw = resp.json() or {}
    if not isinstance(raw, dict):
        raise RuntimeError("Invalid barcode master payload from Firebase.")
    data = str(raw.get("data") or "").strip()
    if not data:
        raise RuntimeError("Barcode master payload is empty.")
    try:
        file_bytes = base64.b64decode(data.encode("ascii"), validate=False)
    except Exception as exc:
        raise RuntimeError(f"Could not decode barcode master file: {exc}") from exc
    if not file_bytes:
        raise RuntimeError("Decoded barcode master file is empty.")
    return file_bytes, meta


# --- Fleet daily backup (Monitor-controlled) ---

_FLEET_SYNC_DEFAULT_TIME = "17:00"
_DEVICE_BACKUP_MAX_RAW_BYTES = 8_000_000  # ~8 MB uncompressed payload

FLEET_OUTPUT_FULL_DB = "full_db"
FLEET_OUTPUT_SESSIONS_JSON = "sessions_json"
FLEET_OUTPUT_PDF = "pdf"
FLEET_OUTPUT_DB_AND_PDF = "db_and_pdf"
FLEET_OUTPUT_MODES = (
    FLEET_OUTPUT_FULL_DB,
    FLEET_OUTPUT_SESSIONS_JSON,
    FLEET_OUTPUT_PDF,
    FLEET_OUTPUT_DB_AND_PDF,
)
FLEET_OUTPUT_LABELS = {
    FLEET_OUTPUT_FULL_DB: "scanner.db",
    FLEET_OUTPUT_SESSIONS_JSON: "Sessions (JSON)",
    FLEET_OUTPUT_PDF: "Report (PDF)",
    FLEET_OUTPUT_DB_AND_PDF: "scanner.db + PDF (ZIP)",
}


def _fleet_sync_settings_url() -> str:
    return f"{resolve_config()['database_url']}/fleet_sync_settings.json"


def _device_backup_meta_url(device_id: str) -> str:
    return f"{resolve_config()['database_url']}/device_backups/{device_id}/meta.json"


def _device_backup_content_url(device_id: str) -> str:
    return f"{resolve_config()['database_url']}/device_backups/{device_id}/content.json"


def _device_backups_root_url() -> str:
    return f"{resolve_config()['database_url']}/device_backups.json"


def _normalize_fleet_time(value: str) -> str:
    import re

    text = (value or "").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return _FLEET_SYNC_DEFAULT_TIME
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return _FLEET_SYNC_DEFAULT_TIME
    return f"{hour:02d}:{minute:02d}"


def normalize_fleet_output(value: str | None) -> str:
    cleaned = str(value or "").strip().lower()
    if cleaned in FLEET_OUTPUT_MODES:
        return cleaned
    return FLEET_OUTPUT_FULL_DB


def fleet_output_label(value: str | None) -> str:
    mode = normalize_fleet_output(value)
    return FLEET_OUTPUT_LABELS.get(mode, FLEET_OUTPUT_LABELS[FLEET_OUTPUT_FULL_DB])


def normalize_fleet_report_date(value: str | None) -> str:
    """Return YYYY-MM-DD. Accepts ISO or dd/mm/yyyy. Defaults to today."""
    text = str(value or "").strip()
    if text:
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            pass
        match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
        if match:
            day, month, year = map(int, match.groups())
            try:
                return date(year, month, day).isoformat()
            except ValueError:
                pass
    return date.today().isoformat()


def normalize_fleet_report_range(
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[str, str]:
    """Return (from, to) as YYYY-MM-DD with from <= to. Defaults both to today."""
    start = date.fromisoformat(normalize_fleet_report_date(date_from))
    end = date.fromisoformat(normalize_fleet_report_date(date_to))
    if end < start:
        start, end = end, start
    return start.isoformat(), end.isoformat()


def format_fleet_report_date(value: str | None) -> str:
    """Display date as dd/mm/yyyy."""
    iso = normalize_fleet_report_date(value)
    try:
        return date.fromisoformat(iso).strftime("%d/%m/%Y")
    except ValueError:
        return iso


def format_fleet_report_range(
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    start, end = normalize_fleet_report_range(date_from, date_to)
    a = format_fleet_report_date(start)
    b = format_fleet_report_date(end)
    if a == b:
        return a
    return f"{a} – {b}"


def get_fleet_download_folder() -> Path:
    """Local Monitor folder used by Download all (defaults to data/fleet_backups)."""
    local = _load_json(_app_config_path())
    raw = str(local.get("fleet_download_folder") or "").strip()
    if raw:
        return Path(raw)
    return get_data_dir() / "fleet_backups"


def set_fleet_download_folder(path: str | Path | None) -> Path:
    """Persist Monitor download folder. Pass None/empty to reset to default."""
    local = _load_json(_app_config_path())
    text = str(path or "").strip()
    if not text:
        local.pop("fleet_download_folder", None)
        _save_json(_app_config_path(), local)
        return get_fleet_download_folder()
    folder = Path(text).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    local["fleet_download_folder"] = str(folder)
    _save_json(_app_config_path(), local)
    return folder


def get_fleet_sync_settings() -> dict[str, Any]:
    """Load Monitor-controlled daily backup schedule (local cache + Firebase)."""
    local = _load_json(_app_config_path())
    # Back-compat: single report_date becomes both from and to.
    legacy = str(local.get("fleet_sync_report_date") or "").strip()
    from_raw = str(local.get("fleet_sync_report_date_from") or legacy or "")
    to_raw = str(local.get("fleet_sync_report_date_to") or legacy or "")
    date_from, date_to = normalize_fleet_report_range(from_raw, to_raw)
    settings = {
        "enabled": bool(local.get("fleet_sync_enabled")),
        "time": _normalize_fleet_time(str(local.get("fleet_sync_time") or _FLEET_SYNC_DEFAULT_TIME)),
        "output_mode": normalize_fleet_output(str(local.get("fleet_sync_output_mode") or "")),
        "report_date_from": date_from,
        "report_date_to": date_to,
        "force_request_at": str(local.get("fleet_sync_force_request_at") or "").strip() or None,
        "updated_by": str(local.get("fleet_sync_updated_by") or "").strip() or None,
        "updated_at": str(local.get("fleet_sync_updated_at") or "").strip() or None,
        "download_folder": str(get_fleet_download_folder()),
    }
    if not is_configured():
        return settings
    try:
        token = _ensure_id_token()
        resp = requests.get(
            _fleet_sync_settings_url(),
            params={"auth": token},
            timeout=15,
        )
        if resp.status_code >= 400:
            return settings
        raw = resp.json() or {}
        if not isinstance(raw, dict) or not raw:
            return settings
        settings["enabled"] = bool(raw.get("enabled"))
        settings["time"] = _normalize_fleet_time(str(raw.get("time") or settings["time"]))
        settings["output_mode"] = normalize_fleet_output(
            str(raw.get("output_mode") or settings["output_mode"])
        )
        legacy_remote = str(raw.get("report_date") or "").strip()
        settings["report_date_from"], settings["report_date_to"] = normalize_fleet_report_range(
            str(raw.get("report_date_from") or legacy_remote or settings["report_date_from"]),
            str(raw.get("report_date_to") or legacy_remote or settings["report_date_to"]),
        )
        settings["force_request_at"] = (
            str(raw.get("force_request_at") or "").strip() or settings["force_request_at"]
        )
        settings["updated_by"] = str(raw.get("updated_by") or "").strip() or settings["updated_by"]
        settings["updated_at"] = str(raw.get("updated_at") or "").strip() or settings["updated_at"]
        local["fleet_sync_enabled"] = settings["enabled"]
        local["fleet_sync_time"] = settings["time"]
        local["fleet_sync_output_mode"] = settings["output_mode"]
        local["fleet_sync_report_date_from"] = settings["report_date_from"]
        local["fleet_sync_report_date_to"] = settings["report_date_to"]
        local.pop("fleet_sync_report_date", None)
        local["fleet_sync_force_request_at"] = settings.get("force_request_at") or ""
        local["fleet_sync_updated_by"] = settings.get("updated_by") or ""
        local["fleet_sync_updated_at"] = settings.get("updated_at") or ""
        _save_json(_app_config_path(), local)
        settings["download_folder"] = str(get_fleet_download_folder())
        return settings
    except Exception:
        return settings


def save_fleet_sync_settings(
    *,
    enabled: bool,
    sync_time: str,
    output_mode: str | None = None,
    report_date_from: str | None = None,
    report_date_to: str | None = None,
    force_request_at: str | None = None,
    updated_by: str | None = None,
    preserve_force_request: bool = True,
) -> dict[str, Any]:
    """Persist fleet schedule locally and to Firebase (Monitor Super Admin)."""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", (sync_time or "").strip())
    if not match:
        raise ValueError("Time must be HH:MM (24-hour), e.g. 17:00")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ValueError("Time must be HH:MM (24-hour), e.g. 17:00")
    normalized = f"{hour:02d}:{minute:02d}"
    mode = normalize_fleet_output(output_mode)

    existing = get_fleet_sync_settings()
    if force_request_at is not None:
        force_value = (force_request_at or "").strip() or None
    elif preserve_force_request:
        force_value = existing.get("force_request_at")
    else:
        force_value = None

    from_iso, to_iso = normalize_fleet_report_range(
        report_date_from
        if report_date_from is not None
        else str(existing.get("report_date_from") or ""),
        report_date_to
        if report_date_to is not None
        else str(existing.get("report_date_to") or ""),
    )

    updated_at = datetime.now(timezone.utc).isoformat()
    current = {
        "enabled": bool(enabled),
        "time": normalized,
        "output_mode": mode,
        "report_date_from": from_iso,
        "report_date_to": to_iso,
        "force_request_at": force_value,
        "updated_by": (updated_by or "").strip() or None,
        "updated_at": updated_at,
        "download_folder": str(get_fleet_download_folder()),
    }

    local = _load_json(_app_config_path())
    local["fleet_sync_enabled"] = current["enabled"]
    local["fleet_sync_time"] = current["time"]
    local["fleet_sync_output_mode"] = current["output_mode"]
    local["fleet_sync_report_date_from"] = current["report_date_from"]
    local["fleet_sync_report_date_to"] = current["report_date_to"]
    local.pop("fleet_sync_report_date", None)
    local["fleet_sync_force_request_at"] = current.get("force_request_at") or ""
    local["fleet_sync_updated_by"] = current.get("updated_by") or ""
    local["fleet_sync_updated_at"] = updated_at
    _save_json(_app_config_path(), local)

    if not is_configured():
        raise RuntimeError("Firebase is not set up. Configure it first (Who's online).")

    token = _ensure_id_token()
    payload = {
        "enabled": current["enabled"],
        "time": current["time"],
        "output_mode": current["output_mode"],
        "report_date_from": current["report_date_from"],
        "report_date_to": current["report_date_to"],
        "force_request_at": current.get("force_request_at"),
        "updated_by": current.get("updated_by"),
        "updated_at": updated_at,
        "firebase_uid": _firebase_uid(),
    }
    resp = requests.put(
        _fleet_sync_settings_url(),
        params={"auth": token},
        json=payload,
        timeout=15,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not save fleet sync settings"))
    return current


def request_fleet_force_sync(
    *,
    updated_by: str | None = None,
    report_date_from: str | None = None,
    report_date_to: str | None = None,
) -> dict[str, Any]:
    """Ask open tablets to upload immediately (next scheduler check), bypassing schedule time."""
    settings = get_fleet_sync_settings()
    stamp = datetime.now(timezone.utc).isoformat()
    return save_fleet_sync_settings(
        enabled=bool(settings.get("enabled")),
        sync_time=str(settings.get("time") or _FLEET_SYNC_DEFAULT_TIME),
        output_mode=str(settings.get("output_mode") or FLEET_OUTPUT_FULL_DB),
        report_date_from=report_date_from
        if report_date_from is not None
        else str(settings.get("report_date_from") or ""),
        report_date_to=report_date_to
        if report_date_to is not None
        else str(settings.get("report_date_to") or ""),
        force_request_at=stamp,
        updated_by=updated_by,
        preserve_force_request=False,
    )


def fleet_sync_status_text() -> str:
    settings = get_fleet_sync_settings()
    enabled = bool(settings.get("enabled"))
    sync_time = str(settings.get("time") or _FLEET_SYNC_DEFAULT_TIME)
    output = fleet_output_label(str(settings.get("output_mode") or ""))
    report_disp = format_fleet_report_range(
        str(settings.get("report_date_from") or ""),
        str(settings.get("report_date_to") or ""),
    )
    by = settings.get("updated_by")
    by_bit = f" (set by {by})" if by else ""
    if not is_configured():
        return "Firebase not set up — fleet backup unavailable."
    if not enabled:
        return (
            f"Off — would run around {sync_time} ({output}) if enabled{by_bit}. "
            f"Force date filter: {report_disp}."
        )
    return (
        f"On — tablets upload {output} around {sync_time}{by_bit}. "
        f"Force date filter: {report_disp}."
    )


def _session_payload_for_fleet(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for session in sessions:
        row = dict(session)
        ticket = row.get("picking_ticket")
        if ticket is not None and hasattr(ticket, "__dict__"):
            from app.pdf_parser import ticket_to_dict

            row["picking_ticket"] = ticket_to_dict(ticket)
        payload.append(row)
    return payload


def _prepare_fleet_backup_bytes(
    output_mode: str,
    *,
    username: str | None = None,
    report_date_from: str | date | None = None,
    report_date_to: str | date | None = None,
) -> tuple[bytes, str, str]:
    """Build backup bytes for the selected output mode.

    Returns (raw_bytes, filename, content_kind).
    ``report_date_from`` / ``report_date_to`` filter sessions for PDF/JSON.
    """
    import io
    import json
    import zipfile

    from app import database
    from app.history_export import export_report_pdf_bytes

    mode = normalize_fleet_output(output_mode)
    from_raw = (
        report_date_from.isoformat()
        if isinstance(report_date_from, date)
        else str(report_date_from or "")
    )
    to_raw = (
        report_date_to.isoformat()
        if isinstance(report_date_to, date)
        else str(report_date_to or "")
    )
    from_iso, to_iso = normalize_fleet_report_range(from_raw, to_raw)
    start = date.fromisoformat(from_iso)
    end = date.fromisoformat(to_iso)
    from_display = start.strftime("%d/%m/%Y")
    to_display = end.strftime("%d/%m/%Y")
    range_label = from_display if from_iso == to_iso else f"{from_display} – {to_display}"
    range_stamp = (
        start.strftime("%Y%m%d")
        if from_iso == to_iso
        else f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
    )
    tag = re.sub(r"[^\w\-]+", "_", (username or "auto").strip() or "auto")[:40] or "auto"
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    if mode == FLEET_OUTPUT_FULL_DB:
        db_path = get_data_dir() / "scanner.db"
        if not db_path.is_file():
            raise FileNotFoundError("scanner.db not found — nothing to back up.")
        raw = db_path.read_bytes()
        if not raw:
            raise ValueError("scanner.db is empty.")
        return raw, "scanner.db", mode

    rows = database.search_sessions(date_from=from_display, date_to=to_display)
    full_sessions = database.get_sessions_with_items([s["id"] for s in rows])

    if mode == FLEET_OUTPUT_SESSIONS_JSON:
        payload = {
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "report_date_from": from_iso,
            "report_date_to": to_iso,
            "filter_summary": f"Fleet backup — {range_label}",
            "session_count": len(full_sessions),
            "username": (username or "").strip() or None,
            "device_label": get_device_label(),
            "sessions": _session_payload_for_fleet(full_sessions),
        }
        raw = json.dumps(payload, indent=2, default=str).encode("utf-8")
        return raw, f"sessions_{range_stamp}_{tag}_{stamp}.json", mode

    if mode == FLEET_OUTPUT_PDF:
        if not full_sessions:
            raise FileNotFoundError(
                f"No sessions for {range_label} — nothing to export as PDF."
            )
        pdf_bytes = export_report_pdf_bytes(
            full_sessions,
            filter_summary=f"Fleet backup — {range_label} ({len(full_sessions)} session(s))",
        )
        return pdf_bytes, f"picking_report_{range_stamp}_{tag}_{stamp}.pdf", mode

    # db_and_pdf → ZIP
    db_path = get_data_dir() / "scanner.db"
    if not db_path.is_file():
        raise FileNotFoundError("scanner.db not found — nothing to back up.")
    db_bytes = db_path.read_bytes()
    if not db_bytes:
        raise ValueError("scanner.db is empty.")
    if full_sessions:
        pdf_bytes = export_report_pdf_bytes(
            full_sessions,
            filter_summary=f"Fleet backup — {range_label} ({len(full_sessions)} session(s))",
        )
    else:
        pdf_bytes = b""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("scanner.db", db_bytes)
        if pdf_bytes:
            zf.writestr(f"picking_report_{range_stamp}_{tag}_{stamp}.pdf", pdf_bytes)
        else:
            zf.writestr(
                "README.txt",
                f"No sessions for {range_label}; ZIP contains scanner.db only.\n",
            )
    return buf.getvalue(), f"fleet_backup_{range_stamp}_{tag}_{stamp}.zip", mode


def publish_device_scanner_backup(
    *,
    username: str | None = None,
    output_mode: str | None = None,
    report_date_from: str | date | None = None,
    report_date_to: str | date | None = None,
) -> dict[str, Any]:
    """Gzip + upload this device's backup payload to Firebase."""
    import base64
    import gzip
    import hashlib

    if not is_configured():
        raise RuntimeError("Firebase is not set up on this device.")

    settings = get_fleet_sync_settings()
    mode = normalize_fleet_output(
        output_mode or settings.get("output_mode")
    )
    from_raw = (
        report_date_from.isoformat()
        if isinstance(report_date_from, date)
        else (
            str(report_date_from)
            if report_date_from is not None
            else str(settings.get("report_date_from") or "")
        )
    )
    to_raw = (
        report_date_to.isoformat()
        if isinstance(report_date_to, date)
        else (
            str(report_date_to)
            if report_date_to is not None
            else str(settings.get("report_date_to") or "")
        )
    )
    from_iso, to_iso = normalize_fleet_report_range(from_raw, to_raw)
    raw, filename, kind = _prepare_fleet_backup_bytes(
        mode,
        username=username,
        report_date_from=from_iso,
        report_date_to=to_iso,
    )
    if len(raw) > _DEVICE_BACKUP_MAX_RAW_BYTES:
        raise ValueError(
            f"Backup is too large ({len(raw):,} bytes). "
            f"Keep under {_DEVICE_BACKUP_MAX_RAW_BYTES:,} bytes."
        )

    compressed = gzip.compress(raw, compresslevel=6)
    digest = hashlib.sha256(raw).hexdigest()
    device_id = get_device_id()
    updated_at = datetime.now(timezone.utc).isoformat()
    sync_date = to_iso
    meta = {
        "device_id": device_id,
        "device_label": get_device_label(),
        "username": (username or "").strip() or None,
        "filename": filename,
        "output_mode": kind,
        "byte_count": len(raw),
        "compressed_bytes": len(compressed),
        "sha256": digest,
        "sync_date": sync_date,
        "report_date": sync_date,
        "report_date_from": from_iso,
        "report_date_to": to_iso,
        "updated_at": updated_at,
        "encoding": "gzip+base64",
        "firebase_uid": _firebase_uid(),
    }
    content = {
        "encoding": "gzip+base64",
        "data": base64.b64encode(compressed).decode("ascii"),
        "sha256": digest,
        "byte_count": len(raw),
        "filename": filename,
        "output_mode": kind,
        "updated_at": updated_at,
        "sync_date": sync_date,
        "report_date": sync_date,
        "report_date_from": from_iso,
        "report_date_to": to_iso,
    }

    token = _ensure_id_token()
    resp = requests.put(
        _device_backup_content_url(device_id),
        params={"auth": token},
        json=content,
        timeout=90,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not upload device backup"))

    resp = requests.put(
        _device_backup_meta_url(device_id),
        params={"auth": token},
        json=meta,
        timeout=20,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not save device backup metadata"))
    return meta


def list_device_backup_metas() -> list[dict[str, Any]]:
    """Return metadata for every device backup currently in Firebase."""
    if not is_configured():
        return []
    token = _ensure_id_token()
    resp = requests.get(
        _device_backups_root_url(),
        params={"auth": token, "shallow": "true"},
        timeout=20,
    )
    if resp.status_code == 404:
        return []
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not list device backups"))
    raw = resp.json() or {}
    if not isinstance(raw, dict) or not raw:
        return []

    metas: list[dict[str, Any]] = []
    for device_id in raw.keys():
        cleaned = str(device_id or "").strip()
        if not cleaned:
            continue
        try:
            meta_resp = requests.get(
                _device_backup_meta_url(cleaned),
                params={"auth": token},
                timeout=15,
            )
            if meta_resp.status_code >= 400:
                continue
            meta = meta_resp.json() or {}
            if isinstance(meta, dict) and meta:
                meta.setdefault("device_id", cleaned)
                metas.append(meta)
        except Exception:
            continue

    metas.sort(key=lambda m: str(m.get("updated_at") or ""), reverse=True)
    return metas


def download_device_scanner_backup(device_id: str) -> tuple[bytes, dict[str, Any]]:
    """Download one device's scanner.db bytes from Firebase."""
    import base64
    import gzip

    cleaned = (device_id or "").strip()
    if not cleaned:
        raise ValueError("device_id is required.")
    if not is_configured():
        raise RuntimeError("Firebase is not set up on this device.")

    token = _ensure_id_token()
    meta_resp = requests.get(
        _device_backup_meta_url(cleaned),
        params={"auth": token},
        timeout=15,
    )
    if meta_resp.status_code >= 400:
        raise RuntimeError(_firebase_error(meta_resp, "Could not load backup metadata"))
    meta = meta_resp.json() or {}
    if not isinstance(meta, dict) or not meta:
        raise FileNotFoundError(f"No backup found for device {cleaned}.")

    resp = requests.get(
        _device_backup_content_url(cleaned),
        params={"auth": token},
        timeout=90,
    )
    if resp.status_code >= 400:
        raise RuntimeError(_firebase_error(resp, "Could not download scanner.db backup"))
    raw = resp.json() or {}
    if not isinstance(raw, dict):
        raise RuntimeError("Invalid backup payload from Firebase.")
    data = str(raw.get("data") or "").strip()
    if not data:
        raise RuntimeError("Backup payload is empty.")
    try:
        compressed = base64.b64decode(data.encode("ascii"), validate=False)
        file_bytes = gzip.decompress(compressed)
    except Exception as exc:
        raise RuntimeError(f"Could not decode scanner.db backup: {exc}") from exc
    if not file_bytes:
        raise RuntimeError("Decoded scanner.db backup is empty.")
    return file_bytes, meta
