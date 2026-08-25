"""Super Admin desktop monitor — top pickers graph with crown and prize message."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Any

import flet as ft

from app import auth
from app import barcode_catalog
from app import database
from app import firebase_presence
from app import scheduled_sync
from app.components import muted
from app.paths import get_data_dir, init_app_storage, logo_src
from app.theme import BG_MAIN, FONT_FAMILY, PRIMARY, TEXT

_REFRESH_SECONDS = 15
_REFRESH_OPTIONS = (10, 15, 30, 60)
_BAR_COLORS = ("#1E88E5", "#43A047", "#FB8C00", "#8E24AA", "#00897B", "#C62828")
_CROWN = "👑"


def _monitor_config_path() -> Path:
    return get_data_dir() / "config.json"


def _load_refresh_prefs() -> dict[str, Any]:
    path = _monitor_config_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    enabled = data.get("monitor_auto_refresh")
    if enabled is None:
        enabled = True
    seconds = int(data.get("monitor_refresh_seconds") or _REFRESH_SECONDS)
    if seconds not in _REFRESH_OPTIONS:
        seconds = _REFRESH_SECONDS
    sort_by = str(data.get("monitor_leaderboard_sort") or "picks").strip().lower()
    if sort_by not in {"picks", "lines"}:
        sort_by = "picks"
    return {"enabled": bool(enabled), "seconds": seconds, "sort_by": sort_by}


def _save_refresh_prefs(
    *,
    enabled: bool,
    seconds: int,
    sort_by: str | None = None,
) -> None:
    path = _monitor_config_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    data["monitor_auto_refresh"] = bool(enabled)
    data["monitor_refresh_seconds"] = (
        seconds if seconds in _REFRESH_OPTIONS else _REFRESH_SECONDS
    )
    if sort_by is not None:
        cleaned = str(sort_by).strip().lower()
        data["monitor_leaderboard_sort"] = (
            cleaned if cleaned in {"picks", "lines"} else "picks"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _normalize_leaderboard_sort(value: Any) -> str:
    """Map dropdown value/label to ``picks`` or ``lines``."""
    raw = str(value or "picks").strip().lower()
    if raw in {"lines", "line", "highest lines", "highest line"}:
        return "lines"
    if "line" in raw:
        return "lines"
    return "picks"


def _format_iso_range(start_iso: str, end_iso: str) -> str:
    try:
        start = date.fromisoformat(start_iso)
        end = date.fromisoformat(end_iso)
        return f"{start.strftime('%d/%m/%Y')} – {end.strftime('%d/%m/%Y')}"
    except Exception:
        return ""


def _ranked_pickers(
    rows: list[firebase_presence.UserFulfilmentRow],
    which: str,
    *,
    sort_by: str = "picks",
) -> list[tuple[str, int, int]]:
    """Return (picker_name, picks, lines) ordered by ``sort_by``.

    ``sort_by``:
      - ``picks`` — highest picks, then highest lines
      - ``lines`` — highest lines, then highest picks
    """
    period = firebase_presence.normalize_week_filter(which)
    ranked: list[tuple[str, int, int]] = []
    for row in rows:
        if period == "today":
            picks = int(row.today)
            lines = int(row.today_lines)
        elif period == "last":
            picks = int(row.last_week)
            lines = int(row.last_week_lines)
        elif period == "month":
            picks = int(row.month)
            lines = int(row.month_lines)
        elif period == "custom":
            picks = int(row.custom)
            lines = int(row.custom_lines)
        else:
            picks = int(row.week)
            lines = int(row.week_lines)
        if picks > 0 or lines > 0:
            ranked.append((row.picker_name, picks, lines))
    return _sort_ranked(ranked, sort_by)


def _sort_ranked(
    ranked: list[tuple[str, int, int]],
    sort_by: str,
) -> list[tuple[str, int, int]]:
    mode = _normalize_leaderboard_sort(sort_by)
    if mode == "lines":
        ranked.sort(key=lambda item: (-item[2], -item[1], item[0].lower()))
    else:
        ranked.sort(key=lambda item: (-item[1], -item[2], item[0].lower()))
    return ranked


def _rank_maps(
    picks: dict[str, int],
    lines: dict[str, int],
    sort_by: str,
) -> list[tuple[str, int, int]]:
    ranked: list[tuple[str, int, int]] = []
    for name in set(picks) | set(lines):
        p = int(picks.get(name, 0))
        l = int(lines.get(name, 0))
        if p > 0 or l > 0:
            ranked.append((name, p, l))
    return _sort_ranked(ranked, sort_by)


def _ranges_overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    return a_start <= b_end and b_start <= a_end


def _synthesize_from_week_buckets(
    rows: list[firebase_presence.UserFulfilmentRow],
    start: date,
    end: date,
    sort_by: str,
) -> list[tuple[str, int, int]]:
    """Build a ranking from today / this-week / last-week buckets that overlap ``start``..``end``.

    Used when tablets have not yet published month/custom/daily stats. Avoids
    double-counting today (subset of this week).
    """
    week_start, week_end = database.week_date_bounds("this")
    last_start, last_end = database.week_date_bounds("last")
    today = date.today()

    use_week = _ranges_overlap(start, end, week_start, week_end)
    use_last = _ranges_overlap(start, end, last_start, last_end)
    use_today_only = (start <= today <= end) and not use_week

    picks: dict[str, int] = {}
    lines: dict[str, int] = {}
    for row in rows:
        name = str(row.picker_name or "").strip()
        if not name:
            continue
        p = 0
        l = 0
        if use_week:
            p += int(row.week)
            l += int(row.week_lines)
        elif use_today_only:
            p += int(row.today)
            l += int(row.today_lines)
        if use_last:
            p += int(row.last_week)
            l += int(row.last_week_lines)
        if p > 0 or l > 0:
            picks[name] = p
            lines[name] = l
    return _rank_maps(picks, lines, sort_by)


def _rank_for_period(
    snap: dict[str, Any],
    which: str,
    *,
    sort_by: str = "picks",
    custom_from: str | None = None,
    custom_to: str | None = None,
) -> list[tuple[str, int, int]]:
    """Rank pickers for the selected period using the exact date bounds.

    Preference order:
      1. Summed daily heartbeat stats (exact From/To)
      2. Local DB sessions on this PC
      3. Dedicated month/custom/today fields when tablets publish them
      4. Synthesize from this-week + last-week + today buckets that overlap
    """
    period = firebase_presence.normalize_week_filter(which)
    start, end = database.period_date_bounds(
        period,
        custom_from=custom_from,
        custom_to=custom_to,
    )
    presence = list(snap.get("presence") or [])
    fulfilments = list(snap.get("fulfilments") or [])

    if period in {"today", "month", "custom"}:
        had_daily, picks, lines = firebase_presence.sum_daily_stats_for_range(
            presence, start, end
        )
        if had_daily and (picks or lines):
            return _rank_maps(picks, lines, sort_by)

        local_picks, local_lines = database.fulfilment_stats_by_picker_range(start, end)
        if local_picks or local_lines:
            return _rank_maps(local_picks, local_lines, sort_by)

        # Dedicated field from newer tablets (month / custom / today).
        dedicated = _ranked_pickers(fulfilments, period, sort_by=sort_by)
        if dedicated:
            return dedicated

        # Older tablets only send today/week/last_week — stitch those together.
        synthesized = _synthesize_from_week_buckets(
            fulfilments, start, end, sort_by
        )
        if synthesized:
            return synthesized
        return dedicated

    return _ranked_pickers(fulfilments, period, sort_by=sort_by)


_PERIOD_OPTIONS = (
    ("today", "Today"),
    ("this", "This week"),
    ("last", "Last week"),
    ("month", "This month"),
    ("custom", "Custom dates"),
)


def _format_display_date(value: date | None) -> str:
    if value is None:
        return ""
    return value.strftime("%d/%m/%Y")


def _parse_iso_or_display(value: str | None) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        pass
    parts = raw.replace("-", "/").split("/")
    if len(parts) == 3:
        try:
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            if year < 100:
                year += 2000
            return date(year, month, day)
        except ValueError:
            return None
    return None


def _online_pickers(
    presence: list[firebase_presence.PresenceEntry],
) -> list[dict[str, object]]:
    """Pickers with today's completed pickups on tablets that are currently online."""
    by_name: dict[str, dict[str, object]] = {}
    for entry in presence:
        if not entry.online:
            continue
        for name, count in (entry.stats_today or {}).items():
            try:
                qty = int(count)
            except (TypeError, ValueError):
                continue
            if qty <= 0:
                continue
            row = by_name.setdefault(
                name,
                {"picker_name": name, "today": 0, "today_lines": 0, "devices": []},
            )
            row["today"] = int(row["today"]) + qty
            devices = row["devices"]
            assert isinstance(devices, list)
            if entry.device_label and entry.device_label not in devices:
                devices.append(entry.device_label)
        for name, count in (entry.stats_today_lines or {}).items():
            try:
                qty = int(count)
            except (TypeError, ValueError):
                continue
            if qty <= 0:
                continue
            row = by_name.setdefault(
                name,
                {"picker_name": name, "today": 0, "today_lines": 0, "devices": []},
            )
            row["today_lines"] = int(row["today_lines"]) + qty
    rows = list(by_name.values())
    rows.sort(
        key=lambda item: (
            -int(item["today"]),
            -int(item.get("today_lines") or 0),
            str(item["picker_name"]).lower(),
        )
    )
    return rows


def _format_picks_lines(picks: int, lines: int) -> str:
    pick_bit = f"{picks} pick" if picks == 1 else f"{picks} picks"
    line_bit = f"{lines} line" if lines == 1 else f"{lines} lines"
    return f"{pick_bit} · {line_bit}"


