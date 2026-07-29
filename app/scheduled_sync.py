"""Daily scheduled upload of each tablet's scanner.db to Firebase (fleet sync).

Schedule is controlled from Top Pickers Monitor (Super Admin). Tablets pull
fleet_sync_settings and upload into the shared device_backups tree.
"""

from __future__ import annotations

import re
import threading
from datetime import date, datetime
from typing import Any, Callable

from app import cloud_sync

ProgressCallback = Callable[[str], None]

_CONFIG_KEY_LAST_DATE = "auto_sync_last_date"  # "YYYY-MM-DD"
_CONFIG_KEY_FORCE_HANDLED = "fleet_force_handled_at"
_DEFAULT_TIME = "17:00"

_scheduler_stop = threading.Event()
_scheduler_thread: threading.Thread | None = None
_scheduler_lock = threading.Lock()


def _load_config() -> dict[str, Any]:
    return cloud_sync._load_app_config()


def _save_config(config: dict[str, Any]) -> None:
    cloud_sync._save_app_config(config)


def normalize_time(value: str) -> str | None:
    """Accept HH:MM or H:MM; return zero-padded HH:MM or None."""
    text = (value or "").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _fleet_settings() -> dict[str, Any]:
    from app import firebase_presence

    try:
        return firebase_presence.get_fleet_sync_settings()
    except Exception:
        return {"enabled": False, "time": _DEFAULT_TIME}


def get_auto_sync_enabled() -> bool:
    """True when Monitor has enabled fleet daily backup."""
    return bool(_fleet_settings().get("enabled"))


def get_auto_sync_time() -> str:
    raw = str(_fleet_settings().get("time") or _DEFAULT_TIME).strip()
    return normalize_time(raw) or _DEFAULT_TIME


def get_auto_sync_last_date() -> str:
    return str(_load_config().get(_CONFIG_KEY_LAST_DATE) or "").strip()


def set_auto_sync(*, enabled: bool, sync_time: str) -> str:
    """Deprecated local setter — prefer Monitor fleet_sync_settings.

    Kept for compatibility; writes through to Firebase when configured.
    """
    from app import firebase_presence

    normalized = normalize_time(sync_time)
    if not normalized:
        raise ValueError("Time must be HH:MM (24-hour), e.g. 17:00")
    firebase_presence.save_fleet_sync_settings(
        enabled=bool(enabled),
        sync_time=normalized,
    )
    return normalized


def mark_auto_sync_ran(day: date | None = None) -> None:
    config = _load_config()
    config[_CONFIG_KEY_LAST_DATE] = (day or date.today()).isoformat()
    _save_config(config)


def auto_sync_status_text() -> str:
    from app import firebase_presence

    if firebase_presence.is_configured():
        base = firebase_presence.fleet_sync_status_text()
    else:
        base = "Firebase not set up — fleet backup unavailable."
    last = get_auto_sync_last_date()
    if last == date.today().isoformat():
        return f"{base} Today's upload already completed on this tablet."
    return base


def _pending_force_request() -> str | None:
    """Return force_request_at if tablets have not handled it yet."""
    settings = _fleet_settings()
    force_at = str(settings.get("force_request_at") or "").strip()
    if not force_at:
        return None
    handled = str(_load_config().get(_CONFIG_KEY_FORCE_HANDLED) or "").strip()
    if force_at == handled:
        return None
    return force_at


def mark_force_request_handled(force_at: str) -> None:
    config = _load_config()
    config[_CONFIG_KEY_FORCE_HANDLED] = (force_at or "").strip()
    _save_config(config)


