"""Reusable UI components."""

from __future__ import annotations

import flet as ft

from app.theme import (
    BG_INPUT,
    BORDER,
    DANGER,
    DANGER_HOVER,
    FONT_FAMILY,
    MIN_TOUCH,
    PRIMARY,
    PRIMARY_HOVER,
    SIDEBAR_WIDTH,
    TEXT,
    TEXT_MUTED,
)


def nav_button(label: str, icon: str, active: bool, on_click) -> ft.Container:
    return ft.Container(
        content=ft.ElevatedButton(
            content=ft.Row(
                [
                    ft.Icon(icon, color=ft.Colors.WHITE, size=22),
                    ft.Text(label, color=ft.Colors.WHITE, size=14, weight=ft.FontWeight.W_600),
                ],
                spacing=8,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            bgcolor=PRIMARY if active else "#42A5F5",
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=ft.Padding.symmetric(horizontal=12, vertical=14),
            ),
            on_click=on_click,
        ),
        width=SIDEBAR_WIDTH - 24,
    )


def labeled_field(label: str, control: ft.Control, *, expand: bool = False) -> ft.Column:
    return ft.Column(
        [
            ft.Text(label, size=13, weight=ft.FontWeight.W_600, color=TEXT, font_family=FONT_FAMILY),
            control,
        ],
        spacing=6,
        tight=not expand,
        expand=expand,
    )


def person_name_dropdown(
    *,
    hint: str,
    options: list[str],
    value: str = "",
) -> ft.Dropdown:
    return ft.Dropdown(
        expand=True,
        editable=True,
        enable_filter=True,
        hint_text=hint,
        value=value or None,
        options=[ft.dropdown.Option(key=name, text=name) for name in options],
        text_size=15,
        border_color=BORDER,
        focused_border_color=PRIMARY,
        bgcolor=BG_INPUT,
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=10),
    )


def text_input(
    *,
    hint: str = "",
    value: str = "",
    read_only: bool = False,
    on_submit=None,
    on_change=None,
    expand: bool = True,
    autofocus: bool = False,
) -> ft.TextField:
    return ft.TextField(
        value=value,
        hint_text=hint,
        read_only=read_only,
        expand=expand,
        autofocus=autofocus,
        text_size=15,
        height=MIN_TOUCH,
        border_color=BORDER,
        focused_border_color=PRIMARY,
        bgcolor=BG_INPUT,
        content_padding=ft.Padding.symmetric(horizontal=12, vertical=10),
        on_submit=on_submit,
        on_change=on_change,
    )


def action_button(
    label: str,
    icon: str,
    *,
    primary: bool = True,
    on_click=None,
    bgcolor: str | None = None,
    hover: str | None = None,
) -> ft.ElevatedButton:
    bg = bgcolor or (PRIMARY if primary else DANGER)
    hover_bg = hover or (PRIMARY_HOVER if primary else DANGER_HOVER)
    return ft.ElevatedButton(
        content=ft.Row(
            [
                ft.Icon(icon, color=ft.Colors.WHITE, size=18),
                ft.Text(label, color=ft.Colors.WHITE, size=14, weight=ft.FontWeight.W_600),
            ],
            spacing=6,
        ),
        bgcolor=bg,
        style=ft.ButtonStyle(
            bgcolor={ft.ControlState.DEFAULT: bg, ft.ControlState.HOVERED: hover_bg},
            shape=ft.RoundedRectangleBorder(radius=6),
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
        ),
        on_click=on_click,
    )


def section_title(text: str) -> ft.Text:
    return ft.Text(
        text,
        size=22,
        weight=ft.FontWeight.BOLD,
        color=TEXT,
        font_family=FONT_FAMILY,
    )


def muted(text: str, size: int = 13) -> ft.Text:
    return ft.Text(text, size=size, color=TEXT_MUTED, font_family=FONT_FAMILY)


def app_footer() -> ft.Container:
    from app.metadata import app_metadata
    from app.picker_helper import CREDIT_AUTHOR

    meta = app_metadata()
    footer_text = f"{meta['name']} v{meta['version']}"
    if meta["copyright"]:
        footer_text = f"{footer_text}  ·  {meta['copyright']}"

    return ft.Container(
        content=ft.Row(
            [
                ft.Column(
                    [
                        muted(f"Developer: {meta['developer']}"),
                        muted(f"Picker Helper: {CREDIT_AUTHOR}", size=12),
                    ],
                    spacing=2,
                    tight=True,
                ),
                ft.Container(expand=True),
                muted(footer_text),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=24, vertical=10),
        border=ft.Border(top=ft.BorderSide(1, BORDER)),
        bgcolor=BG_INPUT,
    )


def capitalize_person_name(value: str) -> str:
    """Capitalize the first letter of each word in a person name."""
    value = (value or "").strip()
    if not value:
        return ""
    return " ".join(part[:1].upper() + part[1:] for part in value.split() if part)


def format_check_when(session: dict | None = None, *, check_date: str = "", check_time: str = "") -> str:
    """Format check date and optional time for display."""
    if session:
        check_date = session.get("check_date", "") or ""
        check_time = session.get("check_time", "") or ""
    time_part = (check_time or "").strip()
    date_part = (check_date or "").strip()
    if date_part and time_part:
        return f"{date_part} {time_part}"
    return date_part or "—"
