"""Super Admin desktop monitor — top pickers graph with crown and prize message."""

from __future__ import annotations

import time
from datetime import date

import flet as ft

from app import auth
from app import database
from app import firebase_presence
from app.components import muted
from app.paths import init_app_storage, logo_src
from app.theme import BG_MAIN, FONT_FAMILY, PRIMARY, TEXT

_REFRESH_SECONDS = 15
_BAR_COLORS = ("#1E88E5", "#43A047", "#FB8C00", "#8E24AA", "#00897B", "#C62828")
_CROWN = "👑"


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
) -> list[tuple[str, int]]:
    ranked: list[tuple[str, int]] = []
    for row in rows:
        count = row.last_week if which == "last" else row.week
        if count > 0:
            ranked.append((row.picker_name, int(count)))
    ranked.sort(key=lambda item: (-item[1], item[0].lower()))
    return ranked


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
                {"picker_name": name, "today": 0, "devices": []},
            )
            row["today"] = int(row["today"]) + qty
            devices = row["devices"]
            assert isinstance(devices, list)
            if entry.device_label and entry.device_label not in devices:
                devices.append(entry.device_label)
    rows = list(by_name.values())
    rows.sort(
        key=lambda item: (-int(item["today"]), str(item["picker_name"]).lower())
    )
    return rows


def _monitor_bar_chart(rows: list[tuple[str, int]]) -> ft.Control:
    if not rows:
        return muted("No pickups recorded for this period yet.")

    max_count = max(count for _, count in rows) or 1
    bars: list[ft.Control] = []
    for index, (name, count) in enumerate(rows):
        is_top = index == 0
        width_frac = max(0.08, count / max_count)
        color = "#F9A825" if is_top else _BAR_COLORS[index % len(_BAR_COLORS)]
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
                str(count),
                size=18 if is_top else 15,
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
                        ft.Container(
                            content=ft.Container(
                                bgcolor=color,
                                border_radius=6,
                                height=28 if is_top else 20,
                            ),
                            bgcolor="#ECEFF1",
                            border_radius=6,
                            height=28 if is_top else 20,
                            width=float(720 * width_frac),
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

    page.title = "DEKS Top Pickers Monitor"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(font_family=FONT_FAMILY)
    page.bgcolor = BG_MAIN
    page.padding = 0
    page.window.width = 1100
    page.window.height = 800
    page.window.min_width = 900
    page.window.min_height = 640

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
        range_label = muted("Week: —")
        prize_banner = ft.Container(visible=False)
        chart_host = ft.Column(spacing=12, tight=True)
        online_pickers_list = ft.Column(spacing=8, tight=True)
        top_label = ft.Text("", size=20, weight=ft.FontWeight.W_600, font_family=FONT_FAMILY)
        week_dropdown = ft.Dropdown(
            label="Week filter",
            width=220,
            value="this",
            visible=can_edit,
            disabled=not can_edit,
            options=[
                ft.DropdownOption(key="this", text="This week"),
                ft.DropdownOption(key="last", text="Last week"),
            ],
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
            label="Week filter",
            width=220,
            value="this",
            options=[
                ft.DropdownOption(key="this", text="This week"),
                ft.DropdownOption(key="last", text="Last week"),
            ],
        )
        settings_status = muted("")

        filter_state = {"value": "this", "prize": ""}
        refresh_token = time.time()
        page._monitor_token = refresh_token

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
                                        muted(f"Today: {row['today']} pickup(s){device_bit}"),
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

        def apply_snapshot(snap: dict) -> None:
            which = str(snap.get("week_filter") or filter_state["value"] or "this")
            prize = str(snap.get("prize_message") or "")
            filter_state["value"] = which
            filter_state["prize"] = prize
            if week_dropdown.value != which:
                week_dropdown.value = which
            if settings_week_dropdown.value != which:
                settings_week_dropdown.value = which
            if prize_field.value != prize:
                prize_field.value = prize

            range_text = _format_iso_range(
                str(snap.get("week_start") or ""),
                str(snap.get("week_end") or ""),
            )
            label = "Last week" if which == "last" else "This week"
            range_label.value = f"{label}: {range_text}" if range_text else label

            ranked = _ranked_pickers(list(snap.get("fulfilments") or []), which)
            chart_host.controls = [_monitor_bar_chart(ranked[:12])]
            if ranked:
                top_name, top_count = ranked[0]
                top_label.value = f"{_CROWN}  #1 {top_name}  —  {top_count} pickups"
                render_prize_banner(prize, top_name)
            else:
                top_label.value = "No pickups yet for this period"
                render_prize_banner(prize, None)

            render_online_pickers(list(snap.get("presence") or []))

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
                            firebase_presence.publish_heartbeat(
                                username=admin_name,
                                role=admin_role,
                                online=True,
                            )
                        except Exception:
                            pass
                    snap = firebase_presence.dashboard_snapshot()

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

        def save_week_filter(chosen: str):
            if not can_edit:
                show_snack("View-only accounts cannot change settings.", error=True)
                week_dropdown.value = filter_state["value"]
                settings_week_dropdown.value = filter_state["value"]
                page.update()
                return
            chosen = "last" if chosen == "last" else "this"

            def work():
                try:
                    firebase_presence.save_dashboard_settings(
                        week_filter=chosen,
                        updated_by=admin_name,
                    )
                    filter_state["value"] = chosen
                    snap = firebase_presence.dashboard_snapshot()

                    def apply():
                        apply_snapshot(snap)
                        show_snack("Week filter updated.")
                        page.update()

                    apply()
                except Exception as exc:
                    show_snack(f"Could not save week filter: {exc}", error=True)

            page.run_thread(work)

        def on_week_change(e):
            save_week_filter((e.control.value or "this").strip().lower())

        def on_settings_week_change(e):
            save_week_filter((e.control.value or "this").strip().lower())

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

        week_dropdown.on_change = on_week_change
        settings_week_dropdown.on_change = on_settings_week_change

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
                                    "Week filter",
                                    weight=ft.FontWeight.W_600,
                                    font_family=FONT_FAMILY,
                                ),
                                muted("Controls the leaderboard period on the board and Home."),
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

        def auto_loop():
            while getattr(page, "_monitor_token", None) == refresh_token:
                try:
                    if firebase_presence.is_configured():
                        try:
                            firebase_presence.publish_heartbeat(
                                username=admin_name,
                                role=admin_role,
                                online=True,
                            )
                        except Exception:
                            pass
                    snap = firebase_presence.dashboard_snapshot()

                    def apply():
                        if getattr(page, "_monitor_token", None) != refresh_token:
                            return
                        apply_snapshot(snap)
                        page.update()

                    apply()
                except Exception as exc:

                    def show_err(message=str(exc)):
                        if getattr(page, "_monitor_token", None) != refresh_token:
                            return
                        status_label.value = f"Refresh failed: {message}"
                        page.update()

                    show_err()
                for _ in range(_REFRESH_SECONDS):
                    if getattr(page, "_monitor_token", None) != refresh_token:
                        return
                    time.sleep(1)

        page.run_thread(auto_loop)

        header_actions = [
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
                                    "Live ranking for monitoring — refreshes automatically."
                                    + (
                                        ""
                                        if can_edit
                                        else " View-only access."
                                    )
                                ),
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
