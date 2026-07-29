"""Google Analytics 4 (Firebase Analytics) via Measurement Protocol.

Sends warehouse events from tablets/PCs over HTTPS — no native Analytics SDK.
Configure ``ga_measurement_id`` (G-…) and ``ga_api_secret`` in firebase_config.json
(Super Admin → Settings → Firebase setup).
"""

from __future__ import annotations

import threading
from datetime import date
from typing import Any

import requests

from app.paths import get_data_dir

_GA_COLLECT = "https://www.google-analytics.com/mp/collect"
_LAST_ACTIVE_KEY = "ga_last_tablet_active_date"
_lock = threading.Lock()


def _load_app_config() -> dict[str, Any]:
    from app import firebase_presence

    path = get_data_dir() / "config.json"
    return firebase_presence._load_json(path)  # noqa: SLF001 — shared local config


def _save_app_config(data: dict[str, Any]) -> None:
    from app import firebase_presence

    path = get_data_dir() / "config.json"
    firebase_presence._save_json(path, data)  # noqa: SLF001


def is_configured() -> bool:
    from app import firebase_presence

    cfg = firebase_presence.resolve_config()
    return bool(cfg.get("ga_measurement_id") and cfg.get("ga_api_secret"))


def status_text() -> str:
    if not is_configured():
        return (
            "Google Analytics not configured — add Measurement ID (G-…) and "
            "API secret in Firebase setup (optional)."
        )
    from app import firebase_presence

    mid = firebase_presence.resolve_config().get("ga_measurement_id") or ""
    return f"Google Analytics ready ({mid})."


def _client_id() -> str:
    from app import firebase_presence

    return firebase_presence.get_device_id()


def send_event(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    user_id: str | None = None,
) -> bool:
    """POST one GA4 event. Returns True if accepted (HTTP 2xx/204). Never raises."""
    try:
        from app import firebase_presence

        cfg = firebase_presence.resolve_config()
        measurement_id = str(cfg.get("ga_measurement_id") or "").strip()
        api_secret = str(cfg.get("ga_api_secret") or "").strip()
        if not measurement_id or not api_secret:
            return False

        event_params: dict[str, Any] = dict(params or {})
        # Required for some standard reports / engagement metrics.
        event_params.setdefault("engagement_time_msec", 1)

        payload: dict[str, Any] = {
            "client_id": _client_id(),
            "events": [{"name": name, "params": event_params}],
        }
        cleaned_user = (user_id or "").strip()
        if cleaned_user:
            payload["user_id"] = cleaned_user[:256]

        resp = requests.post(
            _GA_COLLECT,
            params={"measurement_id": measurement_id, "api_secret": api_secret},
            json=payload,
            timeout=10,
        )
        return resp.status_code < 300
    except Exception:
        return False


def send_event_background(
    name: str,
    params: dict[str, Any] | None = None,
    *,
    user_id: str | None = None,
) -> None:
    def work() -> None:
        send_event(name, params, user_id=user_id)

    threading.Thread(target=work, name=f"ga-{name}", daemon=True).start()


def track_tablet_active(
    *,
    username: str | None = None,
    device_label: str | None = None,
    force: bool = False,
) -> None:
    """Count daily active tablets (once per local day per device unless force)."""
    if not is_configured():
        return
    today = date.today().isoformat()
    with _lock:
        cfg = _load_app_config()
        last = str(cfg.get(_LAST_ACTIVE_KEY) or "").strip()
        if not force and last == today:
            return
        cfg[_LAST_ACTIVE_KEY] = today
        _save_app_config(cfg)

    from app import firebase_presence

    send_event_background(
        "tablet_active",
        {
            "device_label": (device_label or firebase_presence.get_device_label() or "")[
                :100
            ],
            "app_version": str(firebase_presence._app_version()),  # noqa: SLF001
        },
        user_id=username,
    )
    # Helps GA4 treat the tablet as an active user/session that day.
    send_event_background(
        "session_start",
        {"device_label": (device_label or firebase_presence.get_device_label() or "")[:100]},
        user_id=username,
    )


def track_pick_completed(
    *,
    picker_name: str,
    sales_order_no: str = "",
    session_id: int | None = None,
    username: str | None = None,
) -> None:
    """Log one completed pickup for per-picker Analytics reports."""
    if not is_configured():
        return
    picker = (picker_name or "").strip() or "unknown"
    params: dict[str, Any] = {
        "picker_name": picker[:100],
        "sales_order_no": (sales_order_no or "").strip()[:100],
    }
    if session_id is not None:
        params["session_id"] = int(session_id)
    send_event_background(
        "pick_completed",
        params,
        user_id=username or picker,
    )
