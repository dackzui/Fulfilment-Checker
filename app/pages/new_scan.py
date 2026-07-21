"""New scan page — main barcode scanning workflow."""

from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path

import flet as ft

from app import auth
from app import barcode_catalog
from app import database
from app.picker_helper import attach_pick_bays_to_ticket, sort_ticket_items
from app.item_grouping import box_qty_display
from app.components import (
    action_button,
    capitalize_person_name,
    labeled_field,
    muted,
    person_name_dropdown,
    section_title,
    text_input,
)
from app.pdf_parser import (
    PickingTicket,
    PickingTicketItem,
    apply_scans_to_ticket_items,
    find_ticket_item,
    parse_picking_ticket,
    ticket_from_dict,
    total_qty_ordered_for_part,
)
from app.theme import BG_MAIN, BG_TABLE, BORDER, FONT_FAMILY, MIN_TOUCH, PRIMARY, TEXT, TEXT_MUTED
from app.verification import compute_verification

_SCAN_KEY_CHARS = {
    **{f"Digit {d}": str(d) for d in range(10)},
    **{f"Numpad {d}": str(d) for d in range(10)},
}
_ENTER_KEYS = {"Enter", "Numpad Enter"}
_IGNORED_SCAN_KEYS = {
    "Arrow Up",
    "Arrow Down",
    "Arrow Left",
    "Arrow Right",
    "Tab",
    "Shift",
    "Control",
    "Alt",
    "Meta",
    "Caps Lock",
    "Escape",
    "Delete",
    "Home",
    "End",
    "Page Up",
    "Page Down",
}


def _scan_key_char(key: str) -> str | None:
    if len(key) == 1:
        return key
    return _SCAN_KEY_CHARS.get(key)