def run_todays_sessions_sync(
    *,
    checker_username: str | None = None,
    on_progress: ProgressCallback | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    """Upload this tablet's backup to the shared Firebase fleet backup.

    Returns metadata dict on success, None if skipped (disabled / already ran).
    Raises on hard failure.
    """
    from app import firebase_presence

    force_at = _pending_force_request() if not force else None
    force_run = bool(force or force_at)

    if not force_run and not get_auto_sync_enabled():
        if on_progress:
            on_progress("Fleet daily backup is disabled (set in Monitor).")
        return None

    today = date.today()
    if not force_run and get_auto_sync_last_date() == today.isoformat():
        if on_progress:
            on_progress("Today's scheduled backup already completed.")
        return None

    if not firebase_presence.is_configured():
        raise RuntimeError(
            "Firebase is not set up. Configure it once (Who's online), "
            "then enable Fleet data sync in the Monitor app."
        )

    if on_progress:
        on_progress(
            "Force-uploading fleet backup to Firebase…"
            if force_run
            else "Uploading fleet backup to Firebase…"
        )

    settings = firebase_presence.get_fleet_sync_settings()
    # Scheduled runs always use the tablet's local today.
    # Force runs use the admin-selected report date range from Monitor.
    if force_run:
        report_from = str(settings.get("report_date_from") or "")
        report_to = str(settings.get("report_date_to") or "")
    else:
        today_iso = date.today().isoformat()
        report_from = today_iso
        report_to = today_iso

    meta = firebase_presence.publish_device_scanner_backup(
        username=checker_username,
        output_mode=str(settings.get("output_mode") or ""),
        report_date_from=report_from,
        report_date_to=report_to,
    )
    if not force_run:
        mark_auto_sync_ran(today)
    if force_at:
        mark_force_request_handled(force_at)
    elif force:
        # Monitor-side force: treat current request as handled if present.
        current_force = str(settings.get("force_request_at") or "").strip()
        if current_force:
            mark_force_request_handled(current_force)
    if on_progress:
        label = meta.get("device_label") or meta.get("device_id") or "device"
        name = meta.get("filename") or "backup"
        size = int(meta.get("byte_count") or 0)
        on_progress(
            f"Daily backup complete — {label} / {name} ({size:,} bytes) stored in Firebase."
        )
    return meta


def _should_run_now(now: datetime | None = None) -> bool:
    if _pending_force_request():
        return True
    now = now or datetime.now()
    if get_auto_sync_last_date() == now.date().isoformat():
        return False
    if not get_auto_sync_enabled():
        return False
    target = get_auto_sync_time()
    current = now.strftime("%H:%M")
    # Run in the matching minute (and catch up for a few minutes if the app
    # was briefly backgrounded around the set time).
    hour, minute = map(int, target.split(":"))
    target_minutes = hour * 60 + minute
    now_minutes = now.hour * 60 + now.minute
    return 0 <= (now_minutes - target_minutes) <= 5 and current >= target


def start_auto_sync_scheduler(
    *,
    get_checker_username: Callable[[], str | None] | None = None,
    on_status: ProgressCallback | None = None,
) -> None:
    """Start a background minute-check loop (idempotent)."""
    global _scheduler_thread

    def notify(message: str) -> None:
        if on_status:
            try:
                on_status(message)
            except Exception:
                pass

    def loop() -> None:
        # Stagger first check so UI can finish loading.
        if _scheduler_stop.wait(15):
            return
        while not _scheduler_stop.is_set():
            try:
                if get_auto_sync_enabled() or _pending_force_request():
                    if _should_run_now():
                        username = None
                        if get_checker_username:
                            try:
                                username = get_checker_username()
                            except Exception:
                                username = None
                        notify(
                            "Starting force fleet backup…"
                            if _pending_force_request()
                            else "Starting scheduled fleet backup…"
                        )
                        try:
                            run_todays_sessions_sync(
                                checker_username=username,
                                on_progress=notify,
                            )
                        except Exception as exc:
                            notify(f"Scheduled backup failed: {exc}")
            except Exception as exc:
                notify(f"Fleet backup scheduler error: {exc}")
            if _scheduler_stop.wait(30):
                break

    with _scheduler_lock:
        if _scheduler_thread is not None and _scheduler_thread.is_alive():
            return
        _scheduler_stop.clear()
        _scheduler_thread = threading.Thread(
            target=loop,
            name="auto-sync-scheduler",
            daemon=True,
        )
        _scheduler_thread.start()


def stop_auto_sync_scheduler() -> None:
    global _scheduler_thread
    _scheduler_stop.set()
    with _scheduler_lock:
        _scheduler_thread = None
