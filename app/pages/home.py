"""Home dashboard page."""

from __future__ import annotations

import time

import flet as ft

from app import database
from app import firebase_presence
from app.components import muted, section_title, format_check_when
from app.theme import BG_MAIN, FONT_FAMILY, PRIMARY, TEXT

_DASHBOARD_REFRESH_SECONDS = 20


def build(
    page: ft.Page,
    navigate,
    show_snack,
    file_picker: ft.FilePicker,
    *,
    admin_username: str | None = None,
    admin_role: str | None = None,
    **_kwargs,
) -> ft.Control:
    sessions = database.list_sessions(limit=5)
    counts = database.session_stats()

    stats = ft.Row(
        [
            _stat_card("Total Scans", str(counts["total"]), PRIMARY),
            _stat_card("Saved Drafts", str(counts.get("draft", 0)), "#FB8C00"),
            _stat_card("Completed", str(counts.get("completed", 0)), "#43A047"),
        ],
        spacing=16,
        wrap=True,
    )

    online_count_text = ft.Text(
        "—",
        size=28,
        weight=ft.FontWeight.BOLD,
        color="#2E7D32",
        font_family=FONT_FAMILY,
    )
    today_sum_text = ft.Text(
        "—",
        size=28,
        weight=ft.FontWeight.BOLD,
        color=PRIMARY,
        font_family=FONT_FAMILY,
    )
    dash_status = muted("Loading live dashboard…")
    online_list = ft.Column(spacing=8, tight=True)
    fulfilment_list = ft.Column(spacing=8, tight=True)

    quick_actions = ft.Row(
        [
            ft.ElevatedButton(
                "Start New Scan",
                icon=ft.Icons.QR_CODE_SCANNER,
                bgcolor=PRIMARY,
                color=ft.Colors.WHITE,
                height=52,
                on_click=lambda _: navigate("new_scan"),
            ),
            ft.OutlinedButton(
                "View History",
                icon=ft.Icons.HISTORY,
                height=52,
                on_click=lambda _: navigate("history"),
            ),
            ft.OutlinedButton(
                "Settings",
                icon=ft.Icons.SETTINGS,
                height=52,
                on_click=lambda _: navigate("settings"),
            ),
        ],
        spacing=12,
        wrap=True,
    )

    recent = ft.Column(spacing=8)
    if sessions:
        for s in sessions:
            recent.controls.append(
                ft.ListTile(
                    title=ft.Text(
                        f"Sales Order No: {s['sales_order_no']}",
                        weight=ft.FontWeight.W_600,
                        font_family=FONT_FAMILY,
                    ),
                    subtitle=muted(
                        f"{s['picker_name']} · {format_check_when(s)} · {s.get('item_count', 0)} items"
                        + (" · Draft" if s.get("status") == "draft" else "")
                    ),
                    trailing=ft.Icon(ft.Icons.CHEVRON_RIGHT, color=PRIMARY),
                    on_click=lambda _, sid=s["id"]: navigate(
                        "history_detail", session_id=sid
                    ),
                )
            )
    else:
        recent.controls.append(muted("No scans yet. Start your first picking check."))

    signed_in = muted(
        f"Signed in as {admin_username}"
        if admin_username
        else "Not signed in — open Settings to manage users and cloud sync."
    )

    def render_online(entries: list[firebase_presence.PresenceEntry]) -> None:
        online_list.controls.clear()
        online = [e for e in entries if e.online]
        if not online:
            online_list.controls.append(muted("No tablets online right now."))
            return
        for entry in online:
            user_bit = entry.username or "(not logged in)"
            if entry.role:
                user_bit = f"{user_bit} ({entry.role})"
            suffix = " · this device" if entry.is_this_device else ""
            online_list.controls.append(
                ft.Container(
                    bgcolor="#F1F8E9",
                    border=ft.Border.all(1, "#C8E6C9"),
                    border_radius=6,
                    padding=12,
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.CIRCLE, color="#2E7D32", size=12),
                            ft.Column(
                                [
                                    ft.Text(
                                        f"{entry.device_label}{suffix}",
                                        weight=ft.FontWeight.W_600,
                                        font_family=FONT_FAMILY,
                                    ),
                                    muted(
                                        f"{user_bit} · today {entry.fulfilments_today} · "
                                        f"total {entry.fulfilments_total}"
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

    def render_fulfilments(rows: list[firebase_presence.UserFulfilmentRow]) -> None:
        fulfilment_list.controls.clear()
        if not rows:
            fulfilment_list.controls.append(
                muted("No completed fulfilments reported yet.")
            )
            return
        for row in rows:
            status = "Online" if row.online else "Offline"
            status_color = "#2E7D32" if row.online else "#9E9E9E"
            device_bit = (
                f" · {', '.join(row.devices)}" if row.devices else ""
            )
            fulfilment_list.controls.append(
                ft.Container(
                    bgcolor=ft.Colors.WHITE,
                    border=ft.Border.all(1, "#E0E0E0"),
                    border_radius=6,
                    padding=12,
                    content=ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Text(
                                        row.username,
                                        weight=ft.FontWeight.W_600,
                                        font_family=FONT_FAMILY,
                                    ),
                                    muted(f"{status}{device_bit}"),
                                ],
                                spacing=2,
                                tight=True,
                                expand=True,
                            ),
                            ft.Column(
                                [
                                    ft.Text(
                                        str(row.today),
                                        size=22,
                                        weight=ft.FontWeight.BOLD,
                                        color=PRIMARY,
                                        font_family=FONT_FAMILY,
                                        text_align=ft.TextAlign.RIGHT,
                                    ),
                                    muted("today"),
                                ],
                                spacing=0,
                                tight=True,
                                horizontal_alignment=ft.CrossAxisAlignment.END,
                            ),
                            ft.Container(width=16),
                            ft.Column(
                                [
                                    ft.Text(
                                        str(row.total),
                                        size=22,
                                        weight=ft.FontWeight.BOLD,
                                        color=TEXT,
                                        font_family=FONT_FAMILY,
                                        text_align=ft.TextAlign.RIGHT,
                                    ),
                                    muted("total"),
                                ],
                                spacing=0,
                                tight=True,
                                horizontal_alignment=ft.CrossAxisAlignment.END,
                            ),
                            ft.Text(
                                status,
                                color=status_color,
                                weight=ft.FontWeight.W_600,
                                font_family=FONT_FAMILY,
                                width=70,
                                text_align=ft.TextAlign.RIGHT,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
            )

    def apply_snapshot(snap: dict) -> None:
        online_count_text.value = str(snap.get("online_count", 0))
        today_sum_text.value = str(snap.get("today_sum", 0))
        render_online(list(snap.get("presence") or []))
        render_fulfilments(list(snap.get("fulfilments") or []))
        if snap.get("configured"):
            dash_status.value = (
                f"Live from Firebase · refreshes every {_DASHBOARD_REFRESH_SECONDS}s · "
                f"{snap.get('device_count', 0)} device(s) reporting"
            )
        else:
            dash_status.value = (
                "Showing this device only — set up Firebase in Settings → Who's online "
                "to see all tablets."
            )

    def refresh_dashboard(_=None):
        def work():
            try:
                # Push latest local counts before reading the board.
                if firebase_presence.is_configured():
                    try:
                        firebase_presence.publish_heartbeat(
                            username=admin_username,
                            role=admin_role,
                            online=True,
                        )
                    except Exception:
                        pass
                snap = firebase_presence.dashboard_snapshot()

                def apply():
                    apply_snapshot(snap)
                    page.update()

                apply()
            except Exception as exc:
                dash_status.value = f"Dashboard update failed: {exc}"
                try:
                    page.update()
                except Exception:
                    pass

        page.run_thread(work)

    refresh_token = time.time()
    page._home_dashboard_token = refresh_token

    def auto_refresh_loop():
        # Immediate first paint, then keep refreshing while Home stays open.
        while getattr(page, "_home_dashboard_token", None) == refresh_token:
            try:
                if firebase_presence.is_configured():
                    try:
                        firebase_presence.publish_heartbeat(
                            username=admin_username,
                            role=admin_role,
                            online=True,
                        )
                    except Exception:
                        pass
                snap = firebase_presence.dashboard_snapshot()

                def apply():
                    if getattr(page, "_home_dashboard_token", None) != refresh_token:
                        return
                    apply_snapshot(snap)
                    page.update()

                apply()
            except Exception as exc:
                def show_err(message=str(exc)):
                    if getattr(page, "_home_dashboard_token", None) != refresh_token:
                        return
                    dash_status.value = f"Dashboard update failed: {message}"
                    page.update()

                show_err()
            for _ in range(_DASHBOARD_REFRESH_SECONDS):
                if getattr(page, "_home_dashboard_token", None) != refresh_token:
                    return
                time.sleep(1)

    page.run_thread(auto_refresh_loop)

    live_summary = ft.Row(
        [
            ft.Container(
                content=ft.Column(
                    [
                        online_count_text,
                        ft.Text("Online now", size=13, color=TEXT, font_family=FONT_FAMILY),
                    ],
                    spacing=4,
                ),
                bgcolor=ft.Colors.WHITE,
                border_radius=8,
                padding=20,
                width=180,
            ),
            ft.Container(
                content=ft.Column(
                    [
                        today_sum_text,
                        ft.Text(
                            "Fulfilments today",
                            size=13,
                            color=TEXT,
                            font_family=FONT_FAMILY,
                        ),
                    ],
                    spacing=4,
                ),
                bgcolor=ft.Colors.WHITE,
                border_radius=8,
                padding=20,
                width=200,
            ),
            ft.OutlinedButton(
                "Refresh",
                icon=ft.Icons.REFRESH,
                height=52,
                on_click=refresh_dashboard,
            ),
        ],
        spacing=16,
        wrap=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    return ft.Container(
        content=ft.Column(
            [
                section_title("Home"),
                muted("Picking Barcode Scanner — warehouse picking verification"),
                signed_in,
                ft.Divider(height=24, color=ft.Colors.TRANSPARENT),
                ft.Text(
                    "Live team dashboard",
                    size=16,
                    weight=ft.FontWeight.W_600,
                    font_family=FONT_FAMILY,
                ),
                dash_status,
                live_summary,
                ft.Divider(height=12, color=ft.Colors.TRANSPARENT),
                ft.Text(
                    "Who's online",
                    size=15,
                    weight=ft.FontWeight.W_600,
                    font_family=FONT_FAMILY,
                ),
                online_list,
                ft.Divider(height=12, color=ft.Colors.TRANSPARENT),
                ft.Text(
                    "Fulfilments by user",
                    size=15,
                    weight=ft.FontWeight.W_600,
                    font_family=FONT_FAMILY,
                ),
                muted("Completed checks today / all time (summed from every reporting tablet)."),
                fulfilment_list,
                ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                ft.Text(
                    "This device",
                    size=16,
                    weight=ft.FontWeight.W_600,
                    font_family=FONT_FAMILY,
                ),
                stats,
                ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                ft.Text(
                    "Quick Actions",
                    size=16,
                    weight=ft.FontWeight.W_600,
                    font_family=FONT_FAMILY,
                ),
                quick_actions,
                ft.Divider(height=24, color=ft.Colors.TRANSPARENT),
                ft.Text(
                    "Recent Activity",
                    size=16,
                    weight=ft.FontWeight.W_600,
                    font_family=FONT_FAMILY,
                ),
                ft.Container(
                    content=recent,
                    bgcolor=ft.Colors.WHITE,
                    border_radius=8,
                    padding=8,
                ),
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        padding=24,
        expand=True,
        bgcolor=BG_MAIN,
    )


def _stat_card(title: str, value: str, color: str) -> ft.Container:
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    value,
                    size=28,
                    weight=ft.FontWeight.BOLD,
                    color=color,
                    font_family=FONT_FAMILY,
                ),
                ft.Text(title, size=13, color=TEXT, font_family=FONT_FAMILY),
            ],
            spacing=4,
        ),
        bgcolor=ft.Colors.WHITE,
        border_radius=8,
        padding=20,
        width=180,
    )