def build(
    page: ft.Page,
    navigate,
    show_snack,
    file_picker: ft.FilePicker,
    resume_session_id: int | None = None,
    *,
    scan_focus: dict | None = None,
    logged_in_username: str | None = None,
    logged_in_role: str | None = None,
    login_admin=None,
    get_logged_in=None,
) -> ft.Control:
    manual_entry_active = False
    scan_buffer: list[str] = []
    last_scan_key_time = 0.0
    keyboard_listener: ft.KeyboardListener | None = None
    today = date.today().strftime("%d/%m/%Y")
    scanned_items: list[dict] = []
    picking_ticket: PickingTicket | None = None
    last_ticket_pdf: bytes | None = None
    pick_sort_method = {"value": "model_i"}
    draft_session_id: int | None = resume_session_id
    window_height = getattr(getattr(page, "window", None), "height", None) or page.height or 800
    ITEMS_PANEL_HEIGHT = max(320, min(460, int(window_height) - 460))
    ITEMS_LIST_HEIGHT = ITEMS_PANEL_HEIGHT - 40

    def format_person_name_field(field: ft.TextField):
        if field.value:
            field.value = capitalize_person_name(field.value)
            page.update()

    picker = person_name_dropdown(
        hint="Select or enter picker name",
        options=database.list_picker_names(),
    )
    checker = text_input(hint="Enter checker name", expand=False)

    def refresh_picker_options(*, keep_value: bool = True):
        current = capitalize_person_name(picker.value or "") if keep_value else ""
        names = list(database.list_picker_names())
        if current and current not in names:
            names.append(current)
            names.sort(key=str.casefold)
        picker.options = [
            ft.dropdown.Option(key=name, text=name) for name in names
        ]
        if current:
            picker.value = current
        if picker.page is not None:
            picker.update()

    def remember_picker_from_field():
        name = capitalize_person_name(picker.value or "").strip()
        if not name:
            return
        database.remember_picker_name(name)
        refresh_picker_options(keep_value=True)

    def on_picker_blur(_=None):
        if picker.value:
            picker.value = capitalize_person_name(picker.value)
        remember_picker_from_field()

    def current_session() -> tuple[str | None, str | None]:
        if get_logged_in:
            username, role = get_logged_in()
            if username:
                return username, role
        if logged_in_username:
            return logged_in_username, logged_in_role
        page_user = getattr(page, "_session_username", None)
        page_role = getattr(page, "_session_role", None)
        if page_user:
            return page_user, page_role
        return None, None

    def session_is_logged_in() -> bool:
        username, _ = current_session()
        return bool(username)

    manage_pickers_btn = ft.IconButton(
        icon=ft.Icons.MANAGE_ACCOUNTS,
        icon_color=PRIMARY,
        tooltip="Manage picker names",
        visible=False,
        on_click=None,
    )

    def update_picker_manage_visibility():
        _, role = current_session()
        manage_pickers_btn.visible = auth.can_manage_picker_names(role)
        if manage_pickers_btn.page is not None:
            manage_pickers_btn.update()

    def open_manage_pickers_dialog(_=None):
        _, role = current_session()
        if not auth.can_manage_picker_names(role):
            show_snack("Checker or Admin login required to manage picker names.", error=True)
            return

        picker_rows = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=280)

        def render_picker_rows():
            picker_rows.controls.clear()
            names = database.list_picker_names()
            if not names:
                picker_rows.controls.append(muted("No saved picker names yet."))
                return
            for name in names:
                picker_rows.controls.append(
                    ft.ListTile(
                        title=ft.Text(name, font_family=FONT_FAMILY),
                        trailing=ft.IconButton(
                            icon=ft.Icons.DELETE_OUTLINE,
                            icon_color="#E53935",
                            tooltip="Remove picker name",
                            on_click=lambda _, picker_name=name: confirm_delete_picker(picker_name),
                        ),
                    )
                )

        def confirm_delete_picker(picker_name: str):
            def close_confirm(_=None):
                page.pop_dialog()

            def submit_delete(_=None):
                database.delete_picker_name(picker_name)
                page.pop_dialog()
                render_picker_rows()
                refresh_picker_options()
                page.update()
                show_snack(f"Removed picker name — {picker_name}.")

            page.show_dialog(
                ft.AlertDialog(
                    modal=True,
                    title=ft.Text("Remove Picker Name"),
                    content=ft.Text(
                        f"Remove '{picker_name}' from the picker list?",
                        font_family=FONT_FAMILY,
                    ),
                    actions=[
                        ft.TextButton("Cancel", on_click=close_confirm),
                        ft.TextButton("Remove", on_click=submit_delete),
                    ],
                )
            )

        def close_dialog(_=None):
            page.pop_dialog()

        new_picker_field = ft.TextField(
            label="Add picker name",
            autofocus=True,
            on_submit=lambda _: add_picker(),
        )

        def add_picker(_=None):
            name = capitalize_person_name(new_picker_field.value or "").strip()
            if not name:
                show_snack("Enter a picker name.", error=True)
                return
            database.remember_picker_name(name)
            new_picker_field.value = ""
            render_picker_rows()
            refresh_picker_options(keep_value=True)
            page.update()
            show_snack(f"Added picker — {name}")

        render_picker_rows()
        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Manage Picker Names"),
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Container(content=new_picker_field, expand=True),
                                ft.IconButton(
                                    icon=ft.Icons.ADD,
                                    icon_color=PRIMARY,
                                    tooltip="Add picker name",
                                    on_click=add_picker,
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.END,
                        ),
                        muted("Add new names or remove ones no longer needed."),
                        picker_rows,
                    ],
                    tight=True,
                    spacing=12,
                    width=360,
                ),
                actions=[ft.TextButton("Close", on_click=close_dialog)],
            )
        )

    manage_pickers_btn.on_click = open_manage_pickers_dialog

    def apply_logged_in_checker():
        username, role = current_session()
        if role == auth.ROLE_CHECKER and username:
            checker.value = capitalize_person_name(username)

    login_dialog_open = False

    def open_checker_login_dialog(*, on_success=None):
        nonlocal login_dialog_open
        if session_is_logged_in() or not login_admin or login_dialog_open:
            return
        login_dialog_open = True

        username_field = ft.TextField(label="Username", autofocus=True)
        password_field = ft.TextField(
            label="Password",
            password=True,
            can_reveal_password=True,
        )

        def close_dialog(_=None):
            nonlocal login_dialog_open
            login_dialog_open = False
            page.pop_dialog()

        def submit_login(_=None):
            name = (username_field.value or "").strip()
            password = password_field.value or ""
            account = auth.authenticate(name, password)
            if account is None:
                show_snack("Invalid username or password.", error=True)
                return
            if login_admin(name, password):
                close_dialog()
                apply_logged_in_checker()
                update_picker_manage_visibility()
                page.update()
                show_snack(f"Signed in — {account.username} ({account.role_label})")
                if on_success:
                    on_success()

        password_field.on_submit = submit_login

        page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text("Sign In"),
                content=ft.Column(
                    [
                        ft.Text(
                            "Sign in once to start scanning. You stay signed in until you "
                            "close the app or log out from Home.",
                            size=13,
                            font_family=FONT_FAMILY,
                        ),
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

    apply_logged_in_checker()
    check_date = text_input(value=today, read_only=True, expand=False)
    check_date.width = 140
    check_time = text_input(value="", hint="On save", read_only=True, expand=False)
    check_time.width = 110
    sales_order = text_input(hint="Enter sales order number", expand=False)
    no_of_boxes = text_input(hint="Enter number of boxes", expand=False)
    qty_field = text_input(value="1", expand=False)
    qty_field.width = 90
    barcode_field = text_input(hint="Scan Barcode Here", autofocus=True, expand=False)
    manual_part_field = text_input(hint="Item Part No. (no barcode)", expand=False)
    manual_part_field.width = 280

    pdf_label = muted("No picking ticket loaded")

    picking_status = ft.Icon(ft.Icons.RADIO_BUTTON_UNCHECKED, color="#9E9E9E", size=28)
    item_status = ft.Icon(ft.Icons.RADIO_BUTTON_UNCHECKED, color="#9E9E9E", size=28)

    def _set_summary_icon(target: ft.Icon, state: str) -> None:
        icons = {
            "ok": (ft.Icons.CHECK_CIRCLE, "#43A047"),
            "partial": (ft.Icons.TIMELAPSE, "#FB8C00"),
            "warning": (ft.Icons.WARNING_AMBER, "#FB8C00"),
            "error": (ft.Icons.CANCEL, "#E53935"),
            "empty": (ft.Icons.RADIO_BUTTON_UNCHECKED, "#9E9E9E"),
        }
        icon_name, color = icons.get(state, icons["empty"])
        target.icon = icon_name
        target.color = color

    def _item_correct_state(item_ok: bool) -> str:
        if not scanned_items:
            return "empty"
        if item_ok:
            return "ok"
        statuses = {scan.get("match_status") for scan in scanned_items}
        if statuses & {"unknown", "not_on_ticket"}:
            return "error"
        if "qty_exceeded" in statuses:
            return "warning"
        return "error"

    def _picking_correct_state(picking_ok: bool, item_ok: bool) -> str:
        if not picking_ticket or not picking_ticket.items:
            return "empty"
        if picking_ok:
            return "ok"
        if not scanned_items or all(item.qty_scanned == 0 for item in picking_ticket.items):
            return "empty"
        if not item_ok:
            return "error"
        if any(item.is_partial or item.is_complete for item in picking_ticket.items):
            return "partial"
        return "empty"

    LIST_HEADER_HEIGHT = 40

    def _equal_header(label: str) -> ft.Container:
        return ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            content=ft.Text(
                label,
                size=12,
                weight=ft.FontWeight.BOLD,
                font_family=FONT_FAMILY,
                text_align=ft.TextAlign.CENTER,
            ),
        )

    ordered_list = ft.ListView(spacing=0, padding=0, expand=True)
    ordered_header = ft.Container(
        height=LIST_HEADER_HEIGHT,
        bgcolor=BG_TABLE,
        border=ft.Border(bottom=ft.BorderSide(1, BORDER)),
        padding=ft.Padding.symmetric(horizontal=6, vertical=8),
        content=ft.Row(
            [
                _equal_header("Pick Bay"),
                _equal_header("Part"),
                _equal_header("Qty"),
                _equal_header("Scanned"),
                _equal_header("Picked"),
            ],
            spacing=6,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    scanned_list = ft.ListView(spacing=0, padding=0, expand=True)
    scanned_header = ft.Container(
        height=LIST_HEADER_HEIGHT,
        bgcolor=BG_TABLE,
        border=ft.Border(bottom=ft.BorderSide(1, BORDER)),
        padding=ft.Padding.symmetric(horizontal=6, vertical=8),
        content=ft.Row(
            [
                _equal_header("Scanned"),
                _equal_header("Part"),
                _equal_header("Qty"),
                _equal_header("OK"),
                _equal_header(""),
            ],
            spacing=6,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    def _short_label(value: str, limit: int = 14) -> str:
        text = (value or "").strip()
        if len(text) <= limit:
            return text
        return f"{text[: limit - 1]}…"

    def stamp_check_datetime():
        now = datetime.now()
        check_date.value = now.strftime("%d/%m/%Y")
        check_time.value = now.strftime("%I:%M %p")

    def focus_field(field: ft.TextField):
        page.run_task(field.focus)

    async def focus_barcode_field():
        import asyncio

        await asyncio.sleep(0.1)
        if keyboard_listener is not None:
            await keyboard_listener.focus()
        else:
            await barcode_field.focus()

    def focus_scan_field():
        page.run_task(focus_barcode_field)

    def set_manual_entry(active: bool):
        nonlocal manual_entry_active
        manual_entry_active = active

    def wire_manual_entry_field(field: ft.TextField, *, on_blur_extra=None):
        def on_focus(_=None):
            set_manual_entry(True)

        def on_blur(_=None):
            if on_blur_extra:
                on_blur_extra(_)
            set_manual_entry(False)
            focus_scan_field()

        field.on_focus = on_focus
        field.on_blur = on_blur

    wire_manual_entry_field(
        picker,
        on_blur_extra=on_picker_blur,
    )
    wire_manual_entry_field(
        checker,
        on_blur_extra=lambda _: format_person_name_field(checker),
    )
    wire_manual_entry_field(sales_order)
    wire_manual_entry_field(no_of_boxes)
    wire_manual_entry_field(manual_part_field)

    def on_barcode_focus(_=None):
        set_manual_entry(False)

    barcode_field.on_focus = on_barcode_focus

    def refresh_verification_status():
        if picking_ticket:
            apply_scans_to_ticket_items(picking_ticket.items, scanned_items)
        picking_ok, item_ok = compute_verification(picking_ticket, scanned_items)
        _set_summary_icon(item_status, _item_correct_state(item_ok))
        _set_summary_icon(picking_status, _picking_correct_state(picking_ok, item_ok))

    def resolve_scan(code: str, qty: int) -> dict:
        lookup = barcode_catalog.lookup_barcode(code)
        if not lookup:
            catalog = barcode_catalog.lookup_part_no(code)
            if catalog:
                lookup = catalog
        if not lookup:
            return {
                "part_no": code,
                "description": "Unknown barcode",
                "match_status": "unknown",
                "qty_ok": False,
            }

        part_no = lookup["part_no"]
        description = lookup["description"]

        if not picking_ticket:
            return {
                "part_no": part_no,
                "description": description,
                "match_status": "catalog_only",
                "qty_ok": True,
            }

        ticket_item = find_ticket_item(picking_ticket.items, part_no)
        if not ticket_item:
            return {
                "part_no": part_no,
                "description": description,
                "match_status": "not_on_ticket",
                "qty_ok": False,
            }

        already_scanned = barcode_catalog.scanned_qty_for_part(scanned_items, part_no)
        total_ordered = total_qty_ordered_for_part(picking_ticket.items, part_no)
        qty_ok = already_scanned + qty <= total_ordered
        return {
            "part_no": part_no,
            "description": description,
            "match_status": "on_ticket" if qty_ok else "qty_exceeded",
            "qty_ok": qty_ok,
        }

    def resolve_manual_part(part_no: str, qty: int) -> dict:
        part_no = part_no.strip()
        if picking_ticket:
            ticket_item = find_ticket_item(picking_ticket.items, part_no)
            if ticket_item:
                already_scanned = barcode_catalog.scanned_qty_for_part(
                    scanned_items, part_no
                )
                total_ordered = total_qty_ordered_for_part(
                    picking_ticket.items, part_no
                )
                qty_ok = already_scanned + qty <= total_ordered
                return {
                    "part_no": ticket_item.part_no,
                    "description": ticket_item.description,
                    "match_status": "on_ticket" if qty_ok else "qty_exceeded",
                    "qty_ok": qty_ok,
                }

            catalog = barcode_catalog.lookup_part_no(part_no)
            return {
                "part_no": catalog["part_no"] if catalog else part_no,
                "description": (
                    catalog["description"]
                    if catalog
                    else "Not on picking ticket"
                ),
                "match_status": "not_on_ticket",
                "qty_ok": False,
            }

        catalog = barcode_catalog.lookup_part_no(part_no)
        if catalog:
            return {
                "part_no": catalog["part_no"],
                "description": catalog["description"],
                "match_status": "catalog_only",
                "qty_ok": True,
            }

        return {
            "part_no": part_no,
            "description": "No barcode — manual entry",
            "match_status": "manual_no_barcode",
            "qty_ok": True,
        }

    ordered_placeholder = muted("Upload a picking ticket PDF to see ordered items.")

    def _items_list_shell(content: ft.Control, *, height: int | None = None, expand: bool = False) -> ft.Container:
        container = ft.Container(
            height=height,
            expand=expand,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            border=ft.Border.all(1, BORDER),
            border_radius=4,
            content=content,
        )
        return container

    ordered_list_area = _items_list_shell(
        ft.Container(
            content=ordered_placeholder,
            alignment=ft.Alignment(0, 0),
            padding=12,
        ),
        height=ITEMS_LIST_HEIGHT,
        expand=True,
    )

    def _pick_bay_for_part(part_no: str) -> str:
        if not picking_ticket:
            return ""
        ticket_item = find_ticket_item(picking_ticket.items, part_no)
        return (ticket_item.pick_bay if ticket_item else "") or ""

    def refresh_expected_table():
        if not picking_ticket:
            ordered_list.controls = []
            ordered_list_area.content = ft.Container(
                content=ordered_placeholder,
                alignment=ft.Alignment(0, 0),
                padding=12,
            )
            return

        def _ordered_cell(value: str, *, picked: bool) -> ft.Container:
            return ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                content=ft.Text(
                    value,
                    size=12,
                    font_family=FONT_FAMILY,
                    text_align=ft.TextAlign.CENTER,
                    color=TEXT_MUTED if picked else TEXT,
                    style=ft.TextStyle(
                        decoration=(
                            ft.TextDecoration.LINE_THROUGH
                            if picked
                            else ft.TextDecoration.NONE
                        ),
                    ),
                ),
            )

        def _make_ordered_row(item: PickingTicketItem) -> ft.Container:
            picked = bool(item.picked)
            return ft.Container(
                padding=ft.Padding.symmetric(horizontal=6, vertical=8),
                border=ft.Border(bottom=ft.BorderSide(1, BORDER)),
                bgcolor="#E8F5E9" if picked else None,
                ink=True,
                on_click=lambda _, part=item.part_no: toggle_item_picked(part),
                content=ft.Row(
                    [
                        _ordered_cell(item.pick_bay or "—", picked=picked),
                        _ordered_cell(
                            _short_label(item.part_no, 18),
                            picked=picked,
                        ),
                        _ordered_cell(str(item.qty_ordered), picked=picked),
                        _ordered_cell(str(item.qty_scanned), picked=picked),
                        ft.Container(
                            expand=True,
                            alignment=ft.Alignment.CENTER,
                            content=_status_icon(item),
                        ),
                    ],
                    spacing=6,
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )

        ordered_list.controls = [
            _make_ordered_row(item) for item in picking_ticket.items
        ]
        ordered_list_area.content = ft.Column(
            [
                ordered_header,
                ft.Container(
                    height=ITEMS_LIST_HEIGHT - LIST_HEADER_HEIGHT,
                    content=ordered_list,
                ),
            ],
            spacing=0,
            tight=True,
        )
        page.update()

    def toggle_item_picked(part_no: str):
        if not picking_ticket:
            return
        ticket_item = find_ticket_item(picking_ticket.items, part_no)
        if not ticket_item:
            return
        ticket_item.picked = not ticket_item.picked
        refresh_expected_table()

    def _status_icon(item: PickingTicketItem) -> ft.Icon:
        if item.picked:
            return ft.Icon(ft.Icons.CHECK_CIRCLE, color="#43A047", size=22)
        return ft.Icon(ft.Icons.RADIO_BUTTON_UNCHECKED, color="#9E9E9E", size=22)

    def _match_icon(item: dict) -> ft.Icon:
        status = item.get("match_status", "")
        if status == "on_ticket":
            return ft.Icon(ft.Icons.CHECK_CIRCLE, color="#43A047", size=22)
        if status == "qty_exceeded":
            return ft.Icon(ft.Icons.WARNING_AMBER, color="#FB8C00", size=22)
        if status in {"catalog_only", "no_ticket", "manual_no_barcode"}:
            return ft.Icon(ft.Icons.INFO_OUTLINE, color="#1E88E5", size=22)
        return ft.Icon(ft.Icons.CANCEL, color="#E53935", size=22)

    def _qty_label(item: dict) -> ft.Text:
        if item.get("manual"):
            return ft.Text(
                f"{item['qty']} (Manual)",
                font_family=FONT_FAMILY,
                color="#6A1B9A",
            )
        box_display = box_qty_display(item)
        if box_display:
            return ft.Text(
                box_display,
                font_family=FONT_FAMILY,
                color="#1565C0",
                size=11,
            )
        if item.get("pallet_qty"):
            return ft.Text(f"{item['qty']} (Pallet)", font_family=FONT_FAMILY, color="#2E7D32")
        return ft.Text(str(item["qty"]), font_family=FONT_FAMILY)

    def refresh_table():
        scanned_list.controls = [
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=6, vertical=8),
                border=ft.Border(bottom=ft.BorderSide(1, BORDER)),
                content=ft.Row(
                    [
                        ft.Container(
                            expand=True,
                            alignment=ft.Alignment.CENTER,
                            content=ft.Text(
                                _short_label(item["item_scanned"], 18),
                                size=12,
                                font_family=FONT_FAMILY,
                                text_align=ft.TextAlign.CENTER,
                            ),
                        ),
                        ft.Container(
                            expand=True,
                            alignment=ft.Alignment.CENTER,
                            content=ft.Text(
                                _short_label(item["part_no"], 18),
                                size=12,
                                font_family=FONT_FAMILY,
                                text_align=ft.TextAlign.CENTER,
                            ),
                        ),
                        ft.Container(
                            expand=True,
                            alignment=ft.Alignment.CENTER,
                            content=_qty_label(item),
                        ),
                        ft.Container(
                            expand=True,
                            alignment=ft.Alignment.CENTER,
                            content=_match_icon(item),
                        ),
                        ft.Container(
                            expand=True,
                            alignment=ft.Alignment.CENTER,
                            content=ft.IconButton(
                                icon=ft.Icons.DELETE_OUTLINE,
                                icon_color="#E53935",
                                icon_size=20,
                                tooltip="Remove",
                                on_click=lambda _, idx=i: remove_item(idx),
                            ),
                        ),
                    ],
                    spacing=6,
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
            for i, item in enumerate(scanned_items)
        ]
        refresh_expected_table()
        refresh_verification_status()
        page.update()

    def sync_ticket_quantities():
        if not picking_ticket:
            return
        apply_scans_to_ticket_items(picking_ticket.items, scanned_items)

    def remove_item(index: int):
        scanned_items.pop(index)
        sync_ticket_quantities()
        refresh_table()

    def process_barcode(code: str):
        code = barcode_catalog.normalize_scanned_code(code)
        if not code:
            return

        try:
            manual_qty = max(1, int((qty_field.value or "1").strip()))
        except ValueError:
            show_snack("Qty must be a whole number.", error=True)
            return

        qty, qty_source = barcode_catalog.scan_qty_for_barcode(code, manual_qty)

        result = resolve_scan(code, qty)
        part_no = result["part_no"]
        description = result["description"]
        match_status = result["match_status"]

        if match_status == "unknown":
            show_snack(
                "Barcode not found in Barcode Master List. "
                "For parts with no barcode, use Item Part No. "
                f"({barcode_catalog.catalog_count():,} barcodes loaded).",
                error=True,
            )
            return
        elif match_status == "not_on_ticket":
            show_snack(f"Part '{part_no}' is not on the picking ticket.", error=True)
        elif match_status == "qty_exceeded":
            total_ordered = (
                total_qty_ordered_for_part(picking_ticket.items, part_no)
                if picking_ticket
                else "?"
            )
            show_snack(
                f"Qty exceeds order for part '{part_no}' (ordered {total_ordered} total).",
                error=True,
            )
        elif qty_source == "box":
            per_box = qty // manual_qty if manual_qty else qty
            if manual_qty > 1:
                show_snack(f"Scanned {part_no} — {manual_qty} box(es) × {per_box} = {qty}")
            else:
                show_snack(f"Scanned {part_no} — Box Qty: {qty}")
        elif manual_qty > 1:
            show_snack(f"Scanned {part_no} — {manual_qty} item(s)")
        elif match_status == "on_ticket":
            show_snack(f"Scanned {part_no} — qty {qty}")

        lookup = barcode_catalog.lookup_barcode(code)
        box_unit = (
            int(lookup["box_qty"])
            if qty_source == "box" and lookup and lookup.get("box_qty")
            else None
        )
        scanned_items.append(
            {
                "item_scanned": code,
                "part_no": part_no,
                "description": description,
                "qty": qty,
                "box_qty": box_unit,
                "pack_count": manual_qty if qty_source == "box" else None,
                "pallet_qty": None,
                "match_status": match_status,
                "qty_ok": result["qty_ok"],
            }
        )
        sync_ticket_quantities()
        barcode_field.value = ""
        qty_field.value = "1"
        refresh_table()
        focus_scan_field()

    def add_barcode(_=None):
        process_barcode(barcode_field.value or "")

    def handle_scan_key_down(e: ft.KeyDownEvent):
        nonlocal last_scan_key_time

        if manual_entry_active:
            return

        if e.key in _ENTER_KEYS:
            if scan_buffer:
                code = "".join(scan_buffer)
                scan_buffer.clear()
                process_barcode(code)
            return

        if e.key == "Backspace":
            if scan_buffer:
                scan_buffer.pop()
            return

        if e.key in _IGNORED_SCAN_KEYS:
            return

        char = _scan_key_char(e.key)
        if not char:
            return

        now = time.monotonic()
        if now - last_scan_key_time > 0.12:
            scan_buffer.clear()
        scan_buffer.append(char)
        last_scan_key_time = now

    barcode_field.on_submit = add_barcode

    def add_manual_item(_=None):
        part_no = (manual_part_field.value or "").strip()
        if not part_no:
            show_snack("Enter an Item Part No.", error=True)
            focus_field(manual_part_field)
            return

        try:
            qty = max(1, int((qty_field.value or "1").strip()))
        except ValueError:
            show_snack("Qty must be a whole number.", error=True)
            return

        result = resolve_manual_part(part_no, qty)
        part_no = result["part_no"]
        description = result["description"]
        match_status = result["match_status"]

        if match_status == "not_on_ticket":
            show_snack(
                f"Part '{part_no}' is not on the picking ticket. "
                "Check the part number or upload the correct PDF.",
                error=True,
            )
            return
        if match_status == "qty_exceeded":
            total_ordered = (
                total_qty_ordered_for_part(picking_ticket.items, part_no)
                if picking_ticket
                else "?"
            )
            show_snack(
                f"Qty exceeds order for part '{part_no}' (ordered {total_ordered} total).",
                error=True,
            )
            return

        if match_status == "manual_no_barcode":
            show_snack(f"Added {part_no} — qty {qty} (manual, no barcode).")
        else:
            show_snack(f"Added {part_no} — qty {qty} (manual).")

        scanned_items.append(
            {
                "item_scanned": "Manual",
                "part_no": part_no,
                "description": description,
                "qty": qty,
                "box_qty": None,
                "pallet_qty": None,
                "manual": True,
                "match_status": match_status,
                "qty_ok": result["qty_ok"],
            }
        )
        sync_ticket_quantities()
        manual_part_field.value = ""
        qty_field.value = "1"
        refresh_table()
        focus_scan_field()

    manual_part_field.on_submit = add_manual_item

    def apply_pick_sort(method: str | None = None):
        if not picking_ticket:
            return
        method = method or pick_sort_method["value"]
        if last_ticket_pdf:
            attach_pick_bays_to_ticket(
                picking_ticket,
                last_ticket_pdf,
                method=method,  # type: ignore[arg-type]
            )
        else:
            sort_ticket_items(picking_ticket, method=method)  # type: ignore[arg-type]
        refresh_expected_table()
        page.update()

    def on_pick_sort_change(e):
        pick_sort_method["value"] = e.control.value
        apply_pick_sort(e.control.value)

    pick_sort_group = ft.RadioGroup(
        value=pick_sort_method["value"],
        content=ft.Row(
            [
                ft.Radio(value="model_i", label="Model I"),
                ft.Radio(value="model_ii", label="Model II"),
                ft.Radio(value="ascending", label="Asc"),
            ],
            spacing=4,
            tight=True,
        ),
        on_change=on_pick_sort_change,
    )

    def apply_picking_ticket(ticket: PickingTicket):
        nonlocal picking_ticket
        picking_ticket = ticket
        sales_order.value = ticket.order_number
        sales_order.read_only = True
        pdf_label.value = f"Loaded: {ticket.source_file or 'picking ticket.pdf'} ({len(ticket.items)} items)"
        sync_ticket_quantities()
        refresh_expected_table()
        refresh_table()
        show_snack(f"Picking ticket loaded — order {ticket.order_number}.")

    async def handle_pdf_pick(_=None):
        files = await file_picker.pick_files(
            dialog_title="Select Picking Ticket PDF",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["pdf"],
            allow_multiple=False,
            with_data=True,
        )
        if not files:
            return

        selected = files[0]
        try:
            if selected.bytes:
                pdf_data = selected.bytes
            elif selected.path:
                pdf_data = Path(selected.path).read_bytes()
            else:
                show_snack("Could not read the selected PDF file.", error=True)
                return
            ticket = parse_picking_ticket(pdf_data, source_file=selected.name)
            nonlocal last_ticket_pdf
            last_ticket_pdf = pdf_data
            attach_pick_bays_to_ticket(
                ticket,
                pdf_data,
                method=pick_sort_method["value"],  # type: ignore[arg-type]
            )
            apply_picking_ticket(ticket)
        except ValueError as exc:
            show_snack(str(exc), error=True)
        except OSError:
            show_snack("Could not read the selected PDF file.", error=True)
        except Exception as exc:
            show_snack(f"Failed to parse the picking ticket PDF: {exc}", error=True)

    def clear_ticket():
        nonlocal picking_ticket, last_ticket_pdf
        picking_ticket = None
        last_ticket_pdf = None
        sales_order.read_only = False
        pdf_label.value = "No picking ticket loaded"
        refresh_expected_table()

    def _normalize_person_names():
        picker.value = capitalize_person_name(picker.value)
        checker.value = capitalize_person_name(checker.value)
        remember_picker_from_field()

    def _validate_basic_fields() -> bool:
        if not session_is_logged_in():
            open_checker_login_dialog()
            return False
        _normalize_person_names()
        if not picker.value or not picker.value.strip():
            show_snack("Picker Name is required.", error=True)
            focus_field(picker)
            return False
        if not checker.value or not checker.value.strip():
            show_snack("Checker Name is required.", error=True)
            focus_field(checker)
            return False
        if not sales_order.value or not sales_order.value.strip():
            show_snack("Sales Order No. is required.", error=True)
            focus_field(sales_order)
            return False
        if not scanned_items:
            show_snack("Scan or manually add at least one item before saving.", error=True)
            focus_scan_field()
            return False
        return True

    def _validate_ticket_complete() -> bool:
        if not picking_ticket:
            return True
        bad_scans = [
            s for s in scanned_items
            if s.get("match_status") in {"unknown", "not_on_ticket", "qty_exceeded"}
        ]
        if bad_scans:
            show_snack(
                f"{len(bad_scans)} scan(s) do not match the picking ticket or qty ordered.",
                error=True,
            )
            return False
        incomplete = [i for i in picking_ticket.items if not i.is_complete]
        if incomplete:
            show_snack(
                f"{len(incomplete)} item(s) not fully scanned vs. the picking ticket.",
                error=True,
            )
            return False
        return True

    def save_draft(_=None):
        nonlocal draft_session_id
        if not _validate_basic_fields():
            return

        stamp_check_datetime()
        draft_session_id = database.save_session(
            picker_name=picker.value,
            checker_name=checker.value,
            check_date=check_date.value or today,
            check_time=check_time.value or "",
            sales_order_no=sales_order.value,
            no_of_boxes=no_of_boxes.value or "",
            items=scanned_items,
            picking_ticket=picking_ticket,
            session_id=draft_session_id,
            status="draft",
        )
        refresh_picker_options()
        show_snack(f"Saved — resume later to enter No of Boxes (checking #{draft_session_id}).")

    def reset_form():
        nonlocal draft_session_id, picking_ticket
        scanned_items.clear()
        draft_session_id = None
        clear_ticket()
        picker.value = ""
        checker.value = ""
        apply_logged_in_checker()
        check_date.value = today
        check_time.value = ""
        sales_order.value = ""
        no_of_boxes.value = ""
        qty_field.value = "1"
        barcode_field.value = ""
        manual_part_field.value = ""
        refresh_table()
        focus_scan_field()
        show_snack("Scan cancelled.")

    def complete_scan(_=None):
        if not _validate_basic_fields():
            return
        if not no_of_boxes.value or not no_of_boxes.value.strip():
            show_snack("No of Boxes is required to complete.", error=True)
            focus_field(no_of_boxes)
            return
        if not _validate_ticket_complete():
            return

        stamp_check_datetime()
        session_id = database.save_session(
            picker_name=picker.value,
            checker_name=checker.value,
            check_date=check_date.value or today,
            check_time=check_time.value or "",
            sales_order_no=sales_order.value,
            no_of_boxes=no_of_boxes.value or "",
            items=scanned_items,
            picking_ticket=picking_ticket,
            session_id=draft_session_id,
            status="completed",
        )
        show_snack(f"Scan completed — checking #{session_id} saved.")
        navigate("history", session_id=session_id)

    upload_button = ft.ElevatedButton(
        "Upload Picking Ticket",
        icon=ft.Icons.UPLOAD_FILE,
        bgcolor=PRIMARY,
        color=ft.Colors.WHITE,
        height=MIN_TOUCH,
        on_click=lambda _: page.run_task(handle_pdf_pick),
    )

    add_item_button = ft.ElevatedButton(
        "Add Item",
        icon=ft.Icons.ADD,
        bgcolor="#6A1B9A",
        color=ft.Colors.WHITE,
        height=MIN_TOUCH,
        on_click=add_manual_item,
    )

    header = ft.Row(
        [
            section_title("Picking Barcode Scanner"),
            ft.Row(
                [
                    action_button("Cancel", ft.Icons.CLOSE, primary=False, on_click=lambda _: reset_form()),
                    action_button(
                        "Save",
                        ft.Icons.SAVE,
                        bgcolor="#FB8C00",
                        hover="#F57C00",
                        on_click=save_draft,
                    ),
                    action_button("Complete", ft.Icons.CHECK, on_click=complete_scan),
                ],
                spacing=10,
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    form = ft.Column(
        [
            ft.Row(
                [
                    ft.Container(
                        expand=True,
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(
                                            "Picker Name",
                                            size=13,
                                            weight=ft.FontWeight.W_600,
                                            color=TEXT,
                                            font_family=FONT_FAMILY,
                                        ),
                                        manage_pickers_btn,
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                picker,
                            ],
                            spacing=6,
                            tight=True,
                        ),
                    ),
                    labeled_field("Checker Name", checker, expand=True),
                ],
                spacing=16,
            ),
            ft.Row(
                [
                    ft.Container(
                        expand=True,
                        content=ft.Row(
                            [
                                labeled_field("Check Date", check_date, expand=True),
                                labeled_field("Check Time", check_time, expand=True),
                            ],
                            spacing=12,
                        ),
                    ),
                    labeled_field("Sales Order No.", sales_order, expand=True),
                ],
                spacing=16,
            ),
            ft.Row(
                [
                    ft.Container(
                        expand=True,
                        content=ft.Column(
                            [
                                ft.Text(
                                    "Picking Ticket PDF",
                                    size=13,
                                    weight=ft.FontWeight.W_600,
                                    color=TEXT,
                                    font_family=FONT_FAMILY,
                                ),
                                ft.Row(
                                    [upload_button, pdf_label],
                                    spacing=12,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    wrap=True,
                                ),
                            ],
                            spacing=6,
                            tight=True,
                        ),
                    ),
                    labeled_field("No of Boxes", no_of_boxes, expand=True),
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            ft.Column(
                [
                    ft.Text(
                        "Qty & Scan Barcode",
                        size=13,
                        weight=ft.FontWeight.W_600,
                        color=TEXT,
                        font_family=FONT_FAMILY,
                    ),
                    ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Text("Qty", size=12, color=TEXT, font_family=FONT_FAMILY),
                                    qty_field,
                                ],
                                spacing=4,
                                tight=True,
                            ),
                            ft.Container(
                                expand=True,
                                content=ft.Column(
                                    [
                                        ft.Text(
                                            "Scan Barcode",
                                            size=12,
                                            color=TEXT,
                                            font_family=FONT_FAMILY,
                                        ),
                                        barcode_field,
                                    ],
                                    spacing=4,
                                    tight=True,
                                ),
                            ),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                ],
                spacing=6,
                tight=True,
            ),
            ft.Column(
                [
                    ft.Text(
                        "Add Item Without Barcode",
                        size=13,
                        weight=ft.FontWeight.W_600,
                        color=TEXT,
                        font_family=FONT_FAMILY,
                    ),
                    ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Text(
                                        "Item Part No.",
                                        size=12,
                                        color=TEXT,
                                        font_family=FONT_FAMILY,
                                    ),
                                    manual_part_field,
                                ],
                                spacing=4,
                                tight=True,
                            ),
                            add_item_button,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    muted("Uses the Qty field above. Part must match the picking ticket when a PDF is loaded."),
                ],
                spacing=6,
                tight=True,
            ),
            ft.Row(
                [
                    ft.Row(
                        [
                            ft.Text("Picking Correct", size=13, weight=ft.FontWeight.W_600, font_family=FONT_FAMILY),
                            picking_status,
                        ],
                        spacing=12,
                    ),
                    ft.Row(
                        [
                            ft.Text("Item Correct", size=13, weight=ft.FontWeight.W_600, font_family=FONT_FAMILY),
                            item_status,
                        ],
                        spacing=12,
                    ),
                ],
                spacing=32,
            ),
        ],
        spacing=16,
    )

    expected_container = ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            "Items Ordered",
                            size=14,
                            weight=ft.FontWeight.W_600,
                            font_family=FONT_FAMILY,
                        ),
                        ft.Container(expand=True),
                        muted("Pick order:"),
                        pick_sort_group,
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ordered_list_area,
            ],
            spacing=8,
            expand=True,
        ),
        bgcolor=BG_TABLE,
        border_radius=6,
        padding=12,
        expand=True,
        height=ITEMS_PANEL_HEIGHT,
    )

    scanned_list_area = _items_list_shell(
        ft.Column(
            [
                scanned_header,
                ft.Container(
                    height=ITEMS_LIST_HEIGHT - LIST_HEADER_HEIGHT,
                    content=scanned_list,
                    expand=True,
                ),
            ],
            spacing=0,
            expand=True,
        ),
        height=ITEMS_LIST_HEIGHT,
        expand=True,
    )

    table_container = ft.Container(
        content=ft.Column(
            [
                ft.Text("Scanned Items", size=14, weight=ft.FontWeight.W_600, font_family=FONT_FAMILY),
                scanned_list_area,
            ],
            spacing=8,
            expand=True,
        ),
        bgcolor=BG_TABLE,
        border_radius=6,
        padding=12,
        expand=True,
        height=ITEMS_PANEL_HEIGHT,
    )

    items_row = ft.Row(
        [expected_container, table_container],
        spacing=16,
        height=ITEMS_PANEL_HEIGHT,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )

    def load_draft(session_id: int):
        nonlocal picking_ticket, draft_session_id
        session = database.get_session(session_id)
        if not session or session.get("status") != "draft":
            return

        draft_session_id = session_id
        picker.value = capitalize_person_name(session["picker_name"])
        refresh_picker_options(keep_value=True)
        checker.value = capitalize_person_name(session["checker_name"])
        check_date.value = session["check_date"]
        check_time.value = session.get("check_time") or ""
        sales_order.value = session["sales_order_no"]
        sales_order.read_only = True
        no_of_boxes.value = session.get("no_of_boxes") or ""

        if session.get("picking_ticket"):
            picking_ticket = session["picking_ticket"]
            pdf_label.value = (
                f"Loaded: {picking_ticket.source_file or 'picking ticket.pdf'} "
                f"({len(picking_ticket.items)} items)"
            )

        scanned_items.clear()
        for row in session.get("items", []):
            scanned_items.append(
                {
                    "item_scanned": row.get("item_scanned", ""),
                    "part_no": row.get("part_no", ""),
                    "description": row.get("description", ""),
                    "qty": int(row.get("qty", 1)),
                    "box_qty": row.get("box_qty"),
                    "pallet_qty": row.get("pallet_qty"),
                    "manual": bool(row.get("manual"))
                    or (row.get("item_scanned") or "").strip().lower() == "manual",
                    "match_status": row.get("match_status") or "catalog_only",
                    "qty_ok": row.get("match_status") == "on_ticket",
                }
            )
        sync_ticket_quantities()
        refresh_table()
        show_snack(f"Resumed saved checking #{session_id}.")

    def post_build():
        if resume_session_id:
            load_draft(resume_session_id)
        else:
            refresh_verification_status()
        update_picker_manage_visibility()
        if not session_is_logged_in():
            open_checker_login_dialog()
        focus_scan_field()

    if scan_focus is not None:
        scan_focus["post_build"] = post_build

    page_content = ft.Container(
        content=ft.Column(
            [
                header,
                ft.Divider(height=16, color=ft.Colors.TRANSPARENT),
                form,
                ft.Divider(height=8, color=ft.Colors.TRANSPARENT),
                items_row,
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        padding=24,
        expand=True,
        bgcolor=BG_MAIN,
    )

    keyboard_listener = ft.KeyboardListener(
        content=page_content,
        autofocus=True,
        on_key_down=handle_scan_key_down,
    )

    return keyboard_listener