def _metric_bar(*, value: int, max_value: int, color: str, height: int = 18) -> ft.Control:
    max_value = max(1, int(max_value))
    width_frac = max(0.06, min(1.0, int(value) / max_value)) if value > 0 else 0.0
    return ft.Container(
        content=ft.Container(
            bgcolor=color if value > 0 else "#ECEFF1",
            border_radius=5,
            height=height,
        ),
        bgcolor="#ECEFF1",
        border_radius=5,
        height=height,
        width=float(720 * width_frac) if value > 0 else 8,
    )


def _monitor_bar_chart(
    rows: list[tuple[str, int, int]],
    *,
    sort_by: str = "picks",
) -> ft.Control:
    """Leaderboard dual bars; order follows ``sort_by`` (picks or lines)."""
    if not rows:
        return muted("No pickups recorded for this period yet.")

    mode = _normalize_leaderboard_sort(sort_by)
    if mode == "lines":
        rows = sorted(rows, key=lambda item: (-item[2], -item[1], item[0].lower()))
    else:
        rows = sorted(rows, key=lambda item: (-item[1], -item[2], item[0].lower()))
    max_picks = max((picks for _, picks, _ in rows), default=0) or 1
    max_lines = max((lines for _, _, lines in rows), default=0) or 1

    bars: list[ft.Control] = []
    for index, (name, picks, lines) in enumerate(rows):
        is_top = index == 0
        picks_color = "#F9A825" if is_top else "#1E88E5"
        lines_color = "#FFB300" if is_top else "#43A047"
        name_row: list[ft.Control] = []
        if is_top:
            name_row.append(ft.Text(_CROWN, size=22, font_family=FONT_FAMILY))
        name_row.append(
            ft.Text(
                name,
                size=18 if is_top else 15,
                weight=ft.FontWeight.BOLD if is_top else ft.FontWeight.W_600,
                color="#F57F17" if is_top else TEXT,
                font_family=FONT_FAMILY,
                expand=True,
            )
        )
        name_row.append(
            ft.Text(
                _format_picks_lines(picks, lines),
                size=16 if is_top else 14,
                weight=ft.FontWeight.BOLD,
                color="#F57F17" if is_top else PRIMARY,
                font_family=FONT_FAMILY,
            )
        )
        bars.append(
            ft.Container(
                bgcolor="#FFF8E1" if is_top else ft.Colors.WHITE,
                border=ft.Border.all(2 if is_top else 1, "#FFD54F" if is_top else "#E0E0E0"),
                border_radius=10,
                padding=16,
                content=ft.Column(
                    [
                        ft.Row(
                            name_row,
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Row(
                            [
                                ft.Text(
                                    "Picks",
                                    size=12,
                                    width=48,
                                    color="#616161",
                                    font_family=FONT_FAMILY,
                                ),
                                _metric_bar(
                                    value=picks,
                                    max_value=max_picks,
                                    color=picks_color,
                                    height=22 if is_top else 16,
                                ),
                            ],
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Row(
                            [
                                ft.Text(
                                    "Lines",
                                    size=12,
                                    width=48,
                                    color="#616161",
                                    font_family=FONT_FAMILY,
                                ),
                                _metric_bar(
                                    value=lines,
                                    max_value=max_lines,
                                    color=lines_color,
                                    height=22 if is_top else 16,
                                ),
                            ],
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ],
                    spacing=8,
                    tight=True,
                ),
            )
        )
    return ft.Column(bars, spacing=12, tight=True)


async def main(page: ft.Page):
    await init_app_storage(page)
    database.init_db()
    auth.ensure_admins_file()
    auth.sync_with_cloud_background(force=True)
    try:
        barcode_catalog.sync_from_cloud_background()
    except Exception:
        pass

    page.title = "DEKS Top Pickers Monitor"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(font_family=FONT_FAMILY)
    page.bgcolor = BG_MAIN
    page.padding = 0
    page.window.width = 1100
    page.window.height = 800
    page.window.min_width = 900
    page.window.min_height = 640

    file_picker = ft.FilePicker()
    page.services.append(file_picker)

    session = {"username": None, "role": None, "view": "board"}
    body = ft.Container(expand=True, padding=28)
    page.add(body)

    def show_snack(message: str, *, error: bool = False):
        page.show_dialog(
            ft.SnackBar(
                content=ft.Text(message, color=ft.Colors.WHITE),
                bgcolor="#E53935" if error else "#323232",
                open=True,
                duration=ft.Duration(seconds=3),
            )
        )

    def stop_refresh():
        page._monitor_token = None
        callback = getattr(page, "_monitor_live_callback", None)
        if callback is not None:
            try:
                firebase_presence.stop_presence_listener(callback)
            except Exception:
                pass
            page._monitor_live_callback = None

    def build_login() -> ft.Control:
        username_field = ft.TextField(label="Username", autofocus=True, width=360)
        password_field = ft.TextField(
            label="Password",
            password=True,
            can_reveal_password=True,
            width=360,
            on_submit=lambda _: try_login(),
        )
        status = muted(
            "Sign in as Super Admin (full access) or Monitor Viewer (view only)."
        )

        def try_login(_=None):
            account = auth.authenticate(username_field.value or "", password_field.value or "")
            if not account:
                status.value = "Invalid username or password."
                page.update()
                return
            if not auth.can_access_monitor(account.role):
                status.value = (
                    "Only Super Admin or Monitor Viewer can use this monitor app."
                )
                page.update()
                return
            session["username"] = account.username
            session["role"] = account.role
            session["view"] = "board"
            try:
                auth.save_persisted_session(account.username, account.role)
            except Exception:
                pass
            show_shell()

        return ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            content=ft.Container(
                width=480,
                bgcolor=ft.Colors.WHITE,
                border_radius=12,
                padding=32,
                content=ft.Column(
                    [
                        ft.Image(src=logo_src(), width=220, fit=ft.BoxFit.CONTAIN),
                        ft.Text(
                            "Top Pickers Monitor",
                            size=26,
                            weight=ft.FontWeight.BOLD,
                            font_family=FONT_FAMILY,
                        ),
                        muted(
                            "Desktop board for weekly pickup rankings. "
                            "Monitor Viewers can watch only; Super Admin can change settings "
                            "and add users."
                        ),
                        username_field,
                        password_field,
                        status,
                        ft.ElevatedButton(
                            "Sign in",
                            bgcolor=PRIMARY,
                            color=ft.Colors.WHITE,
                            height=48,
                            on_click=try_login,
                        ),
                    ],
                    spacing=14,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ),
        )

    def show_login():
        stop_refresh()
        body.content = build_login()
        page.update()

    def show_shell():
        stop_refresh()
        admin_name = session["username"] or ""
        admin_role = session.get("role") or ""
        can_edit = auth.can_manage_monitor_settings(admin_role)
        content_host = ft.Container(expand=True)

        title = ft.Text(
            "Top Pickers",
            size=28,
            weight=ft.FontWeight.BOLD,
            font_family=FONT_FAMILY,
        )
        status_label = muted("Loading…")

        # --- Board controls ---
        range_label = muted("Period: —")
        prize_banner = ft.Container(visible=False)
        chart_host = ft.Column(spacing=12, tight=True)
        online_pickers_list = ft.Column(spacing=8, tight=True)
        top_label = ft.Text("", size=20, weight=ft.FontWeight.W_600, font_family=FONT_FAMILY)
        week_dropdown = ft.Dropdown(
            label="Period",
            width=220,
            value="this",
            options=[
                ft.DropdownOption(key=key, text=label)
                for key, label in _PERIOD_OPTIONS
            ],
        )
        custom_from_field = ft.TextField(
            label="From",
            width=150,
            dense=True,
            read_only=True,
            hint_text="dd/mm/yyyy",
            visible=False,
        )
        custom_to_field = ft.TextField(
            label="To",
            width=150,
            dense=True,
            read_only=True,
            hint_text="dd/mm/yyyy",
            visible=False,
        )
        custom_apply_btn = ft.OutlinedButton(
            "Apply dates",
            icon=ft.Icons.DATE_RANGE,
            visible=False,
        )
        custom_dates_row = ft.Row(
            [
                custom_from_field,
                custom_to_field,
                custom_apply_btn,
            ],
            spacing=10,
            wrap=True,
            visible=False,
        )

        # --- Settings controls ---
        prize_field = ft.TextField(
            label="Prize message (optional)",
            hint_text="e.g. Top picker wins a $50 voucher this week",
            multiline=True,
            min_lines=3,
            max_lines=4,
            width=560,
        )
        settings_week_dropdown = ft.Dropdown(
            label="Period",
            width=220,
            value="this",
            options=[
                ft.DropdownOption(key=key, text=label)
                for key, label in _PERIOD_OPTIONS
            ],
        )
        settings_status = muted("")
        barcode_status = muted(barcode_catalog.catalog_status_text())
        barcode_cloud_status = muted("")
        _fleet = firebase_presence.get_fleet_sync_settings()
        fleet_enabled_switch = ft.Switch(
            label="Enable daily backup for all tablets",
            value=bool(_fleet.get("enabled")),
        )
        fleet_time_label = muted(
            f"Scheduled time: {scheduled_sync.normalize_time(str(_fleet.get('time') or '17:00')) or '17:00'}"
        )
        fleet_output_dropdown = ft.Dropdown(
            label="Backup file type",
            width=360,
            value=firebase_presence.normalize_fleet_output(
                str(_fleet.get("output_mode") or "")
            ),
            options=[
                ft.DropdownOption(key=key, text=label)
                for key, label in firebase_presence.FLEET_OUTPUT_LABELS.items()
            ],
        )
        fleet_date_label = muted(
            "Report date: "
            + firebase_presence.format_fleet_report_range(
                str(_fleet.get("report_date_from") or ""),
                str(_fleet.get("report_date_to") or ""),
            )
        )
        fleet_folder_label = muted(
            f"Save downloads to: {_fleet.get('download_folder') or firebase_presence.get_fleet_download_folder()}"
        )
        fleet_status = muted(firebase_presence.fleet_sync_status_text())
        fleet_backup_list = ft.Column(spacing=6, tight=True)

        refresh_prefs = _load_refresh_prefs()
        filter_state = {
            "value": "this",
            "prize": "",
            "sort_by": str(refresh_prefs.get("sort_by") or "picks"),
            "custom_from": "",
            "custom_to": "",
        }
        refresh_state = {
            "enabled": bool(refresh_prefs["enabled"]),
            "seconds": int(refresh_prefs["seconds"]),
            "last_at": None,
        }
        latest_snap: dict[str, Any] = {"value": None}
        refresh_token = time.time()
        page._monitor_token = refresh_token
        refresh_wake = threading.Event()

        def bump_refresh() -> None:
            """Wake the auto-refresh loop so the new interval/toggle applies now."""
            refresh_wake.set()

        auto_refresh_switch = ft.Switch(
            label="Auto refresh",
            value=bool(refresh_state["enabled"]),
        )
        refresh_interval_dropdown = ft.Dropdown(
            label="Every",
            width=120,
            dense=True,
            value=str(refresh_state["seconds"]),
            options=[
                ft.DropdownOption(key=str(sec), text=f"{sec}s")
                for sec in _REFRESH_OPTIONS
            ],
        )
        last_refresh_label = muted("Last updated: —")
        leaderboard_sort_dropdown = ft.Dropdown(
            label="Order by",
            width=200,
            value=str(filter_state.get("sort_by") or "picks"),
            options=[
                ft.DropdownOption(key="picks", text="Highest picks"),
                ft.DropdownOption(key="lines", text="Highest lines"),
            ],
        )

        def render_prize_banner(message: str, top_name: str | None) -> None:
            text = (message or "").strip()
            if not text:
                prize_banner.visible = False
                prize_banner.content = None
                return
            who = top_name or "the top picker"
            prize_banner.visible = True
            prize_banner.content = ft.Container(
                bgcolor="#FFF8E1",
                border=ft.Border.all(1, "#FFD54F"),
                border_radius=10,
                padding=16,
                content=ft.Row(
                    [
                        ft.Text(_CROWN, size=28),
                        ft.Column(
                            [
                                ft.Text(
                                    f"Prize for {who}",
                                    weight=ft.FontWeight.W_600,
                                    font_family=FONT_FAMILY,
                                    size=16,
                                ),
                                ft.Text(text, size=15, font_family=FONT_FAMILY, color=TEXT),
                            ],
                            spacing=4,
                            tight=True,
                            expand=True,
                        ),
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        def render_online_pickers(presence: list[firebase_presence.PresenceEntry]) -> None:
            online_pickers_list.controls.clear()
            rows = _online_pickers(presence)
            if not rows:
                online_pickers_list.controls.append(
                    muted("No pickers with today’s pickups on online tablets right now.")
                )
                return
            for row in rows:
                devices = row.get("devices") or []
                device_bit = (
                    f" · {', '.join(str(d) for d in devices)}" if devices else ""
                )
                online_pickers_list.controls.append(
                    ft.Container(
                        bgcolor="#F1F8E9",
                        border=ft.Border.all(1, "#C8E6C9"),
                        border_radius=8,
                        padding=12,
                        content=ft.Row(
                            [
                                ft.Icon(ft.Icons.CIRCLE, color="#2E7D32", size=12),
                                ft.Column(
                                    [
                                        ft.Text(
                                            str(row["picker_name"]),
                                            weight=ft.FontWeight.W_600,
                                            font_family=FONT_FAMILY,
                                        ),
                                        muted(
                                            f"Today: {_format_picks_lines(int(row['today']), int(row.get('today_lines') or 0))}"
                                            f"{device_bit}"
                                        ),
                                    ],
                                    spacing=2,
                                    tight=True,
                                    expand=True,
                                ),
                            ],
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    )
                )

        def sync_custom_date_controls(*, which: str | None = None) -> None:
            period = firebase_presence.normalize_week_filter(
                which or filter_state.get("value") or "this"
            )
            show_custom = period == "custom"
            custom_dates_row.visible = show_custom
            custom_from_field.visible = show_custom
            custom_to_field.visible = show_custom
            custom_apply_btn.visible = show_custom
            from_iso = str(filter_state.get("custom_from") or "")
            to_iso = str(filter_state.get("custom_to") or "")
            custom_from_field.value = _format_display_date(_parse_iso_or_display(from_iso))
            custom_to_field.value = _format_display_date(_parse_iso_or_display(to_iso))

        def apply_snapshot(snap: dict) -> None:
            latest_snap["value"] = snap
            # Super Admin follows shared Firebase period; Monitor Viewer keeps
            # their own local period choice (auto-refresh must not reset it).
            if can_edit:
                which = firebase_presence.normalize_week_filter(
                    snap.get("week_filter") or filter_state["value"] or "this"
                )
                filter_state["custom_from"] = str(
                    snap.get("custom_date_from")
                    or filter_state.get("custom_from")
                    or ""
                )
                filter_state["custom_to"] = str(
                    snap.get("custom_date_to")
                    or filter_state.get("custom_to")
                    or ""
                )
            else:
                which = firebase_presence.normalize_week_filter(
                    filter_state.get("value") or snap.get("week_filter") or "this"
                )
                if not filter_state.get("custom_from"):
                    filter_state["custom_from"] = str(
                        snap.get("custom_date_from") or ""
                    )
                if not filter_state.get("custom_to"):
                    filter_state["custom_to"] = str(
                        snap.get("custom_date_to") or ""
                    )
            prize = str(snap.get("prize_message") or "")
            # Dropdown is source of truth so Order by always matches the graph.
            sort_by = _normalize_leaderboard_sort(
                leaderboard_sort_dropdown.value or filter_state.get("sort_by")
            )
            filter_state["value"] = which
            filter_state["prize"] = prize
            filter_state["sort_by"] = sort_by
            if week_dropdown.value != which:
                week_dropdown.value = which
            if settings_week_dropdown.value != which:
                settings_week_dropdown.value = which
            if leaderboard_sort_dropdown.value != sort_by:
                leaderboard_sort_dropdown.value = sort_by
            if prize_field.value != prize:
                prize_field.value = prize
            sync_custom_date_controls(which=which)

            try:
                start, end = database.period_date_bounds(
                    which,
                    custom_from=filter_state.get("custom_from") or None,
                    custom_to=filter_state.get("custom_to") or None,
                )
                range_text = _format_iso_range(start.isoformat(), end.isoformat())
            except Exception:
                range_text = _format_iso_range(
                    str(snap.get("week_start") or ""),
                    str(snap.get("week_end") or ""),
                )
            label = firebase_presence.period_label(which)
            range_label.value = f"{label}: {range_text}" if range_text else label

            ranked = _rank_for_period(
                snap,
                which,
                sort_by=sort_by,
                custom_from=str(filter_state.get("custom_from") or "") or None,
                custom_to=str(filter_state.get("custom_to") or "") or None,
            )
            chart_host.controls = [
                _monitor_bar_chart(ranked[:12], sort_by=sort_by)
            ]
            if ranked:
                top_name, top_picks, top_lines = ranked[0]
                metric = "lines" if sort_by == "lines" else "picks"
                top_label.value = (
                    f"{_CROWN}  #1 by {metric}: {top_name}  —  "
                    f"{_format_picks_lines(top_picks, top_lines)}"
                )
                render_prize_banner(prize, top_name)
            else:
                top_label.value = "No pickups yet for this period"
                render_prize_banner(prize, None)

            render_online_pickers(list(snap.get("presence") or []))

            now = datetime.now().strftime("%H:%M:%S")
            refresh_state["last_at"] = now
            interval = int(refresh_state["seconds"])
            live = bool(
                firebase_presence.presence_listener_active()
                or snap.get("source") == "firebase-live"
            )
            if live:
                last_refresh_label.value = f"Live · updated {now}"
            elif refresh_state["enabled"]:
                last_refresh_label.value = (
                    f"Last updated: {now} · backup poll every {interval}s"
                )
            else:
                last_refresh_label.value = (
                    f"Last updated: {now} · live off / auto refresh off"
                )

            if snap.get("configured"):
                access = (
                    "full access"
                    if can_edit
                    else "view only"
                )
                status_label.value = (
                    f"Signed in as {admin_name} ({access}) · Firebase live · "
                    f"{snap.get('device_count', 0)} device(s) · "
                    f"online {snap.get('online_count', 0)}"
                )
            else:
                status_label.value = (
                    f"Signed in as {admin_name} · showing this PC only "
                    "(configure Firebase in the main app for all tablets)."
                )

        def refresh_now(_=None):
            def work():
                try:
                    if firebase_presence.is_configured():
                        try:
                            firebase_presence.publish_heartbeat_async(
                                username=admin_name,
                                role=admin_role,
                                online=True,
                            )
                        except Exception:
                            pass
                    snap = firebase_presence.dashboard_snapshot(force_refresh=True)

                    def apply():
                        if getattr(page, "_monitor_token", None) != refresh_token:
                            return
                        apply_snapshot(snap)
                        page.update()

                    apply()
                except Exception as exc:
                    status_label.value = f"Refresh failed: {exc}"
                    try:
                        page.update()
                    except Exception:
                        pass

            page.run_thread(work)

        def persist_refresh_prefs() -> None:
            try:
                _save_refresh_prefs(
                    enabled=bool(refresh_state["enabled"]),
                    seconds=int(refresh_state["seconds"]),
                    sort_by=str(filter_state.get("sort_by") or "picks"),
                )
            except Exception:
                pass

        def on_leaderboard_sort_change(e=None):
            chosen = _normalize_leaderboard_sort(
                getattr(e.control, "value", None)
                if e is not None
                else leaderboard_sort_dropdown.value
            )
            filter_state["sort_by"] = chosen
            leaderboard_sort_dropdown.value = chosen
            persist_refresh_prefs()
            snap = latest_snap.get("value")
            try:
                if isinstance(snap, dict):
                    apply_snapshot(snap)
                # Keep existing chart if snapshot is not ready yet — do not clear it.
            except Exception as exc:
                status_label.value = f"Could not reorder leaderboard: {exc}"
            show_snack(
                "Leaderboard ordered by highest lines."
                if chosen == "lines"
                else "Leaderboard ordered by highest picks."
            )
            page.update()

        leaderboard_sort_dropdown.on_select = on_leaderboard_sort_change

        def on_auto_refresh_toggle(e):
            refresh_state["enabled"] = bool(e.control.value)
            persist_refresh_prefs()
            interval = int(refresh_state["seconds"])
            stamp = refresh_state.get("last_at") or "—"
            if refresh_state["enabled"]:
                last_refresh_label.value = (
                    f"Last updated: {stamp} · auto every {interval}s"
                )
                show_snack(f"Auto refresh on — every {interval}s.")
                bump_refresh()
            else:
                last_refresh_label.value = (
                    f"Last updated: {stamp} · auto refresh off"
                )
                show_snack("Auto refresh off — use Refresh for latest data.")
            page.update()

        def on_refresh_interval_change(e):
            try:
                seconds = int((e.control.value or str(_REFRESH_SECONDS)).strip())
            except ValueError:
                seconds = _REFRESH_SECONDS
            if seconds not in _REFRESH_OPTIONS:
                seconds = _REFRESH_SECONDS
            refresh_state["seconds"] = seconds
            refresh_interval_dropdown.value = str(seconds)
            persist_refresh_prefs()
            stamp = refresh_state.get("last_at") or "—"
            if refresh_state["enabled"]:
                last_refresh_label.value = (
                    f"Last updated: {stamp} · auto every {seconds}s"
                )
            show_snack(f"Auto refresh interval set to {seconds}s.")
            bump_refresh()
            page.update()

        auto_refresh_switch.on_change = on_auto_refresh_toggle
        refresh_interval_dropdown.on_select = on_refresh_interval_change

        def save_week_filter(
            chosen: str,
            *,
            custom_from: str | None = None,
            custom_to: str | None = None,
        ):
            chosen = firebase_presence.normalize_week_filter(chosen)
            week_dropdown.value = chosen
            settings_week_dropdown.value = chosen
            filter_state["value"] = chosen
            if custom_from is not None:
                filter_state["custom_from"] = (
                    _parse_iso_or_display(custom_from).isoformat()
                    if _parse_iso_or_display(custom_from)
                    else ""
                )
            if custom_to is not None:
                filter_state["custom_to"] = (
                    _parse_iso_or_display(custom_to).isoformat()
                    if _parse_iso_or_display(custom_to)
                    else ""
                )
            if chosen == "custom" and not (
                filter_state.get("custom_from") and filter_state.get("custom_to")
            ):
                today_iso = date.today().isoformat()
                filter_state["custom_from"] = filter_state.get("custom_from") or today_iso
                filter_state["custom_to"] = filter_state.get("custom_to") or today_iso
            sync_custom_date_controls(which=chosen)

            def apply_local_preview() -> None:
                snap = latest_snap.get("value")
                if not isinstance(snap, dict):
                    return
                start, end = database.period_date_bounds(
                    chosen,
                    custom_from=filter_state.get("custom_from") or None,
                    custom_to=filter_state.get("custom_to") or None,
                )
                preview = dict(snap)
                preview["week_filter"] = chosen
                preview["week_start"] = start.isoformat()
                preview["week_end"] = end.isoformat()
                preview["custom_date_from"] = filter_state.get("custom_from") or ""
                preview["custom_date_to"] = filter_state.get("custom_to") or ""
                apply_snapshot(preview)

            # Monitor Viewer: local view only — do not change shared Firebase period.
            if not can_edit:
                try:
                    apply_local_preview()
                    show_snack(
                        f"Showing {firebase_presence.period_label(chosen).lower()} "
                        "(this screen only)."
                    )
                except Exception as exc:
                    show_snack(f"Could not change period: {exc}", error=True)
                page.update()
                return

            # Instant UI while Super Admin save runs.
            try:
                apply_local_preview()
                page.update()
            except Exception:
                pass

            def work():
                try:
                    firebase_presence.save_dashboard_settings(
                        week_filter=chosen,
                        custom_date_from=str(filter_state.get("custom_from") or ""),
                        custom_date_to=str(filter_state.get("custom_to") or ""),
                        updated_by=admin_name,
                    )
                    snap = firebase_presence.dashboard_snapshot()

                    def apply():
                        apply_snapshot(snap)
                        show_snack(
                            f"Showing {firebase_presence.period_label(chosen).lower()}."
                        )
                        page.update()
                        bump_refresh()

                    apply()
                except Exception as exc:
                    show_snack(f"Could not save period filter: {exc}", error=True)
                    try:
                        page.update()
                    except Exception:
                        pass

            page.run_thread(work)

        def on_week_change(e):
            save_week_filter(
                getattr(e.control, "value", None) or week_dropdown.value or "this"
            )

        def on_settings_week_change(e):
            save_week_filter(
                getattr(e.control, "value", None)
                or settings_week_dropdown.value
                or "this"
            )

        def _open_custom_date_picker(*, which: str):
            field = custom_from_field if which == "from" else custom_to_field

            def _end_of_today() -> datetime:
                return datetime.combine(date.today(), datetime.max.time())

            picker = ft.DatePicker(
                help_text="Select date",
                entry_mode=ft.DatePickerEntryMode.CALENDAR,
                locale=ft.Locale("en", "AU"),
                first_date=date(2020, 1, 1),
                last_date=_end_of_today(),
                current_date=date.today(),
            )
            parsed = _parse_iso_or_display(field.value)
            picker.value = datetime.combine(
                parsed or date.today(),
                datetime.min.time(),
            )

            def on_picked(_=None):
                selected = picker.value
                if isinstance(selected, datetime):
                    selected = selected.date()
                if isinstance(selected, date):
                    field.value = _format_display_date(selected)
                page.pop_dialog()
                # Re-rank as soon as both ends are set.
                if (custom_from_field.value or "").strip() and (
                    custom_to_field.value or ""
                ).strip():
                    apply_custom_dates()
                else:
                    page.update()

            picker.on_change = on_picked
            page.show_dialog(picker)

        custom_from_field.on_click = lambda _: _open_custom_date_picker(which="from")
        custom_to_field.on_click = lambda _: _open_custom_date_picker(which="to")

        def apply_custom_dates(_=None):
            save_week_filter(
                "custom",
                custom_from=custom_from_field.value or "",
                custom_to=custom_to_field.value or "",
            )

        custom_apply_btn.on_click = apply_custom_dates

        def save_prize(_=None):
            if not can_edit:
                show_snack("View-only accounts cannot change settings.", error=True)
                return

            def work():
                try:
                    firebase_presence.save_dashboard_settings(
                        prize_message=prize_field.value or "",
                        updated_by=admin_name,
                    )
                    snap = firebase_presence.dashboard_snapshot()

                    def apply():
                        apply_snapshot(snap)
                        settings_status.value = "Prize message saved."
                        show_snack("Prize message saved.")
                        page.update()

                    apply()
                except Exception as exc:
                    show_snack(f"Could not save prize message: {exc}", error=True)

            page.run_thread(work)

        def clear_prize(_=None):
            if not can_edit:
                show_snack("View-only accounts cannot change settings.", error=True)
                return
            prize_field.value = ""
            page.update()
            save_prize()

        def logout(_=None):
            stop_refresh()
            session["username"] = None
            session["role"] = None
            try:
                auth.clear_persisted_session()
            except Exception:
                pass
            show_login()

        def open_board(_=None):
            session["view"] = "board"
            render_view()

        def open_settings(_=None):
            if not can_edit:
                show_snack("View-only accounts cannot open Settings.", error=True)
                return
            session["view"] = "settings"
            settings_status.value = (
                "Prize message is optional. Leave blank to hide it on the board and tablets."
            )
            prize_field.value = filter_state.get("prize") or ""
            settings_week_dropdown.value = filter_state.get("value") or "this"
            barcode_status.value = barcode_catalog.catalog_status_text()
            stamp = barcode_catalog._load_cloud_stamp()
            if stamp.get("updated_at"):
                barcode_cloud_status.value = (
                    f"Last published: {stamp.get('updated_at')} "
                    f"by {stamp.get('updated_by') or '—'}"
                )
            else:
                barcode_cloud_status.value = (
                    "Not published yet — upload an Excel file to sync tablets."
                )
            try:
                auth.sync_with_cloud(force=False)
            except Exception:
                pass
            render_view()

        def open_add_user_dialog(_=None):
            if not can_edit:
                show_snack("Only Super Admin can add users.", error=True)
                return
            username_field = ft.TextField(label="Username", autofocus=True)
            password_field = ft.TextField(
                label="Password",
                password=True,
                can_reveal_password=True,
            )
            role_field = ft.Dropdown(
                label="Role",
                value=auth.ROLE_PICKER,
                options=[
                    ft.dropdown.Option(auth.ROLE_PICKER, "Picker"),
                    ft.dropdown.Option(auth.ROLE_ADMIN, "Admin"),
                    ft.dropdown.Option(auth.ROLE_MONITOR_VIEWER, "Monitor Viewer"),
                ],
                width=320,
            )

            def close_dialog(_=None):
                page.pop_dialog()

            def submit_create(_=None):
                try:
                    account = auth.create_user(
                        admin_name,
                        (username_field.value or "").strip(),
                        password_field.value or "",
                        role=role_field.value or auth.ROLE_PICKER,
                    )
                    page.pop_dialog()
                    show_snack(
                        f"User added — {account.username} ({account.role_label}). "
                        "Synced to Firebase for all tablets."
                    )
                    render_view()
                except RuntimeError as exc:
                    page.pop_dialog()
                    show_snack(str(exc), error=True)
                    render_view()
                except (ValueError, PermissionError) as exc:
                    show_snack(str(exc), error=True)

            password_field.on_submit = submit_create
            page.show_dialog(
                ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Add User / Picker"),
                    content=ft.Column(
                        [
                            muted(
                                "Pickers appear in the New Scan picker list on all tablets."
                            ),
                            username_field,
                            password_field,
                            role_field,
                        ],
                        tight=True,
                        spacing=12,
                        width=320,
                    ),
                    actions=[
                        ft.TextButton("Cancel", on_click=close_dialog),
                        ft.TextButton("Add", on_click=submit_create),
                    ],
                )
            )

        def open_set_password_dialog(target_username: str):
            new_field = ft.TextField(
                label="New password",
                password=True,
                can_reveal_password=True,
                autofocus=True,
            )
            confirm_field = ft.TextField(
                label="Confirm new password",
                password=True,
                can_reveal_password=True,
            )

            def close_dialog(_=None):
                page.pop_dialog()

            def submit_set(_=None):
                if (new_field.value or "") != (confirm_field.value or ""):
                    show_snack("Passwords do not match.", error=True)
                    return
                try:
                    auth.set_user_password(
                        admin_name, target_username, new_field.value or ""
                    )
                    page.pop_dialog()
                    show_snack(f"Password set for {target_username}.")
                except (ValueError, PermissionError) as exc:
                    show_snack(str(exc), error=True)

            confirm_field.on_submit = submit_set
            page.show_dialog(
                ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Set Password"),
                    content=ft.Column(
                        [
                            muted(f"Set the password for {target_username}."),
                            new_field,
                            confirm_field,
                        ],
                        tight=True,
                        spacing=12,
                        width=320,
                    ),
                    actions=[
                        ft.TextButton("Cancel", on_click=close_dialog),
                        ft.TextButton("Save", on_click=submit_set),
                    ],
                )
            )

        def open_set_role_dialog(target_username: str, current_role: str):
            if current_role == auth.ROLE_SUPER_ADMIN:
                show_snack("Super Admin role cannot be changed here.", error=True)
                return
            role_field = ft.Dropdown(
                label="Role",
                value=current_role if current_role != "checker" else auth.ROLE_PICKER,
                options=[
                    ft.dropdown.Option(auth.ROLE_PICKER, "Picker"),
                    ft.dropdown.Option(auth.ROLE_ADMIN, "Admin"),
                    ft.dropdown.Option(auth.ROLE_MONITOR_VIEWER, "Monitor Viewer"),
                ],
                width=320,
            )

            def close_dialog(_=None):
                page.pop_dialog()

            def submit_role(_=None):
                try:
                    account = auth.set_user_role(
                        admin_name,
                        target_username,
                        role_field.value or auth.ROLE_PICKER,
                    )
                    page.pop_dialog()
                    show_snack(
                        f"Role updated — {account.username} is now {account.role_label}."
                    )
                    render_view()
                except (ValueError, PermissionError) as exc:
                    show_snack(str(exc), error=True)

            page.show_dialog(
                ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Change Role"),
                    content=ft.Column(
                        [muted(f"Change role for {target_username}."), role_field],
                        tight=True,
                        spacing=12,
                        width=320,
                    ),
                    actions=[
                        ft.TextButton("Cancel", on_click=close_dialog),
                        ft.TextButton("Save", on_click=submit_role),
                    ],
                )
            )

        def confirm_delete_user(target_username: str):
            def close_dialog(_=None):
                page.pop_dialog()

            def submit_delete(_=None):
                try:
                    auth.delete_admin(admin_name, target_username)
                    page.pop_dialog()
                    show_snack(f"Deleted user — {target_username}.")
                    render_view()
                except (ValueError, PermissionError) as exc:
                    show_snack(str(exc), error=True)

            page.show_dialog(
                ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Delete User"),
                    content=ft.Text(
                        f"Delete the account '{target_username}'? This cannot be undone.",
                        font_family=FONT_FAMILY,
                    ),
                    actions=[
                        ft.TextButton("Cancel", on_click=close_dialog),
                        ft.TextButton("Delete", on_click=submit_delete),
                    ],
                )
            )

        def sync_users_now(_=None):
            if not can_edit:
                show_snack("Only Super Admin can sync users.", error=True)
                return

            def work():
                try:
                    auth.sync_with_cloud(force=True)
                    published, err = auth.push_all_users_to_cloud()

                    def done():
                        if err:
                            show_snack(
                                f"Firebase sync failed: {err}",
                                error=True,
                            )
                        else:
                            show_snack(
                                f"Synced {published} user(s) to Firebase for all tablets."
                            )
                        render_view()

                    done()
                except Exception as exc:
                    show_snack(f"Firebase sync failed: {exc}", error=True)

            page.run_thread(work)

        async def handle_barcode_master_pick(_=None):
            if not can_edit:
                show_snack("Only Super Admin can update the barcode list.", error=True)
                return
            files = await file_picker.pick_files(
                dialog_title="Select Barcode Master List",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["xlsx"],
                allow_multiple=False,
                with_data=True,
            )
            if not files:
                return
            selected = files[0]
            try:
                if selected.path:
                    count, _meta = barcode_catalog.publish_to_cloud(
                        selected.path,
                        updated_by=admin_name,
                        filename=Path(selected.path).name,
                    )
                elif selected.bytes:
                    count, _meta = barcode_catalog.publish_to_cloud(
                        selected.bytes,
                        updated_by=admin_name,
                        filename=selected.name or "BarcodeMasterList.xlsx",
                    )
                else:
                    show_snack("Could not read the selected Excel file.", error=True)
                    return
                barcode_status.value = barcode_catalog.catalog_status_text()
                barcode_cloud_status.value = (
                    f"Published to all tablets — {count:,} barcodes."
                )
                show_snack(
                    f"Barcode master published — {count:,} barcodes synced to tablets."
                )
                page.update()
            except (ValueError, FileNotFoundError, RuntimeError) as exc:
                show_snack(str(exc), error=True)
            except Exception as exc:
                show_snack(f"Failed to publish barcode master: {exc}", error=True)

        def on_update_barcode(_=None):
            page.run_task(handle_barcode_master_pick)

        def pull_barcode_now(_=None):
            def work():
                try:
                    updated, message = barcode_catalog.sync_from_cloud(force=True)

                    def done():
                        barcode_status.value = barcode_catalog.catalog_status_text()
                        barcode_cloud_status.value = message
                        lower = message.lower()
                        show_snack(
                            message,
                            error=(
                                "fail" in lower
                                or "denied" in lower
                                or "could not" in lower
                                or "not set up" in lower
                            ),
                        )
                        page.update()

                    done()
                except Exception as exc:
                    show_snack(f"Could not pull barcode list: {exc}", error=True)

            page.run_thread(work)

        def refresh_fleet_ui() -> None:
            settings = firebase_presence.get_fleet_sync_settings()
            fleet_enabled_switch.value = bool(settings.get("enabled"))
            stamp = (
                scheduled_sync.normalize_time(str(settings.get("time") or "17:00"))
                or "17:00"
            )
            fleet_time_label.value = f"Scheduled time: {stamp}"
            fleet_output_dropdown.value = firebase_presence.normalize_fleet_output(
                str(settings.get("output_mode") or "")
            )
            fleet_date_label.value = (
                "Report date: "
                + firebase_presence.format_fleet_report_range(
                    str(settings.get("report_date_from") or ""),
                    str(settings.get("report_date_to") or ""),
                )
            )
            fleet_folder_label.value = (
                f"Save downloads to: {settings.get('download_folder') or firebase_presence.get_fleet_download_folder()}"
            )
            fleet_status.value = firebase_presence.fleet_sync_status_text()

        def render_fleet_backups(metas: list[dict] | None = None) -> None:
            fleet_backup_list.controls.clear()
            rows = metas
            if rows is None:
                try:
                    rows = firebase_presence.list_device_backup_metas()
                except Exception as exc:
                    fleet_backup_list.controls.append(
                        muted(f"Could not load backups: {exc}")
                    )
                    return
            if not rows:
                fleet_backup_list.controls.append(
                    muted("No tablet backups in Firebase yet.")
                )
                return
            for meta in rows:
                label = str(meta.get("device_label") or meta.get("device_id") or "?")
                sync_date = str(meta.get("sync_date") or "?")
                updated = str(meta.get("updated_at") or "")
                try:
                    if updated:
                        updated = (
                            datetime.fromisoformat(updated.replace("Z", "+00:00"))
                            .astimezone()
                            .strftime("%d/%m/%Y %H:%M")
                        )
                except Exception:
                    pass
                size = int(meta.get("byte_count") or 0)
                user = str(meta.get("username") or "").strip()
                user_bit = f" · {user}" if user else ""
                filename = str(meta.get("filename") or "").strip()
                kind = firebase_presence.fleet_output_label(
                    str(meta.get("output_mode") or "")
                )
                file_bit = f" · {filename}" if filename else f" · {kind}"
                fleet_backup_list.controls.append(
                    muted(
                        f"{label}{user_bit}{file_bit} — {sync_date} · {size:,} bytes · {updated}"
                    )
                )

        def _current_fleet_time() -> str:
            return (
                scheduled_sync.normalize_time(
                    str(firebase_presence.get_fleet_sync_settings().get("time") or "17:00")
                )
                or "17:00"
            )

        def save_fleet_settings(_=None):
            if not can_edit:
                show_snack("Only Super Admin can change fleet sync.", error=True)
                return
            current_time = _current_fleet_time()
            output_mode = firebase_presence.normalize_fleet_output(
                str(fleet_output_dropdown.value or "")
            )
            settings_now = firebase_presence.get_fleet_sync_settings()
            report_from, report_to = firebase_presence.normalize_fleet_report_range(
                str(settings_now.get("report_date_from") or ""),
                str(settings_now.get("report_date_to") or ""),
            )

            def work():
                try:
                    firebase_presence.save_fleet_sync_settings(
                        enabled=bool(fleet_enabled_switch.value),
                        sync_time=current_time,
                        output_mode=output_mode,
                        report_date_from=report_from,
                        report_date_to=report_to,
                        updated_by=admin_name,
                    )

                    def done():
                        refresh_fleet_ui()
                        show_snack("Fleet daily backup settings saved to Firebase.")
                        page.update()

                    done()
                except Exception as exc:
                    show_snack(f"Could not save fleet sync: {exc}", error=True)
                    refresh_fleet_ui()
                    page.update()

            page.run_thread(work)

        def on_fleet_switch(e):
            if not can_edit:
                fleet_enabled_switch.value = bool(
                    firebase_presence.get_fleet_sync_settings().get("enabled")
                )
                page.update()
                show_snack("Only Super Admin can change fleet sync.", error=True)
                return
            save_fleet_settings()

        fleet_enabled_switch.on_change = on_fleet_switch
        # Dropdowns use on_select in Flet 0.85+ (on_change is ignored).

        def on_fleet_output_change(_=None):
            if not can_edit:
                refresh_fleet_ui()
                page.update()
                show_snack("Only Super Admin can change fleet sync.", error=True)
                return
            save_fleet_settings()

        fleet_output_dropdown.on_select = on_fleet_output_change

        def open_fleet_time_picker(_=None):
            if not can_edit:
                show_snack("Only Super Admin can change fleet sync.", error=True)
                return
            current = _current_fleet_time()
            h, m = map(int, current.split(":"))

            def on_time_change(e):
                value = e.control.value
                if value is None:
                    return
                stamp = f"{value.hour:02d}:{value.minute:02d}"
                output_mode = firebase_presence.normalize_fleet_output(
                    str(fleet_output_dropdown.value or "")
                )
                settings_now = firebase_presence.get_fleet_sync_settings()
                report_from, report_to = firebase_presence.normalize_fleet_report_range(
                    str(settings_now.get("report_date_from") or ""),
                    str(settings_now.get("report_date_to") or ""),
                )

                def work():
                    try:
                        firebase_presence.save_fleet_sync_settings(
                            enabled=bool(fleet_enabled_switch.value),
                            sync_time=stamp,
                            output_mode=output_mode,
                            report_date_from=report_from,
                            report_date_to=report_to,
                            updated_by=admin_name,
                        )

                        def done():
                            refresh_fleet_ui()
                            show_snack(f"Fleet backup time set to {stamp}.")
                            page.update()

                        done()
                    except Exception as exc:
                        show_snack(f"Could not save fleet sync time: {exc}", error=True)

                page.run_thread(work)

            picker = ft.TimePicker(
                value=dt_time(hour=h, minute=m),
                help_text="Daily fleet backup time",
                confirm_text="Save",
                cancel_text="Cancel",
                hour_format=ft.TimePickerHourFormat.H24,
                on_change=on_time_change,
            )
            page.show_dialog(picker)

        def _save_report_range(date_from: str, date_to: str, *, which: str) -> None:
            def work():
                try:
                    firebase_presence.save_fleet_sync_settings(
                        enabled=bool(fleet_enabled_switch.value),
                        sync_time=_current_fleet_time(),
                        output_mode=firebase_presence.normalize_fleet_output(
                            str(fleet_output_dropdown.value or "")
                        ),
                        report_date_from=date_from,
                        report_date_to=date_to,
                        updated_by=admin_name,
                    )

                    def done():
                        refresh_fleet_ui()
                        show_snack(
                            f"Report date {which} set — "
                            + firebase_presence.format_fleet_report_range(date_from, date_to)
                        )
                        page.update()

                    done()
                except Exception as exc:
                    show_snack(f"Could not save report date: {exc}", error=True)

            page.run_thread(work)

        def open_fleet_date_from_picker(_=None):
            if not can_edit:
                show_snack("Only Super Admin can change the report date.", error=True)
                return
            settings_now = firebase_presence.get_fleet_sync_settings()
            current_from, current_to = firebase_presence.normalize_fleet_report_range(
                str(settings_now.get("report_date_from") or ""),
                str(settings_now.get("report_date_to") or ""),
            )
            current_day = date.fromisoformat(current_from)

            def on_date_change(e):
                value = e.control.value
                if value is None:
                    return
                if isinstance(value, datetime):
                    value = value.date()
                stamp = value.isoformat() if hasattr(value, "isoformat") else str(value)
                _save_report_range(stamp, current_to, which="from")

            picker = ft.DatePicker(
                value=current_day,
                help_text="Report date from",
                confirm_text="Save",
                cancel_text="Cancel",
                on_change=on_date_change,
            )
            page.show_dialog(picker)

        def open_fleet_date_to_picker(_=None):
            if not can_edit:
                show_snack("Only Super Admin can change the report date.", error=True)
                return
            settings_now = firebase_presence.get_fleet_sync_settings()
            current_from, current_to = firebase_presence.normalize_fleet_report_range(
                str(settings_now.get("report_date_from") or ""),
                str(settings_now.get("report_date_to") or ""),
            )
            current_day = date.fromisoformat(current_to)

            def on_date_change(e):
                value = e.control.value
                if value is None:
                    return
                if isinstance(value, datetime):
                    value = value.date()
                stamp = value.isoformat() if hasattr(value, "isoformat") else str(value)
                _save_report_range(current_from, stamp, which="to")

            picker = ft.DatePicker(
                value=current_day,
                help_text="Report date to",
                confirm_text="Save",
                cancel_text="Cancel",
                on_change=on_date_change,
            )
            page.show_dialog(picker)

        async def choose_fleet_download_folder(_=None):
            if not can_edit:
                show_snack("Only Super Admin can change the save folder.", error=True)
                return
            try:
                path = await file_picker.get_directory_path(
                    dialog_title="Choose folder for fleet backup downloads"
                )
            except Exception as exc:
                show_snack(f"Folder picker failed: {exc}", error=True)
                return
            if not path:
                return
            try:
                folder = firebase_presence.set_fleet_download_folder(path)
                fleet_folder_label.value = f"Save downloads to: {folder}"
                show_snack(f"Download folder set — {folder}")
                page.update()
            except Exception as exc:
                show_snack(f"Could not set folder: {exc}", error=True)

        def reset_fleet_download_folder(_=None):
            if not can_edit:
                show_snack("Only Super Admin can change the save folder.", error=True)
                return
            folder = firebase_presence.set_fleet_download_folder(None)
            fleet_folder_label.value = f"Save downloads to: {folder}"
            show_snack("Download folder reset to app default.")
            page.update()

        def refresh_fleet_backups(_=None):
            def work():
                try:
                    metas = firebase_presence.list_device_backup_metas()

                    def done():
                        refresh_fleet_ui()
                        render_fleet_backups(metas)
                        show_snack(f"Found {len(metas)} device backup(s) in Firebase.")
                        page.update()

                    done()
                except Exception as exc:
                    show_snack(f"Could not refresh backups: {exc}", error=True)

            page.run_thread(work)

        def _backup_in_report_range(meta: dict, date_from: str, date_to: str) -> bool:
            start, end = firebase_presence.normalize_fleet_report_range(date_from, date_to)
            start_d = date.fromisoformat(start)
            end_d = date.fromisoformat(end)
            legacy = str(meta.get("report_date") or meta.get("sync_date") or "").strip()
            meta_from = str(meta.get("report_date_from") or legacy or "").strip()
            meta_to = str(meta.get("report_date_to") or legacy or "").strip()
            if not meta_from and not meta_to:
                return True
            m_from, m_to = firebase_presence.normalize_fleet_report_range(meta_from, meta_to)
            a = date.fromisoformat(m_from)
            b = date.fromisoformat(m_to)
            return a <= end_d and b >= start_d

        def _download_all_backups_to_folder(
            *,
            report_date_from: str | None = None,
            report_date_to: str | None = None,
        ) -> tuple[int, Path, list[dict]]:
            metas = firebase_presence.list_device_backup_metas()
            out_dir = firebase_presence.get_fleet_download_folder()
            out_dir.mkdir(parents=True, exist_ok=True)
            if report_date_from or report_date_to:
                wanted_from, wanted_to = firebase_presence.normalize_fleet_report_range(
                    report_date_from, report_date_to
                )
                metas = [
                    m
                    for m in metas
                    if _backup_in_report_range(m, wanted_from, wanted_to)
                ]
            if not metas:
                return 0, out_dir, []
            saved = 0
            for meta in metas:
                device_id = str(meta.get("device_id") or "").strip()
                if not device_id:
                    continue
                raw, detail = firebase_presence.download_device_scanner_backup(device_id)
                label = str(detail.get("device_label") or device_id).strip() or device_id
                safe = "".join(
                    ch if ch.isalnum() or ch in "-_" else "_" for ch in label
                )[:40]
                sync_date = str(detail.get("sync_date") or detail.get("report_date") or "unknown")
                filename = str(detail.get("filename") or "scanner.db").strip()
                suffix = Path(filename).suffix or ".db"
                stem = Path(filename).stem or "backup"
                safe_stem = "".join(
                    ch if ch.isalnum() or ch in "-_" else "_" for ch in stem
                )[:50]
                path = (
                    out_dir / f"{safe}_{sync_date}_{safe_stem}_{device_id[:8]}{suffix}"
                )
                path.write_bytes(raw)
                saved += 1
            return saved, out_dir, metas

        def download_fleet_backups(_=None):
            if not can_edit:
                show_snack("Only Super Admin can download backups.", error=True)
                return

            def work():
                try:
                    settings_now = firebase_presence.get_fleet_sync_settings()
                    report_from, report_to = firebase_presence.normalize_fleet_report_range(
                        str(settings_now.get("report_date_from") or ""),
                        str(settings_now.get("report_date_to") or ""),
                    )
                    saved, out_dir, metas = _download_all_backups_to_folder(
                        report_date_from=report_from,
                        report_date_to=report_to,
                    )
                    if saved <= 0:
                        show_snack(
                            "No backups for "
                            + firebase_presence.format_fleet_report_range(
                                report_from, report_to
                            )
                            + "."
                        )
                        return

                    def done():
                        render_fleet_backups(metas)
                        show_snack(f"Downloaded {saved} backup(s) to {out_dir}")
                        page.update()

                    done()
                except Exception as exc:
                    show_snack(f"Download failed: {exc}", error=True)

            show_snack("Downloading tablet backups…")
            page.run_thread(work)

        def force_download_now(_=None):
            """Bypass schedule: ask tablets to upload for report date, then save to folder."""
            if not can_edit:
                show_snack("Only Super Admin can force download.", error=True)
                return

            def work():
                try:
                    settings_now = firebase_presence.get_fleet_sync_settings()
                    report_from, report_to = firebase_presence.normalize_fleet_report_range(
                        str(settings_now.get("report_date_from") or ""),
                        str(settings_now.get("report_date_to") or ""),
                    )
                    firebase_presence.request_fleet_force_sync(
                        updated_by=admin_name,
                        report_date_from=report_from,
                        report_date_to=report_to,
                    )
                    # Give open tablets a moment, then pull whatever is already available.
                    time.sleep(2)
                    saved, out_dir, metas = _download_all_backups_to_folder(
                        report_date_from=report_from,
                        report_date_to=report_to,
                    )
                    day_label = firebase_presence.format_fleet_report_range(
                        report_from, report_to
                    )

                    def done():
                        refresh_fleet_ui()
                        render_fleet_backups(metas)
                        if saved <= 0:
                            show_snack(
                                f"Force requested for {day_label}. No backups in Firebase yet — "
                                "leave tablets open, wait ~30s, then Force download again."
                            )
                        else:
                            show_snack(
                                f"Force download complete — {saved} file(s) for {day_label} "
                                f"saved to {out_dir}. Open tablets upload within ~30s; "
                                "click again to pull them."
                            )
                        page.update()

                    done()
                except Exception as exc:
                    show_snack(f"Force download failed: {exc}", error=True)

            show_snack("Force download — requesting tablets and saving to folder…")
            page.run_thread(work)

        def fleet_section() -> ft.Control:
            return ft.Container(
                bgcolor=ft.Colors.WHITE,
                border_radius=10,
                padding=20,
                content=ft.Column(
                    [
                        ft.Text(
                            "Fleet data sync",
                            weight=ft.FontWeight.W_600,
                            font_family=FONT_FAMILY,
                        ),
                        muted(
                            "Schedule time controls when open tablets auto-upload. "
                            "File type is the output format. Report date is separate — "
                            "use it for Force download / PDF & JSON (which day's sessions). "
                            "Daily scheduled uploads still use each tablet's local today."
                        ),
                        fleet_enabled_switch,
                        fleet_time_label,
                        fleet_output_dropdown,
                        fleet_date_label,
                        fleet_folder_label,
                        fleet_status,
                        ft.Row(
                            [
                                ft.OutlinedButton(
                                    "Set time",
                                    icon=ft.Icons.SCHEDULE,
                                    height=48,
                                    on_click=open_fleet_time_picker,
                                ),
                                ft.OutlinedButton(
                                    "Set date from",
                                    icon=ft.Icons.CALENDAR_MONTH,
                                    height=48,
                                    on_click=open_fleet_date_from_picker,
                                ),
                                ft.OutlinedButton(
                                    "Set date to",
                                    icon=ft.Icons.EVENT,
                                    height=48,
                                    on_click=open_fleet_date_to_picker,
                                ),
                                ft.ElevatedButton(
                                    "Save schedule",
                                    icon=ft.Icons.SAVE,
                                    bgcolor=PRIMARY,
                                    color=ft.Colors.WHITE,
                                    height=48,
                                    on_click=save_fleet_settings,
                                ),
                                ft.OutlinedButton(
                                    "Choose save folder",
                                    icon=ft.Icons.FOLDER_OPEN,
                                    height=48,
                                    on_click=lambda _: page.run_task(
                                        choose_fleet_download_folder
                                    ),
                                ),
                                ft.TextButton(
                                    "Reset folder",
                                    on_click=reset_fleet_download_folder,
                                ),
                            ],
                            spacing=10,
                            wrap=True,
                        ),
                        ft.Row(
                            [
                                ft.ElevatedButton(
                                    "Force download now",
                                    icon=ft.Icons.DOWNLOAD_FOR_OFFLINE,
                                    bgcolor="#C62828",
                                    color=ft.Colors.WHITE,
                                    height=48,
                                    on_click=force_download_now,
                                ),
                            ],
                            spacing=10,
                            wrap=True,
                        ),
                        muted(
                            "Force download now asks open tablets to upload for the "
                            "selected report date range (does not wait for the schedule), "
                            "then saves matching backups into your save folder."
                        ),
                        ft.Text(
                            "Backups in Firebase",
                            size=13,
                            weight=ft.FontWeight.W_600,
                            font_family=FONT_FAMILY,
                        ),
                        fleet_backup_list,
                        ft.Row(
                            [
                                ft.OutlinedButton(
                                    "Refresh list",
                                    icon=ft.Icons.REFRESH,
                                    height=48,
                                    on_click=refresh_fleet_backups,
                                ),
                                ft.OutlinedButton(
                                    "Download all to save folder",
                                    icon=ft.Icons.DOWNLOAD,
                                    height=48,
                                    on_click=download_fleet_backups,
                                ),
                            ],
                            spacing=10,
                            wrap=True,
                        ),
                    ],
                    spacing=10,
                    tight=True,
                ),
            )

        try:
            render_fleet_backups([])
        except Exception:
            pass

        def barcode_section() -> ft.Control:
            return ft.Container(
                bgcolor=ft.Colors.WHITE,
                border_radius=10,
                padding=20,
                content=ft.Column(
                    [
                        ft.Text(
                            "Barcode Master List",
                            weight=ft.FontWeight.W_600,
                            font_family=FONT_FAMILY,
                        ),
                        muted(
                            "Upload BarcodeMasterList.xlsx here. It is published to Firebase "
                            "and every tablet downloads it automatically."
                        ),
                        ft.Text(
                            "Current local catalog",
                            size=13,
                            weight=ft.FontWeight.W_600,
                            font_family=FONT_FAMILY,
                        ),
                        barcode_status,
                        barcode_cloud_status,
                        ft.Row(
                            [
                                ft.ElevatedButton(
                                    "Upload & publish to tablets",
                                    icon=ft.Icons.UPLOAD_FILE,
                                    bgcolor=PRIMARY,
                                    color=ft.Colors.WHITE,
                                    height=48,
                                    on_click=on_update_barcode,
                                ),
                                ft.OutlinedButton(
                                    "Pull latest from Firebase",
                                    icon=ft.Icons.CLOUD_DOWNLOAD,
                                    height=48,
                                    on_click=pull_barcode_now,
                                ),
                            ],
                            spacing=10,
                            wrap=True,
                        ),
                    ],
                    spacing=10,
                    tight=True,
                ),
            )

        def users_section() -> ft.Control:
            account_rows = ft.Column(spacing=4)
            try:
                accounts = auth.list_admin_accounts(admin_name)
            except PermissionError:
                accounts = []

            if not accounts:
                account_rows.controls.append(muted("No users yet."))
            for account in accounts:
                trailing_actions = [
                    ft.IconButton(
                        icon=ft.Icons.KEY,
                        icon_color=PRIMARY,
                        tooltip="Set password",
                        on_click=lambda _, name=account.username: open_set_password_dialog(
                            name
                        ),
                    ),
                ]
                if account.role != auth.ROLE_SUPER_ADMIN:
                    trailing_actions.insert(
                        0,
                        ft.IconButton(
                            icon=ft.Icons.BADGE,
                            icon_color=PRIMARY,
                            tooltip="Change role",
                            on_click=lambda _, name=account.username, role=account.role: open_set_role_dialog(
                                name, role
                            ),
                        ),
                    )
                if account.username != admin_name:
                    trailing_actions.append(
                        ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            icon_color="#E53935",
                            tooltip="Delete account",
                            on_click=lambda _, name=account.username: confirm_delete_user(
                                name
                            ),
                        )
                    )
                if account.role == auth.ROLE_SUPER_ADMIN:
                    leading_icon = ft.Icons.SHIELD
                elif account.role == auth.ROLE_PICKER:
                    leading_icon = ft.Icons.VERIFIED_USER
                elif account.role == auth.ROLE_MONITOR_VIEWER:
                    leading_icon = ft.Icons.VISIBILITY
                else:
                    leading_icon = ft.Icons.PERSON
                account_rows.controls.append(
                    ft.ListTile(
                        leading=ft.Icon(leading_icon, color=PRIMARY),
                        title=ft.Text(account.username, font_family=FONT_FAMILY),
                        subtitle=muted(account.role_label),
                        trailing=ft.Row(trailing_actions, tight=True),
                    )
                )

            return ft.Container(
                bgcolor=ft.Colors.WHITE,
                border_radius=10,
                padding=20,
                content=ft.Column(
                    [
                        ft.Text(
                            "Users",
                            weight=ft.FontWeight.W_600,
                            font_family=FONT_FAMILY,
                        ),
                        muted(
                            "Add Pickers, Admins, and Monitor Viewers. "
                            "Picker users appear in the New Scan dropdown on tablets. "
                            "Accounts sync when Firebase is set up."
                        ),
                        ft.ElevatedButton(
                            "Add User / Picker",
                            icon=ft.Icons.PERSON_ADD,
                            bgcolor=PRIMARY,
                            color=ft.Colors.WHITE,
                            height=48,
                            on_click=open_add_user_dialog,
                        ),
                        ft.OutlinedButton(
                            "Sync users to Firebase now",
                            icon=ft.Icons.CLOUD_UPLOAD,
                            height=44,
                            on_click=lambda _: sync_users_now(),
                        ),
                        muted(
                            "If tablets do not see new users, publish the app_users "
                            "rules in Firebase Console (see docs/FIREBASE_SETUP.md), "
                            "then tap Sync users to Firebase now."
                        ),
                        ft.Container(
                            content=account_rows,
                            border=ft.Border.all(1, "#E0E0E0"),
                            border_radius=8,
                            padding=4,
                        ),
                    ],
                    spacing=10,
                    tight=True,
                ),
            )

        week_dropdown.on_select = on_week_change
        settings_week_dropdown.on_select = on_settings_week_change

        def board_view() -> ft.Control:
            return ft.Column(
                [
                    top_label,
                    prize_banner,
                    ft.Row(
                        [range_label, week_dropdown],
                        spacing=16,
                        wrap=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    custom_dates_row,
                    ft.Divider(height=8, color=ft.Colors.TRANSPARENT),
                    ft.Text(
                        "Online pickers",
                        size=18,
                        weight=ft.FontWeight.W_600,
                        font_family=FONT_FAMILY,
                    ),
                    muted(
                        "Pickers with completed pickups today on tablets that are currently online."
                    ),
                    online_pickers_list,
                    ft.Divider(height=12, color=ft.Colors.TRANSPARENT),
                    ft.Text(
                        "Pickup leaderboard",
                        size=18,
                        weight=ft.FontWeight.W_600,
                        font_family=FONT_FAMILY,
                    ),
                    ft.Row(
                        [
                            muted(
                                "Each picker shows Picks and Lines bars. Choose order below."
                            ),
                            leaderboard_sort_dropdown,
                        ],
                        spacing=16,
                        wrap=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(content=chart_host, expand=True),
                ],
                expand=True,
                scroll=ft.ScrollMode.AUTO,
                spacing=10,
            )

        def settings_view() -> ft.Control:
            return ft.Column(
                [
                    ft.Text(
                        "Monitor settings",
                        size=22,
                        weight=ft.FontWeight.BOLD,
                        font_family=FONT_FAMILY,
                    ),
                    muted("Only Super Admin can change these. Changes sync to all tablets."),
                    ft.Divider(height=8, color=ft.Colors.TRANSPARENT),
                    ft.Container(
                        bgcolor=ft.Colors.WHITE,
                        border_radius=10,
                        padding=20,
                        content=ft.Column(
                            [
                                ft.Text(
                                    "Period filter",
                                    weight=ft.FontWeight.W_600,
                                    font_family=FONT_FAMILY,
                                ),
                                muted(
                                    "Today, this/last week, this month, or custom dates. "
                                    "Shared with Home on tablets."
                                ),
                                settings_week_dropdown,
                            ],
                            spacing=10,
                            tight=True,
                        ),
                    ),
                    ft.Container(
                        bgcolor=ft.Colors.WHITE,
                        border_radius=10,
                        padding=20,
                        content=ft.Column(
                            [
                                ft.Text(
                                    "Prize for #1 (optional)",
                                    weight=ft.FontWeight.W_600,
                                    font_family=FONT_FAMILY,
                                ),
                                muted(
                                    "Shown on the board under the crown winner, and on tablet Home. "
                                    "Leave blank for no prize message."
                                ),
                                prize_field,
                                ft.Row(
                                    [
                                        ft.ElevatedButton(
                                            "Save prize",
                                            bgcolor=PRIMARY,
                                            color=ft.Colors.WHITE,
                                            height=48,
                                            on_click=save_prize,
                                        ),
                                        ft.OutlinedButton(
                                            "Clear prize",
                                            height=48,
                                            on_click=clear_prize,
                                        ),
                                    ],
                                    spacing=10,
                                ),
                                settings_status,
                            ],
                            spacing=10,
                            tight=True,
                        ),
                    ),
                    fleet_section(),
                    barcode_section(),
                    users_section(),
                    ft.TextButton(
                        "Back to board",
                        icon=ft.Icons.ARROW_BACK,
                        on_click=open_board,
                    ),
                ],
                expand=True,
                scroll=ft.ScrollMode.AUTO,
                spacing=14,
            )

        def render_view():
            content_host.content = (
                settings_view() if session.get("view") == "settings" else board_view()
            )
            page.update()

        async def auto_loop():
            while getattr(page, "_monitor_token", None) == refresh_token:
                try:
                    if refresh_state["enabled"]:

                        def work():
                            if firebase_presence.is_configured():
                                try:
                                    firebase_presence.publish_heartbeat_async(
                                        username=admin_name,
                                        role=admin_role,
                                        online=True,
                                    )
                                except Exception:
                                    pass
                            # Backup poll — live SSE is preferred when connected.
                            return firebase_presence.dashboard_snapshot(
                                force_refresh=True
                            )

                        snap = await asyncio.to_thread(work)
                        if getattr(page, "_monitor_token", None) != refresh_token:
                            return
                        apply_snapshot(snap)
                        page.update()
                except Exception as exc:
                    if getattr(page, "_monitor_token", None) != refresh_token:
                        return
                    status_label.value = f"Refresh failed: {exc}"
                    try:
                        page.update()
                    except Exception:
                        pass

                wait_for = max(5, int(refresh_state.get("seconds") or _REFRESH_SECONDS))
                # When the real-time listener is healthy, poll only as a safety net.
                if firebase_presence.presence_listener_active():
                    wait_for = max(wait_for, 60)
                refresh_wake.clear()
                elapsed = 0.0
                while elapsed < wait_for:
                    if getattr(page, "_monitor_token", None) != refresh_token:
                        return
                    if refresh_wake.is_set():
                        refresh_wake.clear()
                        break
                    await asyncio.sleep(0.5)
                    elapsed += 0.5

        live_apply_pending = {"busy": False, "dirty": False}

        def on_live_presence_change() -> None:
            """Firebase SSE pushed a presence change — refresh board immediately."""

            async def apply_live():
                try:
                    while live_apply_pending["dirty"]:
                        live_apply_pending["dirty"] = False
                        if getattr(page, "_monitor_token", None) != refresh_token:
                            return
                        snap = await asyncio.to_thread(
                            lambda: firebase_presence.dashboard_snapshot(
                                force_refresh=False
                            )
                        )
                        if getattr(page, "_monitor_token", None) != refresh_token:
                            return
                        apply_snapshot(snap)
                        page.update()
                except Exception as exc:
                    if getattr(page, "_monitor_token", None) != refresh_token:
                        return
                    status_label.value = f"Live update failed: {exc}"
                    try:
                        page.update()
                    except Exception:
                        pass
                finally:
                    live_apply_pending["busy"] = False
                    if live_apply_pending["dirty"] and (
                        getattr(page, "_monitor_token", None) == refresh_token
                    ):
                        live_apply_pending["busy"] = True
                        page.run_task(apply_live)

            live_apply_pending["dirty"] = True
            if getattr(page, "_monitor_token", None) != refresh_token:
                return
            if live_apply_pending["busy"]:
                return
            live_apply_pending["busy"] = True
            page.run_task(apply_live)

        page._monitor_live_callback = on_live_presence_change
        if firebase_presence.is_configured():
            try:
                firebase_presence.start_presence_listener(on_live_presence_change)
                last_refresh_label.value = "Live · connecting…"
            except Exception:
                last_refresh_label.value = "Live listener unavailable — using poll"

        page.run_task(auto_loop)

        header_actions = [
            auto_refresh_switch,
            refresh_interval_dropdown,
            ft.OutlinedButton(
                "Refresh",
                icon=ft.Icons.REFRESH,
                on_click=refresh_now,
            ),
        ]
        if can_edit:
            header_actions.append(
                ft.OutlinedButton(
                    "Settings",
                    icon=ft.Icons.SETTINGS,
                    on_click=open_settings,
                )
            )
        header_actions.append(ft.TextButton("Sign out", on_click=logout))

        body.content = ft.Column(
            [
                ft.Row(
                    [
                        ft.Image(src=logo_src(), width=140, fit=ft.BoxFit.CONTAIN),
                        ft.Column(
                            [
                                title,
                                muted(
                                    "Live Firebase updates — rankings change as soon as "
                                    "tablets publish. Auto refresh is a backup poll."
                                    + (
                                        ""
                                        if can_edit
                                        else " View-only access."
                                    )
                                ),
                                last_refresh_label,
                            ],
                            spacing=4,
                            expand=True,
                        ),
                        *header_actions,
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                status_label,
                ft.Divider(height=8, color=ft.Colors.TRANSPARENT),
                content_host,
            ],
            expand=True,
            spacing=8,
        )
        render_view()

    restored = auth.load_persisted_session()
    if restored and auth.can_access_monitor(restored.role):
        session["username"] = restored.username
        session["role"] = restored.role
        session["view"] = "board"
        show_shell()
    else:
        show_login()
