"""Daily scheduled sync of today's scan sessions to cloud folder / Drive."""

from __future__ import annotations

import re
import threading
from datetime import date, datetime
from typing import Any, Callable

from app import cloud_sync
from app import database
from app.history_export import export_report_pdf_bytes

ProgressCallback = Callable[[str], None]

_CONFIG_KEY_ENABLED = "auto_sync_enabled"
_CONFIG_KEY_TIME = "auto_sync_time"  # "HH:MM" 24-hour
_CONFIG_KEY_LAST_DATE = "auto_sync_last_date"  # "YYYY-MM-DD"
_DEFAULT_TIME = "17:00"

_scheduler_stop = threading.Event()
_scheduler_thread: threading.Thread | None = None
_scheduler_lock = threading.Lock()


def _load_config() -> dict[str, Any]:
    return cloud_sync._load_app_config()


def _save_config(config: dict[str, Any]) -> None:
    cloud_sync._save_app_config(config)


def get_auto_sync_enabled() -> bool:
    return bool(_load_config().get(_CONFIG_KEY_ENABLED))


def get_auto_sync_time() -> str:
    raw = str(_load_config().get(_CONFIG_KEY_TIME) or _DEFAULT_TIME).strip()
    return normalize_time(raw) or _DEFAULT_TIME


def get_auto_sync_last_date() -> str:
    return str(_load_config().get(_CONFIG_KEY_LAST_DATE) or "").strip()


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


def set_auto_sync(*, enabled: bool, sync_time: str) -> str:
    """Save schedule. Returns normalized HH:MM. Raises ValueError on bad time."""
    normalized = normalize_time(sync_time)
    if not normalized:
        raise ValueError("Time must be HH:MM (24-hour), e.g. 17:00")
    config = _load_config()
    config[_CONFIG_KEY_ENABLED] = bool(enabled)
    config[_CONFIG_KEY_TIME] = normalized
    _save_config(config)
    return normalized


def mark_auto_sync_ran(day: date | None = None) -> None:
    config = _load_config()
    config[_CONFIG_KEY_LAST_DATE] = (day or date.today()).isoformat()
    _save_config(config)


def auto_sync_status_text() -> str:
    enabled = get_auto_sync_enabled()
    sync_time = get_auto_sync_time()
    last = get_auto_sync_last_date()
    if not enabled:
        return f"Off — would run around {sync_time} if enabled."
    if last == date.today().isoformat():
        return f"On — today's sync already ran (scheduled {sync_time})."
    return f"On — next sync around {sync_time} (app must be open)."


def resolve_auto_sync_provider() -> str | None:
    if cloud_sync.get_sync_folder() is not None:
        return cloud_sync.PROVIDER_FOLDER
    if cloud_sync.is_signed_in(cloud_sync.PROVIDER_GOOGLE):
        return cloud_sync.PROVIDER_GOOGLE
    if cloud_sync.is_signed_in(cloud_sync.PROVIDER_ONEDRIVE):
        return cloud_sync.PROVIDER_ONEDRIVE
    return None


def _checker_tag(username: str | None) -> str:
    raw = (username or "").strip() or "auto"
    safe = re.sub(r"[^\w\-]+", "_", raw, flags=re.UNICODE)
    return (safe.strip("_") or "auto")[:40]


def _session_payload(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for session in sessions:
        row = dict(session)
        ticket = row.get("picking_ticket")
        if ticket is not None and hasattr(ticket, "__dict__"):
            from app.pdf_parser import ticket_to_dict

            row["picking_ticket"] = ticket_to_dict(ticket)
        payload.append(row)
    return payload


def run_todays_sessions_sync(
    *,
    checker_username: str | None = None,
    on_progress: ProgressCallback | None = None,
    force: bool = False,
) -> cloud_sync.SyncResult | None:
    """Export and sync all sessions with check_date = today.

    Returns SyncResult on success, None if skipped (disabled / already ran /
    nothing to sync / no destination). Raises on hard failure.
    """
    if not force and not get_auto_sync_enabled():
        if on_progress:
            on_progress("Auto-sync is disabled.")
        return None

    today = date.today()
    if not force and get_auto_sync_last_date() == today.isoformat():
        if on_progress:
            on_progress("Today's scheduled sync already completed.")
        return None

    provider = resolve_auto_sync_provider()
    if provider is None:
        raise RuntimeError(
            "No sync destination. Choose a tablet/PC sync folder or sign in "
            "to Google Drive / OneDrive in Settings → Cloud Sync."
        )

    today_display = today.strftime("%d/%m/%Y")
    if on_progress:
        on_progress(f"Looking up sessions for {today_display}…")

    rows = database.search_sessions(date_from=today_display, date_to=today_display)
    full_sessions = database.get_sessions_with_items([s["id"] for s in rows])
    if not full_sessions:
        if on_progress:
            on_progress("No sessions for today — nothing to sync.")
        mark_auto_sync_ran(today)
        return None

    checker_tag = _checker_tag(checker_username)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    filter_summary = f"Auto sync — {today_display} ({len(full_sessions)} session(s))"

    if on_progress:
        on_progress(f"Building report for {len(full_sessions)} session(s)…")

    pdf_bytes = export_report_pdf_bytes(
        full_sessions,
        filter_summary=filter_summary,
    )
    files = cloud_sync.prepare_sync_files(
        pdf_bytes=pdf_bytes,
        pdf_name=f"picking_report_daily_{checker_tag}_{stamp}.pdf",
        backup_mode=cloud_sync.BACKUP_FILTERED,
        filtered_sessions=_session_payload(full_sessions),
        filter_summary=filter_summary,
        checker_tag=checker_tag,
    )

    root_name = None
    if provider == cloud_sync.PROVIDER_FOLDER:
        # Use existing folder if present; otherwise create default name.
        root_name = cloud_sync.cloud_root_folder_name(checker_tag)
        existing = cloud_sync.sync_root_path(
            checker_tag, root_folder_name=root_name
        )
        if existing is not None and existing.exists():
            root_name = existing.name

    if on_progress:
        on_progress(
            f"Uploading to {cloud_sync.PROVIDER_LABELS.get(provider, provider)}…"
        )

    result = cloud_sync.sync_files(
        provider,
        files,
        on_progress=on_progress,
        checker_tag=checker_tag,
        root_folder_name=root_name,
    )
    mark_auto_sync_ran(today)
    if on_progress:
        on_progress("Daily sync complete.")
    return result


def _should_run_now(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if get_auto_sync_last_date() == now.date().isoformat():
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
                if get_auto_sync_enabled() and _should_run_now():
                    username = None
                    if get_checker_username:
                        try:
                            username = get_checker_username()
                        except Exception:
                            username = None
                    notify("Starting scheduled daily sync…")
                    try:
                        run_todays_sessions_sync(
                            checker_username=username,
                            on_progress=notify,
                        )
                    except Exception as exc:
                        notify(f"Scheduled sync failed: {exc}")
            except Exception as exc:
                notify(f"Auto-sync scheduler error: {exc}")
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
