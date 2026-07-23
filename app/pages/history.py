"""Scan history page."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import flet as ft

from app import auth
from app import cloud_sync
from app import database
from app.components import muted, section_title, text_input, format_check_when
from app.database import _parse_display_date
from app.history_export import (
    export_report_pdf_bytes,
    export_session_pdf_bytes,
)
from app.item_grouping import group_scanned_items
from app.theme import BG_MAIN, BG_TABLE, BORDER, FONT_FAMILY, MIN_TOUCH, PRIMARY, TEXT


def _export_checker_tag(username: str | None) -> str:
    """Safe filename fragment from the logged-in checker (unique per tablet user)."""
    raw = (username or "").strip() or "unknown"
    safe = re.sub(r"[^\w\-]+", "_", raw, flags=re.UNICODE)
    return (safe.strip("_") or "unknown")[:40]


def _list_height(page: ft.Page) -> int:
    height = getattr(getattr(page, "window", None), "height", None) or page.height or 800
    return max(360, min(640, int(height) - 300))


def _detail_list_height(page: ft.Page) -> int:
    height = getattr(getattr(page, "window", None), "height", None) or page.height or 800
    return max(280, int(height) - 480)


async def _save_pdf_export(
    page: ft.Page,
    file_picker: ft.FilePicker,
    *,
    dialog_title: str,
    file_name: str,
    pdf_bytes: bytes,
) -> str | None:
    """Save a PDF via FilePicker — mobile/tablet requires ``src_bytes``."""
    save_kwargs = {
        "dialog_title": dialog_title,
        "file_name": file_name,
        "file_type": ft.FilePickerFileType.CUSTOM,
        "allowed_extensions": ["pdf"],
    }
    if page.web or page.platform.is_mobile():
        save_kwargs["src_bytes"] = pdf_bytes

    dest = await file_picker.save_file(**save_kwargs)
    if not dest:
        return None
    if not page.web and not page.platform.is_mobile():
        Path(dest).write_bytes(pdf_bytes)
    return dest


def _filter_field(label: str, control: ft.Control, width: float) -> ft.Container:
    return ft.Container(
        width=width,
        content=ft.Column(
            [
                ft.Text(label, size=13, weight=ft.FontWeight.W_600, font_family=FONT_FAMILY, color=TEXT),
                control,
            ],
            spacing=6,
            tight=True,
        ),
    )


def _format_filter_date(value: datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime("%d/%m/%Y")


def _date_filter_row(
    page: ft.Page,
    *,
    value: str = "",
    width: float = 160,
) -> tuple[ft.TextField, ft.Row]:
    field = text_input(hint="dd/mm/yyyy", value=value, read_only=True, expand=True)

    def _end_of_today() -> datetime:
        return datetime.combine(date.today(), datetime.max.time())

    picker = ft.DatePicker(
        help_text="Select date (today allowed)",
        entry_mode=ft.DatePickerEntryMode.CALENDAR,
        locale=ft.Locale("en", "AU"),
        first_date=date(2020, 1, 1),
        last_date=_end_of_today(),
        current_date=date.today(),
    )

    def open_picker(_=None):
        # Keep last_date at end of today so the current day stays selectable.
        picker.last_date = _end_of_today()
        picker.current_date = date.today()
        parsed = _parse_display_date(field.value or "")
        if parsed and parsed > date.today():
            parsed = date.today()
        picker.value = datetime.combine(
            parsed or date.today(),
            datetime.min.time(),
        )
        page.show_dialog(picker)

    def on_date_selected(_):
        if picker.value:
            selected = picker.value
            if isinstance(selected, datetime):
                selected = selected.date()
            if selected > date.today():
                selected = date.today()
            field.value = _format_filter_date(selected)
        page.pop_dialog()
        field.update()

    picker.on_change = on_date_selected
    field.on_click = open_picker

    row = ft.Row(
        [
            ft.Container(content=field, expand=True),
            ft.IconButton(
                icon=ft.Icons.CALENDAR_MONTH,
                icon_color=PRIMARY,
                tooltip="Pick date",
                on_click=open_picker,
            ),
        ],
        spacing=0,
        width=width,
    )
    return field, row


def _admin_helpers(
    page: ft.Page,
    navigate,
    show_snack,
    *,
    is_admin: bool,
    login_admin,
    queue_admin_action,
    on_after_login=None,
):
    def show_admin_login(on_success):
        username_field = ft.TextField(label="Username", autofocus=True)
        password_field = ft.TextField(label="Password", password=True, can_reveal_password=True)

        def close_dialog(_=None):
            page.pop_dialog()

        def submit_login(_=None):
            name = (username_field.value or "").strip()
            if login_admin and login_admin(name, password_field.value or ""):
                page.pop_dialog()
                show_snack(f"Admin login successful — {name}")
                if queue_admin_action:
                    queue_admin_action(on_success)
                if on_after_login:
                    on_after_login()
            else:
                show_snack("Invalid admin username or password.", error=True)

        password_field.on_submit = submit_login
        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Admin Login"),
                content=ft.Column(
                    [
                        ft.Text("Admin access required.", size=13, font_family=FONT_FAMILY),
                        username_field,
                        password_field,
                    ],
                    tight=True,
                    spacing=12,
                    width=320,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=close_dialog),
                    ft.TextButton("Login", on_click=submit_login),
                ],
            )
        )

    def require_admin(action):
        def runner(_=None):
            if is_admin:
                action()
            else:
                show_admin_login(action)

        return runner

    return require_admin


def build(
    page: ft.Page,
    navigate,
    show_snack,
    file_picker: ft.FilePicker,
    *,
    admin_username: str | None = None,
    admin_role: str | None = None,
    login_admin=None,
    logout_admin=None,
    queue_admin_action=None,
    filters: dict | None = None,
    on_filters_change=None,
) -> ft.Control:
    is_admin = bool(admin_username)
    filters = dict(filters or {})
    sales_order = filters.get("sales_order", "")
    date_from = filters.get("date_from", "")
    date_to = filters.get("date_to", "")
    status = filters.get("status", "all")
    status_value = {"value": status}
    list_height = _list_height(page)

    search_field = text_input(hint="Search Sales Order No.", value=sales_order, expand=False)
    search_field.width = 260
    date_from_field, date_from_row = _date_filter_row(page, value=date_from, width=160)
    date_to_field, date_to_row = _date_filter_row(page, value=date_to, width=160)

    def current_filters() -> dict:
        return {
            "sales_order": (search_field.value or "").strip(),
            "date_from": (date_from_field.value or "").strip(),
            "date_to": (date_to_field.value or "").strip(),
            "status": status_value["value"],
        }

    def filter_summary_text() -> str:
        parts = []
        active = current_filters()
        if active["sales_order"]:
            parts.append(f"Sales Order contains '{active['sales_order']}'")
        if active["date_from"]:
            parts.append(f"From {active['date_from']}")
        if active["date_to"]:
            parts.append(f"To {active['date_to']}")
        if active["status"] != "all":
            parts.append(f"Status {active['status']}")
        return "; ".join(parts) if parts else "No filters applied"

    sessions = database.search_sessions(
        sales_order=sales_order,
        date_from=date_from,
        date_to=date_to,
        status=None if status == "all" else status,
    )

    require_admin = _admin_helpers(
        page,
        navigate,
        show_snack,
        is_admin=is_admin,
        login_admin=login_admin,
        queue_admin_action=queue_admin_action,
        on_after_login=lambda: navigate("history"),
    )

    session_column = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO)
    for session in sessions:
        is_draft = session.get("status") == "draft"
        subtitle = f"Checking #{session['id']} · {format_check_when(session)} · Picker: {session['picker_name']}"
        if is_draft:
            subtitle += " · Draft"
        session_column.controls.append(
            ft.Container(
                content=ft.ListTile(
                    title=ft.Text(
                        f"Sales Order No: {session['sales_order_no']}",
                        weight=ft.FontWeight.W_600,
                        font_family=FONT_FAMILY,
                        size=14,
                    ),
                    subtitle=muted(subtitle),
                    trailing=ft.Row(
                        [
                            ft.Text(
                                f"{session.get('item_count', 0)} items",
                                size=12,
                                color="#FB8C00" if is_draft else TEXT,
                                font_family=FONT_FAMILY,
                            ),
                            ft.Icon(ft.Icons.CHEVRON_RIGHT, color=PRIMARY, size=22),
                        ],
                        spacing=8,
                        tight=True,
                    ),
                    on_click=lambda _, sid=session["id"]: navigate("history_detail", session_id=sid),
                ),
                bgcolor=ft.Colors.WHITE,
                border_radius=6,
            )
        )
    if not sessions:
        session_column.controls.append(muted("No sessions match your filters."))

    def apply_filters(_=None):
        if on_filters_change:
            on_filters_change(current_filters())
        navigate("history")

    def set_status(value: str):
        active = current_filters()
        active["status"] = value
        if on_filters_change:
            on_filters_change(active)
        navigate("history")

    search_field.on_submit = lambda _: apply_filters()

    def clear_all_history(_=None):
        def confirm_clear(_=None):
            deleted = database.delete_all_sessions()
            page.pop_dialog()
            show_snack(f"Deleted {deleted} session(s).")
            navigate("history")

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Clear All History"),
                content=ft.Text("Delete all scan history? This cannot be undone."),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                    ft.TextButton("Delete All", on_click=confirm_clear),
                ],
            )
        )

    async def export_filtered_report(_=None):
        active = current_filters()
        rows = database.search_sessions(
            sales_order=active["sales_order"],
            date_from=active["date_from"],
            date_to=active["date_to"],
            status=None if active["status"] == "all" else active["status"],
        )
        full_sessions = database.get_sessions_with_items([s["id"] for s in rows])
        if not full_sessions:
            show_snack("No sessions to export for the current filters.", error=True)
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        checker_tag = _export_checker_tag(admin_username)
        try:
            pdf_bytes = export_report_pdf_bytes(
                full_sessions,
                filter_summary=filter_summary_text(),
            )
            dest = await _save_pdf_export(
                page,
                file_picker,
                dialog_title="Export Filtered Report PDF",
                file_name=f"picking_report_{checker_tag}_{stamp}.pdf",
                pdf_bytes=pdf_bytes,
            )
        except Exception as exc:
            show_snack(f"Export failed: {exc}", error=True)
            return
        if not dest:
            return
        show_snack(f"Exported {len(full_sessions)} session(s) to PDF.")

    def open_sync_dialog(_=None):
        if not cloud_sync.credentials_configured():
            show_snack(
                "Cloud sync is not set up. Open Settings → Cloud Sync and "
                "choose your Google Drive or OneDrive folder on this device.",
                error=True,
            )
            return

        has_date_filter = bool(
            (date_from_field.value or "").strip() or (date_to_field.value or "").strip()
        )
        if cloud_sync.get_sync_folder() is not None:
            default_provider = cloud_sync.PROVIDER_FOLDER
        elif cloud_sync.is_signed_in(cloud_sync.PROVIDER_GOOGLE):
            default_provider = cloud_sync.PROVIDER_GOOGLE
        elif cloud_sync.is_signed_in(cloud_sync.PROVIDER_ONEDRIVE):
            default_provider = cloud_sync.PROVIDER_ONEDRIVE
        elif cloud_sync.oauth_available(cloud_sync.PROVIDER_GOOGLE):
            default_provider = cloud_sync.PROVIDER_GOOGLE
        else:
            default_provider = cloud_sync.PROVIDER_ONEDRIVE

        provider_value = {"value": default_provider}
        backup_value = {
            "value": (
                cloud_sync.BACKUP_FILTERED
                if has_date_filter
                else cloud_sync.BACKUP_FULL_DB
            )
        }
        status_text = ft.Text(
            "Choose where to sync and what to upload.",
            size=13,
            font_family=FONT_FAMILY,
        )

        provider_options = []
        if cloud_sync.get_sync_folder() is not None:
            provider_options.append(
                ft.Radio(
                    value=cloud_sync.PROVIDER_FOLDER,
                    label="Cloud folder on this device (recommended)",
                )
            )
        if cloud_sync.oauth_available(cloud_sync.PROVIDER_GOOGLE):
            signed = cloud_sync.is_signed_in(cloud_sync.PROVIDER_GOOGLE)
            provider_options.append(
                ft.Radio(
                    value=cloud_sync.PROVIDER_GOOGLE,
                    label="Google Drive" + (" (signed in)" if signed else " (sign in)"),
                )
            )
        if cloud_sync.oauth_available(cloud_sync.PROVIDER_ONEDRIVE):
            signed = cloud_sync.is_signed_in(cloud_sync.PROVIDER_ONEDRIVE)
            provider_options.append(
                ft.Radio(
                    value=cloud_sync.PROVIDER_ONEDRIVE,
                    label="OneDrive" + (" (signed in)" if signed else " (sign in)"),
                )
            )
        if not provider_options:
            show_snack(
                "Open Settings → Cloud Sync and choose a cloud folder first.",
                error=True,
            )
            return

        provider_group = ft.RadioGroup(
            value=provider_value["value"],
            content=ft.Column(provider_options, tight=True),
        )
        backup_group = ft.RadioGroup(
            value=backup_value["value"],
            content=ft.Column(
                [
                    ft.Radio(
                        value=cloud_sync.BACKUP_FULL_DB,
                        label="Full scanner.db backup",
                    ),
                    ft.Radio(
                        value=cloud_sync.BACKUP_FILTERED,
                        label="Filtered sessions (selected dates / current filters)",
                    ),
                ],
                tight=True,
            ),
            on_change=lambda e: backup_value.update(value=e.control.value),
        )

        sync_btn = ft.TextButton("Sync Now")
        cancel_btn = ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog())
        sign_out_btn = ft.TextButton("Disconnect")

        def update_sign_out_label():
            provider = provider_value["value"]
            if cloud_sync.is_signed_in(provider):
                label = cloud_sync.PROVIDER_LABELS.get(provider, provider)
                sign_out_btn.text = (
                    "Clear folder"
                    if provider == cloud_sync.PROVIDER_FOLDER
                    else f"Sign Out ({label})"
                )
                sign_out_btn.visible = True
            else:
                sign_out_btn.visible = False
            page.update()

        def on_provider_change(e):
            provider_value["value"] = e.control.value
            update_sign_out_label()

        provider_group.on_change = on_provider_change

        def on_sign_out(_=None):
            cloud_sync.sign_out(provider_value["value"])
            show_snack("Cloud connection cleared.")
            update_sign_out_label()

        sign_out_btn.on_click = on_sign_out

        def run_sync(_=None):
            page.run_task(do_sync)

        async def choose_folder_name_if_needed(checker_tag: str) -> str | None:
            """Return root folder name, or None if the user cancelled."""
            default_name = cloud_sync.cloud_root_folder_name(checker_tag)
            existing = cloud_sync.sync_root_path(
                checker_tag, root_folder_name=default_name
            )
            if existing is None or not existing.exists():
                return default_name

            import asyncio

            decision: dict[str, str | None] = {"value": None}
            done = asyncio.Event()
            name_field = ft.TextField(
                label="Folder name",
                value=f"{default_name} (2)",
                autofocus=True,
            )

            def finish(value: str | None):
                decision["value"] = value
                page.pop_dialog()
                done.set()

            def use_existing(_=None):
                finish(default_name)

            def use_new_name(_=None):
                typed = cloud_sync.sanitize_folder_name(name_field.value or "")
                if not typed:
                    show_snack("Enter a folder name.", error=True)
                    return
                target = cloud_sync.sync_root_path(
                    checker_tag, root_folder_name=typed
                )
                if target is not None and target.exists() and typed != default_name:
                    show_snack(
                        f"Folder '{typed}' already exists. Choose another name.",
                        error=True,
                    )
                    return
                finish(typed)

            def rename_existing(_=None):
                typed = cloud_sync.sanitize_folder_name(name_field.value or "")
                if not typed:
                    show_snack("Enter a new folder name.", error=True)
                    return
                if typed == default_name:
                    show_snack("Enter a different name to rename the folder.", error=True)
                    return
                try:
                    cloud_sync.rename_sync_root(default_name, typed)
                except Exception as exc:
                    show_snack(str(exc), error=True)
                    return
                finish(typed)

            def cancel(_=None):
                finish(None)

            page.show_dialog(
                ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Cloud folder already exists"),
                    content=ft.Column(
                        [
                            muted(
                                f"Folder '{default_name}' already exists. "
                                "Use it, rename it, or sync with a different name."
                            ),
                            name_field,
                        ],
                        tight=True,
                        spacing=12,
                        width=420,
                    ),
                    actions=[
                        ft.TextButton("Cancel", on_click=cancel),
                        ft.TextButton("Rename existing", on_click=rename_existing),
                        ft.TextButton("Use new name", on_click=use_new_name),
                        ft.TextButton("Use existing", on_click=use_existing),
                    ],
                )
            )
            await done.wait()
            return decision["value"]

        async def do_sync():
            import asyncio

            provider = provider_value["value"]
            backup_mode = backup_value["value"]
            sync_btn.disabled = True
            status_text.value = "Preparing files…"
            page.update()

            active = current_filters()
            rows = database.search_sessions(
                sales_order=active["sales_order"],
                date_from=active["date_from"],
                date_to=active["date_to"],
                status=None if active["status"] == "all" else active["status"],
            )
            full_sessions = database.get_sessions_with_items([s["id"] for s in rows])
            if not full_sessions and backup_mode == cloud_sync.BACKUP_FILTERED:
                sync_btn.disabled = False
                status_text.value = "No sessions match the current filters."
                page.update()
                show_snack("No sessions to sync for the current filters.", error=True)
                return

            stamp = datetime.now().strftime("%Y%m%d_%H%M")
            checker_tag = _export_checker_tag(admin_username)
            root_folder_name: str | None = None
            if provider == cloud_sync.PROVIDER_FOLDER:
                status_text.value = "Checking cloud folder…"
                page.update()
                root_folder_name = await choose_folder_name_if_needed(checker_tag)
                if root_folder_name is None:
                    sync_btn.disabled = False
                    status_text.value = "Sync cancelled."
                    page.update()
                    return

            try:
                pdf_bytes = export_report_pdf_bytes(
                    full_sessions or [],
                    filter_summary=filter_summary_text(),
                )
                sessions_payload = []
                for session in full_sessions or []:
                    row = dict(session)
                    ticket = row.get("picking_ticket")
                    if ticket is not None and hasattr(ticket, "__dict__"):
                        from app.pdf_parser import ticket_to_dict

                        row["picking_ticket"] = ticket_to_dict(ticket)
                    sessions_payload.append(row)

                files = cloud_sync.prepare_sync_files(
                    pdf_bytes=pdf_bytes,
                    pdf_name=f"picking_report_{checker_tag}_{stamp}.pdf",
                    backup_mode=backup_mode,
                    filtered_sessions=(
                        sessions_payload
                        if backup_mode == cloud_sync.BACKUP_FILTERED
                        else None
                    ),
                    filter_summary=filter_summary_text(),
                    checker_tag=checker_tag,
                )

                def on_progress(message: str):
                    status_text.value = message
                    page.update()

                result = await asyncio.to_thread(
                    cloud_sync.sync_files,
                    provider,
                    files,
                    on_progress=on_progress,
                    checker_tag=checker_tag,
                    root_folder_name=root_folder_name,
                )
            except Exception as exc:
                sync_btn.disabled = False
                status_text.value = str(exc)
                page.update()
                show_snack(f"Sync failed: {exc}", error=True)
                return

            page.pop_dialog()
            label = cloud_sync.PROVIDER_LABELS.get(result.provider, result.provider)
            names = ", ".join(result.uploaded)
            show_snack(f"Synced to {label}: {names}")

        sync_btn.on_click = run_sync
        update_sign_out_label()

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Sync History to Cloud"),
                content=ft.Column(
                    [
                        muted(
                            "Copies the report PDF and backup into your chosen "
                            "Google Drive / OneDrive folder on this device."
                        ),
                        ft.Text("Destination", weight=ft.FontWeight.W_600, font_family=FONT_FAMILY),
                        provider_group,
                        ft.Text("Backup", weight=ft.FontWeight.W_600, font_family=FONT_FAMILY),
                        backup_group,
                        status_text,
                    ],
                    tight=True,
                    spacing=10,
                    width=420,
                    scroll=ft.ScrollMode.AUTO,
                ),
                actions=[cancel_btn, sign_out_btn, sync_btn],
            )
        )

    def status_chip(label: str, value: str) -> ft.Control:
        if status_value["value"] == value:
            return ft.ElevatedButton(
                label,
                height=MIN_TOUCH,
                on_click=lambda _: set_status(value),
                bgcolor=PRIMARY,
                color=ft.Colors.WHITE,
            )
        return ft.OutlinedButton(label, height=MIN_TOUCH, on_click=lambda _: set_status(value))

    admin_controls: list[ft.Control] = []
    if is_admin:
        admin_controls.extend(
            [
                muted(
                    f"Admin: {admin_username}"
                    + (
                        f" ({auth.ROLE_LABELS.get(admin_role or '', 'Admin')})"
                        if admin_role
                        else ""
                    )
                ),
                ft.TextButton("Logout", on_click=lambda _: (logout_admin(), navigate("history"))),
                ft.OutlinedButton(
                    "Clear All History",
                    icon=ft.Icons.DELETE_FOREVER,
                    on_click=require_admin(clear_all_history),
                ),
            ]
        )
    else:
        admin_controls.append(muted("Admin login required to delete history."))

    toolbar = ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    "Search & Filters",
                    size=16,
                    weight=ft.FontWeight.W_600,
                    font_family=FONT_FAMILY,
                ),
                ft.Row(
                    [
                        _filter_field("Sales Order No.", search_field, 260),
                        _filter_field("Date From", date_from_row, 160),
                        _filter_field("Date To", date_to_row, 160),
                    ],
                    spacing=24,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                ft.Row(
                    [
                        status_chip("All", "all"),
                        status_chip("Completed", "completed"),
                        status_chip("Draft", "draft"),
                        ft.ElevatedButton(
                            "Apply Filters",
                            icon=ft.Icons.SEARCH,
                            height=MIN_TOUCH,
                            bgcolor=PRIMARY,
                            color=ft.Colors.WHITE,
                            on_click=apply_filters,
                        ),
                    ],
                    spacing=12,
                    wrap=True,
                ),
                ft.Divider(height=1, color="#E0E0E0"),
                ft.Row(
                    [
                        ft.OutlinedButton(
                            "Export Report PDF",
                            icon=ft.Icons.PICTURE_AS_PDF,
                            height=MIN_TOUCH,
                            on_click=lambda _: page.run_task(export_filtered_report),
                        ),
                        ft.ElevatedButton(
                            "Sync",
                            icon=ft.Icons.CLOUD_UPLOAD,
                            height=MIN_TOUCH,
                            bgcolor=PRIMARY,
                            color=ft.Colors.WHITE,
                            on_click=open_sync_dialog,
                        ),
                        ft.Container(expand=True),
                        ft.Row(admin_controls, spacing=12, wrap=True),
                    ],
                    spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                muted(filter_summary_text()),
            ],
            spacing=16,
            tight=True,
        ),
        bgcolor=ft.Colors.WHITE,
        border_radius=8,
        padding=20,
    )

    sessions_panel = ft.Container(
        content=ft.Column(
            [
                ft.Text("Sessions", size=16, weight=ft.FontWeight.W_600, font_family=FONT_FAMILY),
                muted("Tap a session to view full details"),
                ft.Container(
                    content=session_column,
                    height=list_height,
                ),
            ],
            spacing=12,
            tight=True,
        ),
        expand=True,
        bgcolor=ft.Colors.WHITE,
        border_radius=8,
        padding=16,
    )

    return ft.Container(
        content=ft.Column(
            [
                section_title("History"),
                muted("Search, filter, and export picking verification sessions"),
                toolbar,
                sessions_panel,
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=16,
        ),
        padding=24,
        expand=True,
        bgcolor=BG_MAIN,
    )


def build_session_detail(
    page: ft.Page,
    navigate,
    show_snack,
    file_picker: ft.FilePicker,
    session_id: int,
    *,
    admin_username: str | None = None,
    admin_role: str | None = None,
    login_admin=None,
    logout_admin=None,
    queue_admin_action=None,
) -> ft.Control:
    is_admin = bool(admin_username)
    session = database.get_session(session_id)
    list_height = _detail_list_height(page)

    require_admin = _admin_helpers(
        page,
        navigate,
        show_snack,
        is_admin=is_admin,
        login_admin=login_admin,
        queue_admin_action=queue_admin_action,
        on_after_login=lambda: navigate("history_detail", session_id=session_id),
    )

    def delete_session(_=None):
        def confirm_delete(_=None):
            database.delete_session(session_id)
            page.pop_dialog()
            show_snack(f"Checking #{session_id} deleted.")
            navigate("history")

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Delete Session"),
                content=ft.Text(f"Delete checking #{session_id}? This cannot be undone."),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                    ft.TextButton("Delete", on_click=confirm_delete),
                ],
            )
        )

    async def export_session(_=None):
        if not session:
            return
        checker_tag = _export_checker_tag(admin_username)
        so = str(session["sales_order_no"]).replace("/", "-")
        default_name = f"session_{session['id']}_{so}_{checker_tag}.pdf"
        try:
            pdf_bytes = export_session_pdf_bytes(session)
            dest = await _save_pdf_export(
                page,
                file_picker,
                dialog_title="Export Session PDF",
                file_name=default_name,
                pdf_bytes=pdf_bytes,
            )
        except Exception as exc:
            show_snack(f"Export failed: {exc}", error=True)
            return
        if not dest:
            return
        show_snack("Session exported to PDF.")

    if not session:
        return ft.Container(
            content=ft.Column(
                [
                    ft.IconButton(
                        icon=ft.Icons.ARROW_BACK,
                        icon_size=28,
                        tooltip="Back to History",
                        on_click=lambda _: navigate("history"),
                    ),
                    muted("Session not found."),
                ],
                spacing=16,
            ),
            padding=24,
            expand=True,
            bgcolor=BG_MAIN,
        )

    detail_body = _build_detail(
        page,
        session,
        navigate,
        is_admin=is_admin,
        on_delete=delete_session,
        on_export=export_session,
        require_admin=require_admin,
        items_list_height=list_height,
    )

    admin_bar: list[ft.Control] = []
    if is_admin:
        admin_bar.append(
            muted(
                f"Admin: {admin_username}"
                + (f" ({auth.ROLE_LABELS.get(admin_role or '', 'Admin')})" if admin_role else "")
            )
        )
        admin_bar.append(
            ft.TextButton("Logout", on_click=lambda _: (logout_admin(), navigate("history_detail", session_id=session_id)))
        )

    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.IconButton(
                            icon=ft.Icons.ARROW_BACK,
                            icon_size=28,
                            tooltip="Back to History",
                            on_click=lambda _: navigate("history"),
                        ),
                        ft.Text(
                            f"Checking #{session_id}",
                            size=22,
                            weight=ft.FontWeight.BOLD,
                            font_family=FONT_FAMILY,
                            expand=True,
                        ),
                        ft.Row(admin_bar, spacing=12),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(
                    content=ft.Column(
                        [detail_body],
                        scroll=ft.ScrollMode.AUTO,
                        expand=True,
                        spacing=0,
                    ),
                    expand=True,
                    bgcolor=ft.Colors.WHITE,
                    border_radius=8,
                    padding=20,
                ),
            ],
            expand=True,
            spacing=16,
        ),
        padding=24,
        expand=True,
        bgcolor=BG_MAIN,
    )


def _ticket_details(session: dict) -> dict[str, str]:
    ticket = session.get("picking_ticket")
    if ticket:
        return {
            "sales_order_no": ticket.order_number,
            "order_date": ticket.order_date or "—",
            "ship_date": ticket.ship_date or "—",
            "ship_to": ticket.ship_to or "—",
            "bill_to": ticket.bill_to or "—",
        }
    return {
        "sales_order_no": session.get("sales_order_no", "—"),
        "order_date": "—",
        "ship_date": "—",
        "ship_to": "—",
        "bill_to": "—",
    }


def _detail_line(label: str, value: str, *, multiline: bool = False) -> ft.Control:
    return ft.Column(
        [
            ft.Text(label, size=12, weight=ft.FontWeight.W_600, font_family=FONT_FAMILY, color=TEXT),
            ft.Text(
                value,
                size=13,
                color=TEXT,
                font_family=FONT_FAMILY,
                selectable=multiline,
            ),
        ],
        spacing=4,
    )


def _build_items_list(grouped_items: list, list_height: int) -> ft.Control:
    LIST_HEADER_HEIGHT = 40
    items_list = ft.ListView(spacing=0, padding=0, expand=True)
    for item in grouped_items:
        items_list.controls.append(
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=8, vertical=10),
                border=ft.Border(bottom=ft.BorderSide(1, "#E0E0E0")),
                content=ft.Row(
                    [
                        ft.Text(
                            item["item_scanned"],
                            width=130,
                            size=11,
                            font_family=FONT_FAMILY,
                            color=TEXT,
                            selectable=True,
                        ),
                        ft.Text(
                            item.get("part_no") or "—",
                            width=80,
                            size=12,
                            font_family=FONT_FAMILY,
                            color=TEXT,
                        ),
                        ft.Text(
                            item.get("description") or "—",
                            expand=True,
                            size=12,
                            font_family=FONT_FAMILY,
                            color=TEXT,
                        ),
                        ft.Text(
                            str(item["qty"]),
                            width=48,
                            size=12,
                            font_family=FONT_FAMILY,
                            color=TEXT,
                        ),
                    ],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
            )
        )

    header = ft.Container(
        height=LIST_HEADER_HEIGHT,
        bgcolor=BG_TABLE,
        border=ft.Border(bottom=ft.BorderSide(1, BORDER)),
        padding=ft.Padding.symmetric(horizontal=8, vertical=8),
        content=ft.Row(
            [
                ft.Text("Item Scanned", width=130, size=12, weight=ft.FontWeight.BOLD, font_family=FONT_FAMILY),
                ft.Text("Item Part No.", width=80, size=12, weight=ft.FontWeight.BOLD, font_family=FONT_FAMILY),
                ft.Text("Description", expand=True, size=12, weight=ft.FontWeight.BOLD, font_family=FONT_FAMILY),
                ft.Text("Qty", width=48, size=12, weight=ft.FontWeight.BOLD, font_family=FONT_FAMILY),
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    return ft.Container(
        border=ft.Border.all(1, BORDER),
        border_radius=4,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        content=ft.Column(
            [
                header,
                ft.Container(
                    height=list_height,
                    content=items_list if grouped_items else ft.Container(
                        content=muted("No scanned items."),
                        padding=12,
                    ),
                ),
            ],
            spacing=0,
            tight=True,
        ),
    )


def _build_detail(
    page: ft.Page,
    session: dict,
    navigate,
    *,
    is_admin: bool,
    on_delete,
    on_export,
    require_admin,
    items_list_height: int = 320,
) -> ft.Control:
    items = session.get("items", [])
    grouped_items = group_scanned_items(items)
    is_draft = session.get("status") == "draft"
    ticket = _ticket_details(session)

    actions = ft.Row(spacing=10, wrap=True)
    if is_draft:
        actions.controls.append(
            ft.ElevatedButton(
                "Resume",
                icon=ft.Icons.PLAY_ARROW,
                bgcolor=PRIMARY,
                color=ft.Colors.WHITE,
                on_click=lambda _: navigate("new_scan", session_id=session["id"], resume=True),
            )
        )
    actions.controls.append(
        ft.OutlinedButton(
            "Export PDF",
            icon=ft.Icons.PICTURE_AS_PDF,
            on_click=lambda _: page.run_task(on_export),
        )
    )
    if is_admin:
        actions.controls.append(
            ft.OutlinedButton(
                "Delete Session",
                icon=ft.Icons.DELETE_OUTLINE,
                on_click=require_admin(on_delete),
            )
        )

    return ft.Column(
        [
            ft.Row(
                [
                    ft.Text(
                        f"Sales Order No: {ticket['sales_order_no']}",
                        size=20,
                        weight=ft.FontWeight.BOLD,
                        font_family=FONT_FAMILY,
                        expand=True,
                    ),
                    ft.Container(
                        content=ft.Text("Draft", size=11, color=ft.Colors.WHITE, font_family=FONT_FAMILY),
                        bgcolor="#FB8C00",
                        border_radius=10,
                        padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                        visible=is_draft,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ft.Row(
                [
                    _detail_line("Sales Order No:", ticket["sales_order_no"]),
                    _detail_line("Order Date:", ticket["order_date"]),
                    _detail_line("Ship Date:", ticket["ship_date"]),
                ],
                spacing=24,
                wrap=True,
            ),
            ft.Row(
                [
                    ft.Container(
                        expand=True,
                        content=_detail_line("Ship To", ticket["ship_to"], multiline=True),
                    ),
                    ft.Container(
                        expand=True,
                        content=_detail_line("Bill To", ticket["bill_to"], multiline=True),
                    ),
                ],
                spacing=24,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            muted(
                f"Picker: {session['picker_name']} · Checker: {session['checker_name']} · "
                f"Checked: {format_check_when(session)} · Boxes: {session.get('no_of_boxes') or '—'}"
            ),
            muted(f"{len(grouped_items)} product(s) · {len(items)} scan(s)"),
            ft.Row(
                [
                    _status_badge("Picking Correct", bool(session.get("picking_correct"))),
                    _status_badge("Item Correct", bool(session.get("item_correct"))),
                ],
                spacing=12,
            ),
            actions,
            _build_items_list(grouped_items, items_list_height),
        ],
        spacing=12,
    )


def _status_badge(label: str, correct: bool) -> ft.Container:
    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(
                    ft.Icons.CHECK_CIRCLE if correct else ft.Icons.CANCEL,
                    color=ft.Colors.WHITE,
                    size=16,
                ),
                ft.Text(label, size=12, color=ft.Colors.WHITE, font_family=FONT_FAMILY),
            ],
            spacing=6,
        ),
        bgcolor="#43A047" if correct else "#E53935",
        border_radius=12,
        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
    )
