"""Home dashboard page."""

from __future__ import annotations

import flet as ft

from app import database
from app.components import muted, section_title, format_check_when
from app.theme import BG_MAIN, FONT_FAMILY, PRIMARY, TEXT


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

    return ft.Container(
        content=ft.Column(
            [
                section_title("Home"),
                muted("Picking Barcode Scanner — warehouse picking verification"),
                signed_in,
                ft.Divider(height=24, color=ft.Colors.TRANSPARENT),
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
